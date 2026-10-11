"""Local JSON-line IPC engine. SQLite + small derivatives; never copies originals.

Catalog IDs use folder UUID / relative path, so drive letters can be relinked.
DeepFace is lazy and opt-in; only one CPU face worker runs, paused for viewing.
"""
from __future__ import annotations
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import re
import shutil
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid
from folder_cache import FolderCache
from memories import Memories
from capture_dates import CaptureDates
from categories import Categories
from face_matching import banks, diverse, match, unit, learned_limits
from request_lanes import RequestLanes
from people_organizer import PeopleOrganizer
from decoded_views import DecodedViews
from image_codec import open_image,read_gps

VERSION = 'thumb-v4-720-heif123'
KINDS = {**dict.fromkeys('jpg jpeg jpe jfif png apng webp gif heic heif avif tif tiff bmp dib ico icns psd jp2 j2k jpf jpx ppm pgm pbm pnm tga pcx qoi sgi rgb rgba svg dds'.split(), 'image'),
         **dict.fromkeys('dng cr2 cr3 crw nef nrw arw srf sr2 raf orf rw2 raw pef srw kdc dcr mos mrw 3fr fff iiq rwl x3f'.split(), 'raw'),
         **dict.fromkeys('mp4 mov m4v avi mkv webm 3gp'.split(), 'video'),
         **dict.fromkeys('m4a mp3 wav flac aac ogg'.split(), 'audio'), 'pdf': 'document'}
MODEL = 'SFace'
FACE_VERSION = 'sface-yunet-1600-v2'
PROTOCOL_OUT = sys.stdout

class Engine(FolderCache):
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / 'vela-library.json'
        if marker.exists():
            manifest = json.loads(marker.read_text('utf-8'))
            if manifest.get('format') != 'vela-portable' or manifest.get('version') != 1:
                raise ValueError('This folder has an incompatible Vela library format.')
            if not (self.root / 'catalog.sqlite').is_file():
                raise ValueError('The saved library is missing catalog.sqlite. Restore the complete data folder.')
        else:
            if any(self.root.iterdir()):
                raise ValueError('Choose an empty folder for a new library, or a folder containing vela-library.json.')
            marker.write_text(json.dumps({'format': 'vela-portable', 'version': 1, 'id': str(uuid.uuid4())}), 'utf-8')
        self.database = self.root / 'catalog.sqlite'
        # A packaged build can include public weights for offline first use.
        seed = Path(__file__).resolve().parent.parent / 'models' / '.deepface' / 'weights'
        if seed.is_dir():
            target = self.root / 'models' / '.deepface' / 'weights'; target.mkdir(parents=True, exist_ok=True)
            for name in ['face_recognition_sface_2021dec.onnx','haarcascade_eye.xml','haarcascade_frontalface_default.xml','face_detection_yunet_2023mar.onnx']:
                if (seed / name).is_file() and not (target / name).exists(): shutil.copyfile(seed / name,target / name)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.enabled = False
        self.paused = False
        self.closing = False
        self.priority = []
        self.organization_pending = 0
        self.decoded_views = DecodedViews()
        os.environ['DEEPFACE_BACKEND_ENGINE'] = 'onnx'
        os.environ['DEEPFACE_HOME'] = str(self.root / 'models')
        self.status = {'state': 'idle', 'current': '', 'done': 0, 'total': 0, 'failed': 0, 'error': ''}
        with self.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS folders(id TEXT PRIMARY KEY, name TEXT, root TEXT, relative_root TEXT, added INTEGER);
            CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY, folder_id TEXT REFERENCES folders(id), path TEXT, bytes INTEGER, modified INTEGER, kind TEXT, extension TEXT, favorite INTEGER DEFAULT 0, present INTEGER DEFAULT 1, fingerprint TEXT, face_version TEXT, face_error TEXT);
            CREATE INDEX IF NOT EXISTS media_folder ON media(folder_id);
            CREATE TABLE IF NOT EXISTS previews(media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE, version TEXT, file TEXT, bytes INTEGER, accessed INTEGER, duration TEXT);
            CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY, name TEXT DEFAULT '', embedding TEXT);
            CREATE TABLE IF NOT EXISTS person_exemplars(person_id TEXT REFERENCES people(id) ON DELETE CASCADE, embedding TEXT, PRIMARY KEY(person_id,embedding));
            CREATE TABLE IF NOT EXISTS person_negatives(person_id TEXT REFERENCES people(id) ON DELETE CASCADE, embedding TEXT, PRIMARY KEY(person_id,embedding));
            CREATE TABLE IF NOT EXISTS person_separate(first TEXT,second TEXT,PRIMARY KEY(first,second));
            CREATE TABLE IF NOT EXISTS person_alias(old_id TEXT PRIMARY KEY,person_id TEXT);
            CREATE TABLE IF NOT EXISTS rejected_faces(media_id TEXT REFERENCES media(id) ON DELETE CASCADE, fingerprint TEXT, box TEXT, PRIMARY KEY(media_id,fingerprint,box));
            CREATE TABLE IF NOT EXISTS locations(media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE, version TEXT, latitude REAL, longitude REAL, error TEXT);
            CREATE TABLE IF NOT EXISTS folder_caches(folder_id TEXT PRIMARY KEY REFERENCES folders(id), path TEXT, relative_cache TEXT);
            CREATE TABLE IF NOT EXISTS faces(id TEXT PRIMARY KEY, media_id TEXT REFERENCES media(id) ON DELETE CASCADE, person_id TEXT REFERENCES people(id), embedding TEXT, box TEXT, file TEXT);
            CREATE TABLE IF NOT EXISTS face_assignments(media_id TEXT REFERENCES media(id) ON DELETE CASCADE,fingerprint TEXT,box TEXT,person_id TEXT REFERENCES people(id) ON DELETE CASCADE,PRIMARY KEY(media_id,fingerprint,box));
            CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id);
            CREATE INDEX IF NOT EXISTS faces_media ON faces(media_id);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            ''')
            self.enabled = self.setting(db, 'facesEnabled', 'false') == 'true'
            self.all_paused = self.setting(db, 'allPaused', 'false') == 'true'
        self.people_organizer = PeopleOrganizer(self)
        self.location_status = {'state':'idle','current':''}
        self.location_worker = threading.Thread(target=self.location_loop, daemon=True)
        self.location_worker.start()
        self.worker = threading.Thread(target=self.face_loop, daemon=True)
        self.worker.start()
        self.init_folder_cache()
        self.scan_state = {'state':'idle','done':0,'current':''}
        self.scanner = None
        with self.connection() as db: unfinished = self.setting(db,'diskScan','')
        if unfinished: self.start_scan(json.loads(unfinished))
        self.capture_dates = CaptureDates(self)
        self.categories = Categories(self,VERSION)
        self.memories = Memories(self)
        self.people_organizer.start_feedback()
        self.people_organizer.start(background=True)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def setting(db, key, default):
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def resolve_root(self, folder):
        # Portable relative root first; original machine's absolute path second.
        relative = (self.root / folder['relative_root']).resolve() if folder['relative_root'] else None
        if relative and relative.is_dir():
            return relative
        return Path(folder['root'])

    def original(self, db, media_id):
        row = db.execute('SELECT m.*, f.root, f.relative_root FROM media m JOIN folders f ON f.id=m.folder_id WHERE m.id=? AND m.present=1', (media_id,)).fetchone()
        if not row:
            raise FileNotFoundError('The original is no longer in this catalog. Rescan its folder.')
        root = self.resolve_root(row).resolve()
        source = (root / row['path']).resolve()
        if not source.is_relative_to(root):
            raise ValueError('Invalid catalog path outside its source folder.')
        if not source.is_file():
            raise FileNotFoundError('Original folder is offline or moved. Relink it in Folders; previews and names are retained.')
        stat = source.stat()
        if stat.st_size != row['bytes']:
            raise ValueError('The original has changed. Rescan its folder to update this catalog entry.')
        if stat.st_mtime_ns // 1000000 != row['modified']:
            with source.open('rb') as stream:
                head=stream.read(16384);stream.seek(max(0,stat.st_size-16384));tail=stream.read(16384)
            if hashlib.blake2b(head+tail,digest_size=16).hexdigest() != row['fingerprint']:
                raise ValueError('The original has changed. Rescan its folder to update this catalog entry.')
        return source

    def load(self, args):
        with self.connection() as db:
            folders = []
            for f in db.execute('SELECT * FROM folders ORDER BY added'):
                cache = db.execute('SELECT path FROM folder_caches WHERE folder_id=?',(f['id'],)).fetchone()
                try: cache_path = str(self.cache_root(db,f['id'])) if cache else None
                except (OSError,ValueError): cache_path = cache['path'] if cache else None
                folders.append(dict(id=f['id'], name=f['name'], location=str(self.resolve_root(f)), cacheLocation=cache_path, addedAt=f['added'], mode='desktop', count=db.execute('SELECT count(*) FROM media WHERE folder_id=? AND present=1', (f['id'],)).fetchone()[0], online=self.resolve_root(f).is_dir()))
            items = []
            for m in db.execute('SELECT m.*, p.version preview_version, p.duration, c.captured_at, c.source date_source,cat.categories,cat.source category_source FROM media m LEFT JOIN previews p ON p.media_id=m.id LEFT JOIN capture_dates c ON c.media_id=m.id AND c.version=m.fingerprint LEFT JOIN media_categories cat ON cat.media_id=m.id AND cat.version=m.fingerprint WHERE m.present=1 ORDER BY m.folder_id,m.path'):
                version = f"{m['bytes']}:{m['modified']}:{VERSION}"
                ready = m['preview_version'] == version
                size = m['bytes']; units = f'{max(1, round(size / 1024))} KB' if size < 1048576 else f'{size / 1048576:.1f} MB' if size < 1073741824 else f'{size / 1073741824:.1f} GB'
                captured=m['captured_at']
                items.append(dict(id=m['id'], folderId=m['folder_id'], relativePath=m['path'], name=Path(m['path']).name, byteSize=size, modified=m['modified'], version=version, kind=m['kind'], extension=m['extension'], favorite=bool(m['favorite']), size=units, capturedAt=captured, captureDate=captured[:10] if captured else None, categories=json.loads(m['categories'] or '[]'), categorySource=m['category_source'], dateSource=m['date_source'] or 'unknown', date=captured[:10] if captured else 'Unknown capture date', accent='linear-gradient(135deg,#c6d8f6,#718ec5)', processing='ready' if ready else 'queued' if m['kind'] in ['image', 'raw', 'video'] else 'unsupported', thumbnailReady=ready, duration=m['duration']))
            return {'folders': folders, 'items': items}

    def scan(self, args):
        root = Path(args['path']).resolve()
        if not root.is_dir():
            raise ValueError('The selected original folder is unavailable.')
        if root == self.root:
            raise ValueError('Select your original media folder, not the Vela library folder.')
        with self.connection() as db:
            folder_id = args.get('folderId')
            if folder_id and not db.execute('SELECT id FROM folders WHERE id=?', (folder_id,)).fetchone():
                raise ValueError('Unknown folder to relink.')
            if not folder_id:
                for f in db.execute('SELECT * FROM folders'):
                    if os.path.normcase(str(self.resolve_root(f))) == os.path.normcase(str(root)):
                        folder_id = f['id']; break
                    if root.is_relative_to(self.resolve_root(f).resolve()): raise ValueError(f'This folder is already included in {f["name"]}. Use its existing library entry instead of indexing duplicate originals.')
            folder_id = folder_id or str(uuid.uuid4())
            try: relative = os.path.relpath(root, self.root)
            except ValueError: relative = ''
            db.execute('INSERT INTO folders VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET root=excluded.root,relative_root=excluded.relative_root', (folder_id, root.name, str(root), relative, int(time.time()*1000)))
            other_roots = {self.resolve_root(f).resolve() for f in db.execute('SELECT * FROM folders WHERE id!=?',(folder_id,))}
            present = set()
            errors = []
            paused = False
            def walk_error(error): errors.append(str(error))
            for parent, dirs, files in os.walk(root, onerror=walk_error, followlinks=False):
                if args.get('background') and (self.all_paused or self.closing): paused=True; errors.append('Disk scan paused; saved entries are retained.'); break
                dirs[:] = [d for d in dirs if not Path(parent,d).is_symlink() and Path(parent,d).resolve() not in other_roots and Path(parent,d).resolve() not in [self.root,Path(__file__).resolve().parent.parent] and not (Path(parent,d)/'vela-folder-cache.json').is_file() and not (Path(parent,d)/'vela-library.json').is_file() and not (Path(parent)==root and d.lower() in ['windows','program files','program files (x86)','$recycle.bin','system volume information'])]
                for name in files:
                    if args.get('background') and (self.all_paused or self.closing): paused=True; errors.append('Disk scan paused; saved entries are retained.'); break
                    source = Path(parent, name)
                    ext = source.suffix[1:].lower()
                    if ext not in KINDS or source.is_symlink(): continue
                    try:
                        stat = source.stat(); path = source.relative_to(root).as_posix()
                        media_id = f'{folder_id}/{path}'; modified = stat.st_mtime_ns // 1000000
                        old = db.execute('SELECT * FROM media WHERE id=?', (media_id,)).fetchone()
                        # Timestamp-only changes after a transfer need not discard faces or previews.
                        same = old and old['bytes'] == stat.st_size and old['modified'] == modified
                        if not same:
                            with source.open('rb') as stream:
                                head = stream.read(16384); stream.seek(max(0, stat.st_size-16384)); tail = stream.read(16384)
                            digest = hashlib.blake2b(head+tail, digest_size=16).hexdigest()
                            if old and old['bytes'] == stat.st_size and old['fingerprint'] == digest:
                                modified = old['modified']; same = True
                        else: digest = old['fingerprint']
                        if old and not same:
                            db.execute('DELETE FROM previews WHERE media_id=?', (media_id,)); db.execute('DELETE FROM faces WHERE media_id=?', (media_id,))
                        db.execute('''INSERT INTO media(id,folder_id,path,bytes,modified,kind,extension,fingerprint) VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET bytes=excluded.bytes,modified=excluded.modified,present=1,fingerprint=excluded.fingerprint,face_version=CASE WHEN ? THEN face_version ELSE NULL END,face_error=CASE WHEN ? THEN face_error ELSE NULL END''', (media_id, folder_id, path, stat.st_size, modified, KINDS[ext], ext.upper(), digest, bool(same), bool(same)))
                        present.add(media_id)
                        if args.get('background'):
                            self.scan_state.update(done=len(present),current=path)
                            if len(present)%250==0: db.commit()
                    except (OSError, ValueError) as error: errors.append(f'{source}: {error}')
                if paused: break
            # Only this root is reconciled. An interrupted/incomplete scan must not mark files missing.
            if not errors:
                for m in db.execute('SELECT id FROM media WHERE folder_id=?', (folder_id,)):
                    if m['id'] not in present: db.execute('UPDATE media SET present=0 WHERE id=?', (m['id'],))
            self.wake.set()
            return {'folderId': folder_id, 'count': len(present), 'warnings': errors[:8], 'paused':paused}

    def start_scan(self, args):
        if self.scanner and self.scanner.is_alive(): raise ValueError('A disk scan is already running. Stop/resume it or wait before adding another disk.')
        args = dict(args)
        source = Path(args['path']).resolve()
        if not source.is_dir(): raise ValueError('The selected original folder is unavailable.')
        if source == self.root: raise ValueError('Select your original media folder, not the library/cache folder.')
        if args.get('cachePath'):
            cache = self.validate_cache(source,args['cachePath'])
            if cache is None: args.pop('cachePath')
            else: args['cachePath']=str(cache)
        with self.connection() as db: db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('diskScan',json.dumps(args)))
        self.scan_state = {'state':'paused' if self.all_paused else 'scanning','done':0,'current':args['path']}
        def run():
            try:
                while not self.closing:
                    if self.all_paused:
                        self.scan_state['state']='paused'; time.sleep(.25); continue
                    self.scan_state['state']='scanning'
                    result = self.mount_cache(args['path'],args['cachePath'],background=True) if args.get('cachePath') else self.scan({**args,'background':True})
                    self.cache_dirty=True
                    if result['paused']: continue
                    self.scan_state.update(state='complete',done=result['count'],current='',error=' · '.join(result['warnings']))
                    with self.connection() as db: db.execute('DELETE FROM settings WHERE key="diskScan"')
                    break
            except Exception as error:
                self.scan_state.update(state='error',error=str(error))
                with self.connection() as db: db.execute('DELETE FROM settings WHERE key="diskScan"')
        self.scanner=threading.Thread(target=run,daemon=True);self.scanner.start()
        return {'backgroundScan':True}

    def preview_get(self, args):
        if args.get('type') != 'thumbnail': return None
        with self.connection() as db:
            row = db.execute('SELECT * FROM previews WHERE media_id=? AND version=?', (args['id'], args['version'])).fetchone()
            if not row: return None
            try: file = self.resource_path(db,row['file'])
            except (OSError,ValueError): return None
            if not file.is_file():
                db.execute('DELETE FROM previews WHERE media_id=?', (args['id'],)); return None
            db.execute('UPDATE previews SET accessed=? WHERE media_id=?', (int(time.time()*1000), args['id']))
            return {'data': base64.b64encode(file.read_bytes()).decode(), 'duration': row['duration']}

    def preview_save(self, args):
        data = base64.b64decode(args['data'], validate=True)
        if len(data) > 2 * 1024 * 1024: raise ValueError('Only small thumbnails may be saved in the portable cache.')
        with self.lock, self.connection() as db:
            row = db.execute('SELECT * FROM media WHERE id=?', (args['id'],)).fetchone()
            if not row or args['version'] != f"{row['bytes']}:{row['modified']}:{VERSION}": return False
            name = self.derivative_name(db,row['folder_id'],f"thumbnails/{hashlib.sha256((args['id']+'@'+args['version']).encode()).hexdigest()}.jpg")
            file = self.resource_path(db,name); file.parent.mkdir(parents=True,exist_ok=True)
            temporary = file.with_suffix('.tmp'); temporary.write_bytes(data); temporary.replace(file)
            db.execute('INSERT OR REPLACE INTO previews VALUES(?,?,?,?,?,?)', (args['id'], args['version'], name, len(data), int(time.time()*1000), args.get('duration')))
        self.trim()
        return True

    def trim(self):
        with self.lock, self.connection() as db:
            cap = int(self.setting(db, 'cacheLimit', str(512*1024*1024)))
            total = 0
            for row in db.execute('SELECT * FROM previews ORDER BY accessed DESC'):
                total += row['bytes']
                if total > cap:
                    try: self.resource_path(db,row['file']).unlink(missing_ok=True)
                    except (OSError,ValueError): continue
                    db.execute('DELETE FROM previews WHERE media_id=?', (row['media_id'],))
            referenced = {p['file'] for p in db.execute('SELECT file FROM previews')} | {f['file'] for f in db.execute('SELECT file FROM faces')}
            roots = [(self.root,'')]
            for row in db.execute('SELECT folder_id FROM folder_caches'):
                try: roots.append((self.cache_root(db,row['folder_id']),f'@{row["folder_id"]}/'))
                except (OSError,ValueError): continue
            for base,prefix in roots:
                for directory in ['thumbnails','faces']:
                    for file in (base / directory).glob('*.jpg'):
                        # Clean only our generated derivative names, never unrelated files.
                        generated = re.fullmatch(r'[a-f0-9]{64}\.jpg' if directory=='thumbnails' else r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\.jpg',file.name)
                        if generated and f'{prefix}{directory}/{file.name}' not in referenced:
                            try: file.unlink(missing_ok=True)
                            except OSError: pass

    def people(self, args):
        with self.connection() as db:
            result = []
            for person in db.execute('SELECT * FROM people ORDER BY name,id'):
                faces = list(db.execute('SELECT f.id,f.media_id FROM faces f JOIN media m ON m.id=f.media_id WHERE person_id=? AND m.present=1', (person['id'],)))
                if faces:
                    media = list(dict.fromkeys(f['media_id'] for f in faces))
                    aliases=[row[0] for row in db.execute('SELECT old_id FROM person_alias WHERE person_id=?',(person['id'],))]
                    result.append({'id': person['id'], 'name': person['name'], 'count': len(media), 'faceId': faces[0]['id'], 'mediaIds': media,'aliases':aliases,'isMe':self.setting(db,'myPerson','')==person['id']})
            return result

    @staticmethod
    def overlaps(first, second):
        x = max(first['x'],second['x']); y = max(first['y'],second['y'])
        right = min(first['x']+first['w'],second['x']+second['w']); bottom = min(first['y']+first['h'],second['y']+second['h'])
        intersection = max(0,right-x)*max(0,bottom-y)
        return intersection / max(1,first['w']*first['h']+second['w']*second['h']-intersection)

    def location_loop(self):
        from PIL import Image
        while not self.closing:
            if self.all_paused or self.paused:
                self.location_status['state'] = 'paused'; self.wake.wait(1); continue
            with self.connection() as db:
                row = db.execute('SELECT m.* FROM media m LEFT JOIN locations l ON l.media_id=m.id WHERE m.present=1 AND m.kind="image" AND (l.version IS NULL OR l.version!=m.fingerprint) LIMIT 1').fetchone()
            if not row:
                self.location_status.update(state='complete',current=''); self.wake.wait(2); continue
            self.location_status.update(state='processing',current=Path(row['path']).name)
            latitude = longitude = None; error = None
            try:
                with self.connection() as db: source = self.original(db,row['id'])
                latitude,longitude=read_gps(source)
            except Exception as reason: error = str(reason)
            with self.lock, self.connection() as db:
                fresh = db.execute('SELECT fingerprint FROM media WHERE id=?',(row['id'],)).fetchone()
                if fresh and fresh['fingerprint']==row['fingerprint']:
                    db.execute('INSERT OR REPLACE INTO locations VALUES(?,?,?,?,?)',(row['id'],row['fingerprint'],latitude,longitude,error))
            self.cache_dirty = True
            self.wake.wait(.05)

    def face_loop(self):
        deepface = None
        while not self.closing:
            if not self.enabled or self.paused or self.all_paused:
                self.status['state'] = 'paused' if self.enabled else 'idle'
                self.wake.wait(1); self.wake.clear(); continue
            with self.connection() as db:
                pending = list(db.execute('SELECT id FROM media WHERE present=1 AND kind="image" AND (face_version IS NULL OR face_version!=?)', (FACE_VERSION,)))
                self.status['total'] = db.execute('SELECT count(*) FROM media WHERE present=1 AND kind="image"').fetchone()[0]
                self.status['done'] = self.status['total'] - len(pending)
                self.status['failed'] = db.execute('SELECT count(*) FROM media WHERE present=1 AND face_error IS NOT NULL').fetchone()[0]
            if not pending:
                if self.organization_pending:
                    self.people_organizer.start(background=True);self.organization_pending=0
                self.status.update(state='complete', current=''); self.wake.wait(2); self.wake.clear(); continue
            priority = {value: i for i, value in enumerate(self.priority)}
            pending.sort(key=lambda r: priority.get(r['id'], len(priority)))
            media_id = pending[0]['id']
            self.status.update(state='loading' if deepface is None else 'processing', current=Path(media_id).name, error='')
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    if deepface is None:
                        os.environ.setdefault('OMP_NUM_THREADS', '2')
                        from deepface import DeepFace
                        # Give playback/interaction room: one model, two inference threads.
                        import onnxruntime as ort
                        recognizer = DeepFace.build_model(MODEL)
                        options = ort.SessionOptions(); options.intra_op_num_threads = 2; options.inter_op_num_threads = 1; options.log_severity_level = 3
                        weights = self.root / 'models' / '.deepface' / 'weights' / 'face_recognition_sface_2021dec.onnx'
                        recognizer.model = ort.InferenceSession(str(weights), sess_options=options, providers=['CPUExecutionProvider'])
                        deepface = DeepFace
                    import numpy as np
                    import cv2
                    from PIL import Image, ImageOps
                    cv2.setNumThreads(2)
                    with self.connection() as db:
                        source = self.original(db, media_id)
                        snapshot = dict(db.execute('SELECT bytes,modified,fingerprint FROM media WHERE id=?', (media_id,)).fetchone())
                    with open_image(source) as opened:
                        image = ImageOps.exif_transpose(opened).convert('RGB'); image.thumbnail((1600, 1600))
                    try:
                        os.environ['yunet_score_threshold'] = '0.9'
                        faces = deepface.represent(img_path=np.array(image)[:, :, ::-1], model_name=MODEL, detector_backend='yunet', enforce_detection=True, align=True)
                    except ValueError as error:
                        if 'Face could not be detected' in str(error): faces = []
                        else: raise
                with self.lock, self.connection() as db:
                    # A rescan cannot attach an old face result to a changed original.
                    m = db.execute('SELECT * FROM media WHERE id=? AND present=1', (media_id,)).fetchone()
                    if not m or any(m[key] != value for key,value in snapshot.items()): continue
                    current_source = self.original(db, media_id)
                    if current_source.stat().st_size != m['bytes']: continue
                    rejected = [json.loads(r['box']) for r in db.execute('SELECT box FROM rejected_faces WHERE media_id=? AND fingerprint=?',(media_id,m['fingerprint']))]
                    assigned = [(json.loads(r['box']),r['person_id']) for r in db.execute('SELECT box,person_id FROM face_assignments WHERE media_id=? AND fingerprint=?',(media_id,m['fingerprint']))]
                    # Replace a photo's old detections only after successful re-analysis;
                    # person seeds, confirmed merges and names remain available.
                    db.execute('DELETE FROM faces WHERE media_id=?',(media_id,))
                    # Confirmed merges retain distinct appearances instead of discarding
                    # the source group or averaging away different angles/lighting.
                    prototypes=banks(db);limits=learned_limits(db);negative={}
                    for row in db.execute('SELECT person_id,embedding FROM person_negatives'):
                        negative.setdefault(row['person_id'],[]).append(unit(json.loads(row['embedding'])))
                    negative={key:np.asarray(values,dtype=np.float32) for key,values in negative.items()}
                    used_people=set()
                    for face in faces:
                        vector = np.array(face['embedding'], dtype=np.float32); vector /= max(np.linalg.norm(vector), 1e-9)
                        area = face['facial_area']; x,y,w,h = [int(area[k]) for k in ['x','y','w','h']]
                        if w < 20 or h < 20 or face.get('face_confidence',1) < .9 or any(self.overlaps(area,box) > .4 for box in rejected): continue
                        overrides=sorted([(self.overlaps(area,box),key) for box,key in assigned],reverse=True)
                        person_id=overrides[0][1] if overrides and overrides[0][0]>.4 else match(vector,{key:bank for key,bank in prototypes.items() if key not in used_people},negative,limits)
                        if person_id is None:
                            person_id = str(uuid.uuid4()); db.execute('INSERT INTO people(id,embedding) VALUES(?,?)', (person_id, json.dumps(vector.tolist())))
                            prototypes[person_id]=np.asarray([vector],dtype=np.float32)
                        elif float(np.min(1-prototypes.get(person_id,np.asarray([vector]))@vector))<.25:
                            # Retain confident alternate poses, not just the first
                            # face seed. Keep the reference bank bounded.
                            db.execute('INSERT OR IGNORE INTO person_exemplars VALUES(?,?)',(person_id,json.dumps(vector.tolist())))
                            values=[r[0] for r in db.execute('SELECT embedding FROM person_exemplars WHERE person_id=?',(person_id,))]
                            if len(values)>64:
                                db.execute('DELETE FROM person_exemplars WHERE person_id=?',(person_id,))
                                db.executemany('INSERT INTO person_exemplars VALUES(?,?)',[(person_id,v) for v in diverse(values,48)])
                            prototypes[person_id]=np.asarray([unit(json.loads(v)) for v in diverse([json.dumps(v.tolist()) for v in prototypes[person_id]]+[json.dumps(vector.tolist())],48)],dtype=np.float32)
                        used_people.add(person_id)
                        pad = int(max(w,h)*.18)
                        crop = image.crop((max(0,x-pad),max(0,y-pad),min(image.width,x+w+pad),min(image.height,y+h+pad))); crop.thumbnail((160,160))
                        face_id = str(uuid.uuid4()); filename = self.derivative_name(db,m['folder_id'],f'faces/{face_id}.jpg')
                        crop_path = self.resource_path(db,filename); crop_path.parent.mkdir(parents=True,exist_ok=True)
                        crop.save(crop_path, 'JPEG', quality=80)
                        db.execute('INSERT INTO faces VALUES(?,?,?,?,?,?)', (face_id, media_id, person_id, json.dumps(vector.tolist()), json.dumps(area), filename))
                    db.execute('UPDATE media SET face_version=?,face_error=NULL WHERE id=?', (FACE_VERSION, media_id))
                self.cache_dirty = True
                self.organization_pending += 1
                if self.organization_pending>=50:
                    self.people_organizer.start(background=True);self.organization_pending=0
            except Exception as error:
                self.status.update(state='error', error=str(error))
                if deepface is None:
                    self.enabled = False
                else:
                    with self.connection() as db: db.execute('UPDATE media SET face_version=?,face_error=? WHERE id=?', (FACE_VERSION, str(error), media_id))
                self.wake.wait(1); self.wake.clear()

    def dispatch(self, op, args):
        result = self._dispatch(op,args)
        if op in ['scan','previewSave','favorite','renamePerson','mergePeople','rejectPeople','refindFaces','reviewPerson','setMe','editPersonFaces']: self.cache_dirty=True
        if op in ['reviewPerson','mergePeople','editPersonFaces'] and not args.get('automatic'):
            self.people_organizer.start()
        return result

    def _dispatch(self, op, args):
        if op=='organizePeople':return self.people_organizer.start()
        if op=='peopleOrganization':return dict(self.people_organizer.state)
        if op=='editPersonFaces':return self.people_organizer.edit(args)
        if op=='groupAppearances':
            offset=max(0,int(args.get('offset',0)));limit=min(100,max(1,int(args.get('limit',60))))
            with self.connection() as db:
                query='FROM faces f JOIN media m ON m.id=f.media_id WHERE f.person_id=? AND m.present=1'
                total=db.execute('SELECT count(*) '+query,(args['id'],)).fetchone()[0]
                rows=[{'id':r['id'],'mediaId':r['media_id'],'name':Path(r['path']).name} for r in db.execute('SELECT f.id,f.media_id,m.path '+query+' ORDER BY f.id LIMIT ? OFFSET ?',(args['id'],limit,offset))]
                return {'total':total,'faces':rows}
        if op == 'categories': return self.categories.changes(args)
        if op == 'assignCategories': return self.categories.assign(args)
        if op == 'mediaSelection':
            ids=list(dict.fromkeys(args.get('ids',[])))
            if not ids or len(ids)>100: raise ValueError('Select between 1 and 100 files per action.')
            with self.connection() as db:
                result=[]
                for media_id in ids:
                    row=db.execute('SELECT * FROM media WHERE id=? AND present=1',(media_id,)).fetchone()
                    if not row or row['kind'] not in ['image','raw','video']: raise ValueError('Only available photos and videos can be selected.')
                    source=self.original(db,media_id)
                    result.append({'id':media_id,'path':str(source),'name':source.name,'byteSize':row['bytes'],'modified':row['modified']})
                return result
        if op == 'mediaRemoved':
            with self.lock,self.connection() as db:
                for media_id in args.get('ids',[]):
                    try: self.original(db,media_id)
                    except FileNotFoundError: db.execute('UPDATE media SET present=0 WHERE id=?',(media_id,))
                self.cache_dirty=True
            return True
        if op == 'similarPeople':
            import numpy as np
            person_id=args['id']
            with self.connection() as db:
                prototypes=banks(db)
                if person_id not in prototypes:raise ValueError('Choose a saved person to review.')
                vectors=prototypes[person_id];ranked={}
                active={row[0] for row in db.execute('SELECT DISTINCT f.person_id FROM faces f JOIN media m ON m.id=f.media_id WHERE m.present=1')}
                denied={row[0] for row in db.execute('SELECT second FROM person_separate WHERE first=?',(person_id,))}
                for key,bank in prototypes.items():
                    if key not in active or key==person_id or (key in denied and not args.get('revisit')):continue
                    distance=float(np.min(1-vectors@bank.T))
                    if args.get('all') or distance<.65:ranked[key]=distance
                output=[]
                for key,distance in sorted(ranked.items(),key=lambda pair:pair[1]):
                    examples=[row[0] for row in db.execute('SELECT f.id FROM faces f JOIN media m ON m.id=f.media_id WHERE f.person_id=? AND m.present=1 ORDER BY f.id LIMIT 6',(key,))]
                    output.append({'id':key,'distance':distance,'faceIds':examples,'previousNo':key in denied})
            return output if args.get('all') else output[:24]
        if op=='peopleReviews':return self.people_organizer.reviews(args)
        if op=='reviewFeedback':return self.people_organizer.enqueue(args)
        if op=='personFaces':
            with self.connection() as db:return [row[0] for row in db.execute('SELECT f.id FROM faces f JOIN media m ON m.id=f.media_id WHERE person_id=? AND m.present=1 ORDER BY f.id LIMIT 12',(args['id'],))]
        if op=='setMe':
            with self.lock,self.connection() as db:
                if not db.execute('SELECT 1 FROM people WHERE id=?',(args['id'],)).fetchone():raise ValueError('Choose an available person.')
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('myPerson',args['id']))
            return self.people({})
        if op=='reviewPerson':
            if args.get('same') is True:return self._dispatch('mergePeople',{'into':args['id'],'fromIds':[args['candidate']]})
            with self.lock,self.connection() as db:
                first,second=args['id'],args['candidate']
                if first==second:raise ValueError('Choose two different people.')
                for key in [first,second]:
                    if not db.execute('SELECT 1 FROM people WHERE id=?',(key,)).fetchone():raise ValueError('This group changed. Refresh the review.')
                for a,b in [(first,second),(second,first)]:
                    db.execute('INSERT OR IGNORE INTO person_separate VALUES(?,?)',(a,b))
                    values=[row[0] for row in db.execute('SELECT embedding FROM people WHERE id=? UNION ALL SELECT embedding FROM person_exemplars WHERE person_id=?',(b,b))]
                    values+=[row[0] for row in db.execute('SELECT embedding FROM person_negatives WHERE person_id=?',(a,))]
                    db.execute('DELETE FROM person_negatives WHERE person_id=?',(a,))
                    db.executemany('INSERT OR IGNORE INTO person_negatives VALUES(?,?)',[(a,v) for v in diverse(values,64)])
            return self.people({})
        if op == 'captureDates': return self.capture_dates.changes(args)
        if op.startswith('memory:'): return self.memories.dispatch(op.split(':',1)[1],args)
        if op == 'workspaceApply':
            # Path restoration only: no scanning, copying originals or clearing metadata.
            folders=args.get('folders',[])
            with self.lock,self.connection() as db:
                known={row['id'] for row in db.execute('SELECT id FROM folders')}
                prepared=[]
                for folder in folders:
                    if folder['id'] not in known: raise ValueError('The workspace folder does not belong to this saved catalog.')
                    source=Path(folder['location'])
                    if not source.is_absolute(): raise ValueError('Workspace originals require an absolute location.')
                    cache=Path(folder['cacheLocation']).resolve() if folder.get('cacheLocation') else None
                    if cache==self.root: cache=None
                    if cache and (cache/'vela-folder-cache.json').exists():
                        marker=json.loads((cache/'vela-folder-cache.json').read_text('utf-8'))
                        if marker.get('format')!='vela-folder-cache' or marker.get('version')!=1 or marker.get('folderId')!=folder['id']:
                            raise ValueError('Workspace cache identity does not match its media folder.')
                    prepared.append((folder['id'],source.resolve(),cache))
                for identity,source,cache in prepared:
                    try: relative=os.path.relpath(source,self.root)
                    except ValueError: relative=''
                    db.execute('UPDATE folders SET root=?,relative_root=? WHERE id=?',(str(source),relative,identity))
                    if cache:
                        try: cache_relative=os.path.relpath(cache,source)
                        except ValueError: cache_relative=''
                        db.execute('INSERT OR REPLACE INTO folder_caches VALUES(?,?,?)',(identity,str(cache),cache_relative))
                    else: db.execute('DELETE FROM folder_caches WHERE folder_id=?',(identity,))
                    for table,key in [('previews','media_id'),('faces','media_id')]:
                        for row in list(db.execute(f'SELECT {table}.rowid AS identity,{table}.file FROM {table} JOIN media m ON m.id={table}.{key} WHERE m.folder_id=?',(identity,))):
                            tail=row['file'].split('/',1)[1] if row['file'].startswith('@') else row['file']
                            name=f'@{identity}/{tail}' if cache else tail
                            db.execute(f'UPDATE {table} SET file=? WHERE rowid=?',(name,row['identity']))
            return True
        if op == 'startScan': return self.start_scan(args)
        if op == 'scanStatus': return dict(self.scan_state)
        if op == 'mountFolderCache': return self.mount_cache(args['path'],args['cachePath'])
        if op == 'setFolderCache': return self.attach_cache(args['folderId'],args['cachePath'])
        if op == 'syncFolderCaches': self.export_caches(); return {'error':self.cache_error}
        if op == 'load': return self.load(args)
        if op == 'scan': return self.scan(args)
        if op == 'previewGet': return self.preview_get(args)
        if op == 'previewSave': return self.preview_save(args)
        if op == 'people': return self.people(args)
        if op == 'status':
            with self.connection() as db:
                db.execute('BEGIN')
                total = db.execute('SELECT count(*) FROM media WHERE present=1 AND kind="image"').fetchone()[0]
                done = db.execute('SELECT count(*) FROM media WHERE present=1 AND kind="image" AND face_version=?', (FACE_VERSION,)).fetchone()[0]
                failed = db.execute('SELECT count(*) FROM media WHERE present=1 AND face_error IS NOT NULL').fetchone()[0]
                locations = dict(self.location_status)
                locations['total'] = total
                locations['done'] = db.execute('SELECT count(*) FROM locations l JOIN media m ON m.id=l.media_id WHERE m.present=1 AND m.kind="image" AND l.version=m.fingerprint').fetchone()[0]
                locations['tagged'] = db.execute('SELECT count(*) FROM locations l JOIN media m ON m.id=l.media_id WHERE m.present=1 AND l.version=m.fingerprint AND l.latitude IS NOT NULL').fetchone()[0]
                locations['failed'] = db.execute('SELECT count(*) FROM locations l JOIN media m ON m.id=l.media_id WHERE m.present=1 AND l.version=m.fingerprint AND l.error IS NOT NULL').fetchone()[0]
            state = self.status['state']
            if state == 'complete' and done < total: state = 'processing'
            return {**self.status, 'done':done, 'total':total, 'failed':failed, 'state':state, 'enabled': self.enabled, 'paused': self.paused or self.all_paused, 'allPaused':self.all_paused, 'locations':locations, 'cacheError':self.cache_error, 'scan':dict(self.scan_state)}
        if op == 'indexControl':
            self.all_paused = bool(args['paused'])
            with self.connection() as db: db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('allPaused',json.dumps(self.all_paused)))
            self.wake.set(); return self.dispatch('status',{})
        if op == 'locations':
            with self.connection() as db:
                return [dict(r) for r in db.execute('SELECT l.media_id AS mediaId,l.latitude,l.longitude FROM locations l JOIN media m ON m.id=l.media_id WHERE m.present=1 AND (l.version=m.fingerprint OR l.version IS NULL) AND l.latitude IS NOT NULL')]
        if op == 'refindFaces':
            with self.lock, self.connection() as db: db.execute('UPDATE media SET face_version=NULL,face_error=NULL WHERE present=1 AND kind="image"')
            self.enabled = True
            with self.connection() as db: db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('facesEnabled','true'))
            self.wake.set(); return self.dispatch('status',{})
        if op == 'refindLocations':
            with self.lock,self.connection() as db:
                db.execute("UPDATE locations SET version=NULL WHERE media_id IN (SELECT id FROM media WHERE present=1 AND kind='image')")
            self.location_status.update(state='queued',current='')
            self.cache_dirty=True;self.wake.set()
            return self.dispatch('status',{})
        if op == 'rejectPeople':
            ids = list(dict.fromkeys(args['ids']))
            if not ids or len(ids)>200: raise ValueError('Select up to 200 non-face groups.')
            with self.lock, self.connection() as db:
                for person_id in ids:
                    db.execute('INSERT OR IGNORE INTO rejected_faces SELECT f.media_id,m.fingerprint,f.box FROM faces f JOIN media m ON m.id=f.media_id WHERE f.person_id=?',(person_id,))
                    db.execute('DELETE FROM faces WHERE person_id=?',(person_id,))
                    db.execute('DELETE FROM people WHERE id=?',(person_id,))
            return True
        if op == 'faceControl':
            if 'enabled' in args:
                self.enabled = bool(args['enabled'])
                with self.connection() as db: db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('facesEnabled', json.dumps(self.enabled)))
            if 'paused' in args: self.paused = bool(args['paused'])
            if 'priority' in args: self.priority = args['priority'][:100]
            if args.get('retry'):
                with self.connection() as db: db.execute('UPDATE media SET face_version=NULL,face_error=NULL WHERE face_error IS NOT NULL')
            self.wake.set(); return self.dispatch('status',{})
        if op == 'renamePerson':
            name = str(args['name']).strip()[:120]
            with self.lock,self.connection() as db: db.execute('UPDATE people SET name=? WHERE id=?', (name, args['id']))
            return True
        if op == 'mergePeople':
            with self.lock, self.connection() as db:
                target = args['into']
                sources = list(dict.fromkeys(args.get('fromIds', [args.get('from')])) )
                if not sources or len(sources) > 200 or target in sources: raise ValueError('Choose different groups to merge into the retained person.')
                for person_id in [target, *sources]:
                    if not db.execute('SELECT id FROM people WHERE id=?', (person_id,)).fetchone(): raise ValueError('A selected person is no longer available. Refresh People and try again.')
                import numpy as np
                if not args.get('automatic'):
                    def confirmed_bank(person_id):
                        values=[r[0] for r in db.execute('SELECT embedding FROM people WHERE id=? UNION ALL SELECT embedding FROM person_exemplars WHERE person_id=?',(person_id,person_id))]
                        return np.asarray([unit(json.loads(v)) for v in diverse(values)])
                    bank=confirmed_bank(target)
                    distances=[]
                    for source_id in sources:
                        incoming=confirmed_bank(source_id)
                        distances.extend((1-incoming@bank.T).min(axis=1).tolist())
                    radius=min(.38,max(.32,float(np.quantile(distances,.9))+.04))
                    db.execute('INSERT INTO person_learning VALUES(?,?,1) ON CONFLICT(person_id) DO UPDATE SET radius=MAX(radius,excluded.radius),confirmations=confirmations+1',(target,radius))
                for person_id in sources:
                    learned=db.execute('SELECT radius,confirmations FROM person_learning WHERE person_id=?',(person_id,)).fetchone()
                    if learned:
                        db.execute('INSERT INTO person_learning VALUES(?,?,?) ON CONFLICT(person_id) DO UPDATE SET radius=MAX(radius,excluded.radius),confirmations=confirmations+excluded.confirmations',(target,learned['radius'],learned['confirmations']))
                        db.execute('DELETE FROM person_learning WHERE person_id=?',(person_id,))
                    # Retain confirmed pose/lighting examples, not just the first
                    # portrait that originally seeded each fragmented group.
                    db.execute('INSERT OR IGNORE INTO person_exemplars SELECT ?,embedding FROM faces WHERE person_id=? LIMIT 24',(target,person_id))
                    db.execute('INSERT OR IGNORE INTO person_exemplars SELECT ?,embedding FROM people WHERE id=?', (target,person_id))
                    db.execute('INSERT OR IGNORE INTO person_exemplars SELECT ?,embedding FROM person_exemplars WHERE person_id=?', (target,person_id))
                    db.execute('UPDATE faces SET person_id=? WHERE person_id=?', (target,person_id))
                    db.execute('UPDATE face_assignments SET person_id=? WHERE person_id=?',(target,person_id))
                    db.execute('UPDATE person_alias SET person_id=? WHERE person_id=?',(target,person_id))
                    db.execute('INSERT OR REPLACE INTO person_alias VALUES(?,?)',(person_id,target))
                    if self.setting(db,'myPerson','')==person_id:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('myPerson',target))
                    # Carry unrelated negative labels over; a confirmed Yes overrides
                    # an earlier No between these exact groups, without broadening thresholds.
                    others=[r[0] for r in db.execute('SELECT second FROM person_separate WHERE first=?',(person_id,)) if r[0] not in [target,*sources]]
                    for other in others:
                        db.execute('INSERT OR IGNORE INTO person_separate VALUES(?,?)',(target,other))
                        db.execute('INSERT OR IGNORE INTO person_separate VALUES(?,?)',(other,target))
                    db.execute('DELETE FROM person_separate WHERE first=? OR second=?',(person_id,person_id))
                    db.execute('INSERT OR IGNORE INTO person_negatives SELECT ?,embedding FROM person_negatives WHERE person_id=?',(target,person_id))
                    db.execute('DELETE FROM people WHERE id=?', (person_id,))
                values=[r[0] for r in db.execute('SELECT embedding FROM people WHERE id=? UNION ALL SELECT embedding FROM person_exemplars WHERE person_id=?',(target,target))]
                db.execute('DELETE FROM person_exemplars WHERE person_id=?',(target,))
                db.executemany('INSERT OR IGNORE INTO person_exemplars VALUES(?,?)',[(target,v) for v in diverse(values)])
                # Remove contradictory negative examples that now belong to this person.
                import numpy as np
                positives=np.asarray([unit(json.loads(v)) for v in diverse(values)],dtype=np.float32)
                negatives=[r[0] for r in db.execute('SELECT embedding FROM person_negatives WHERE person_id=?',(target,))]
                negatives=[v for v in negatives if float(np.min(1-positives@unit(json.loads(v))))>.005]
                db.execute('DELETE FROM person_negatives WHERE person_id=?',(target,))
                db.executemany('INSERT OR IGNORE INTO person_negatives VALUES(?,?)',[(target,v) for v in diverse(negatives,64)])
            return {'personId':target,'people':self.people({})}
        if op == 'decodeImage':
            from PIL import Image, ImageOps, UnidentifiedImageError
            with self.connection() as db:
                row = db.execute('SELECT kind,fingerprint FROM media WHERE id=?', (args['id'],)).fetchone()
                if not row or row['kind'] not in ['image','raw']: raise ValueError('This item is not an image.')
                source = self.original(db, args['id'])
            if args.get('stream') and not args.get('thumbnail') and not args.get('memory'):
                key=(args['id'],row['fingerprint'],source.stat().st_mtime_ns)
                def load_view():
                    if row['kind']=='raw':
                        import rawpy
                        with rawpy.imread(str(source)) as raw:opened=Image.fromarray(raw.postprocess(use_camera_wb=True,output_bps=8))
                    else:opened=open_image(source)
                    with opened:
                        image=ImageOps.exif_transpose(opened);profile=image.info.get('icc_profile')
                        if image.mode=='CMYK' and profile:
                            from PIL import ImageCms
                            image=ImageCms.profileToProfile(image,ImageCms.ImageCmsProfile(io.BytesIO(profile)),ImageCms.createProfile('sRGB'),outputMode='RGB')
                            profile=image.info.get('icc_profile')
                        else:image=image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                        return image,profile
                return self.decoded_views.produce(key,load_view)
            try:
                if row['kind'] == 'raw':
                    import rawpy
                    with rawpy.imread(str(source)) as raw:
                        opened = Image.fromarray(raw.postprocess(use_camera_wb=True, output_bps=8, half_size=args.get('thumbnail') is True))
                else: opened = open_image(source)
                with opened:
                    image = ImageOps.exif_transpose(opened)
                    profile = image.info.get('icc_profile')
                    if image.mode == 'CMYK' and profile:
                        from PIL import ImageCms
                        image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile('sRGB'), outputMode='RGB')
                        profile = image.info.get('icc_profile')
                    else:
                        image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                    output = io.BytesIO()
                    thumbnail = args.get('thumbnail') is True
                    memory_frame = args.get('memory') is True
                    if thumbnail:
                        image.thumbnail((720,720)); image = image.convert('RGB')
                    elif memory_frame:
                        image.thumbnail((1600,1600)); image = image.convert('RGB')
                    image.save(output, 'JPEG' if thumbnail or memory_frame else 'PNG', **({'quality':82 if thumbnail else 92} if thumbnail or memory_frame else {'compress_level':1}), **({'icc_profile':profile} if profile else {}))
                    return {'data':base64.b64encode(output.getvalue()).decode('ascii'), 'mime':'image/jpeg' if thumbnail or memory_frame else 'image/png'}
            except UnidentifiedImageError as error:
                with source.open('rb') as stream:
                    all_zero = True
                    while chunk := stream.read(65536):
                        if any(chunk): all_zero = False; break
                if all_zero:
                    raise ValueError('This file contains only zero bytes, not image data. Restore a healthy copy from your backup; installing a decoder cannot repair it.') from error
                raise ValueError('The file does not contain a recognized image, or its format is unsupported. Check the original or restore it from a backup.') from error
            except (OSError, Image.DecompressionBombError) as error:
                raise ValueError(f'The original image could not be decoded: {error}. Check or restore the original file.') from error
        if op == 'favorite':
            with self.connection() as db: db.execute('UPDATE media SET favorite=? WHERE id=?', (bool(args['favorite']), args['id']))
            return True
        if op == 'resolve':
            if args['type']=='decoded':return self.decoded_views.resolve(args['id'])
            with self.connection() as db:
                if args['type'] == 'original': return str(self.original(db, args['id']))
                if args['type'] != 'face': raise ValueError('Unknown resource')
                row = db.execute('SELECT file FROM faces WHERE id=?', (args['id'],)).fetchone()
                if not row: raise FileNotFoundError('Face preview not found')
                file = self.resource_path(db,row['file'])
                return str(file)
        if op == 'storage':
            with self.connection() as db:
                cache = db.execute('SELECT coalesce(sum(bytes),0) FROM previews').fetchone()[0]
                for row in db.execute('SELECT file FROM faces'):
                    try: cache += self.resource_path(db,row['file']).stat().st_size
                    except (OSError,ValueError): pass
                return dict(path=str(self.root), cacheBytes=cache, databaseBytes=sum(f.stat().st_size for f in self.root.glob('catalog.sqlite*')), cacheLimit=int(self.setting(db, 'cacheLimit', str(512*1024*1024))), originalCopies=0)
        if op == 'cacheLimit':
            limit = int(args['megabytes'])
            if limit not in [128,256,512,1024]: raise ValueError('Unsupported thumbnail cache limit')
            with self.connection() as db: db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('cacheLimit',str(limit*1024*1024)))
            self.trim(); return self.dispatch('storage',{})
        if op == 'backup':
            self.export_caches()
            with self.lock, self.connection() as db:
                # Stable snapshot, no media content. Close/checkpoint the app before copying the folder.
                db.execute('PRAGMA wal_checkpoint(FULL)')
            return True
        if op == 'rebase':
            with self.connection() as db:
                for folder in args['folders']:
                    try: relative = os.path.relpath(folder['location'], self.root)
                    except ValueError: relative = ''
                    db.execute('UPDATE folders SET root=?,relative_root=? WHERE id=?', (folder['location'], relative, folder['id']))
            return True
        if op == 'shutdown':
            self.closing = True; self.enabled = False; self.wake.set()
            self.decoded_views.close()
            self.memories.thread.join(timeout=8)
            self.capture_dates.thread.join(timeout=13)
            self.categories.thread.join(timeout=5)
            if self.scanner: self.scanner.join(timeout=5)
            self.worker.join(timeout=15)
            self.location_worker.join(timeout=5)
            self.cache_worker.join(timeout=2)
            self.export_caches()
            with self.connection() as db: db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            return True
        raise ValueError('Unsupported local operation')

def main():
    engine = Engine(sys.argv[1])
    output_lock=threading.Lock()
    def respond(response):
        with output_lock:
            PROTOCOL_OUT.write(json.dumps(response,ensure_ascii=True)+'\n');PROTOCOL_OUT.flush()
    lanes=RequestLanes(engine,respond)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request['op'] in ['decodeImage','resolve']:
                lanes.submit(request);continue
            if request['op']=='shutdown':lanes.close()
            result = engine.dispatch(request['op'], request.get('args') or {})
            response = {'id': request['id'], 'result': result}
        except Exception as error:
            response = {'id': request.get('id') if 'request' in locals() else None, 'error': str(error)}
        respond(response)
        if request.get('op') == 'shutdown': break
    lanes.close()

if __name__ == '__main__': main()
