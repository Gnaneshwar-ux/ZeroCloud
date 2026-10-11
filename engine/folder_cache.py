"""Optional per-source portable caches. Originals are never copied or modified."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

class FolderCache:
    def init_folder_cache(self):
        self.cache_dirty = False
        self.cache_error = ''
        self.cache_worker = threading.Thread(target=self.cache_loop, daemon=True)
        self.cache_worker.start()

    def cache_root(self, db, folder_id):
        row = db.execute('SELECT c.*,f.root,f.relative_root FROM folder_caches c JOIN folders f ON f.id=c.folder_id WHERE c.folder_id=?',(folder_id,)).fetchone()
        if not row: return None
        relative = (self.resolve_root(row) / row['relative_cache']).resolve() if row['relative_cache'] else None
        directory = relative if relative and (relative/'vela-folder-cache.json').is_file() else Path(row['path'])
        marker = json.loads((directory/'vela-folder-cache.json').read_text('utf-8'))
        if marker.get('format')!='vela-folder-cache' or marker.get('version')!=1 or marker.get('folderId')!=folder_id: raise ValueError('Portable cache identity does not match this media folder.')
        return directory.resolve()

    def resource_path(self, db, name):
        if name.startswith('@'):
            folder_id, relative = name[1:].split('/',1)
            root = self.cache_root(db,folder_id)
            if not root: raise ValueError('Portable folder cache is not connected.')
        else: root,relative = self.root,name
        file = (root/relative).resolve()
        if not file.is_relative_to(root): raise ValueError('Invalid derivative path outside the selected cache.')
        return file

    def derivative_name(self, db, folder_id, relative):
        return f'@{folder_id}/{relative}' if db.execute('SELECT 1 FROM folder_caches WHERE folder_id=?',(folder_id,)).fetchone() else relative

    def validate_cache(self, source, directory):
        """Reject invalid destinations before registering or walking any originals."""
        source, directory = Path(source).resolve(), Path(directory).resolve()
        if directory == self.root:
            return None  # The selected folder is already our shared catalog/cache.
        if directory.is_relative_to(self.root):
            raise ValueError('Choose the main library folder itself to reuse app storage, or a separate portable folder cache.')
        if directory == source:
            raise ValueError('Keep the cache separate from the originals. Use a .vela-cache subfolder or app storage.')
        marker = directory/'vela-folder-cache.json'
        if marker.exists():
            value = json.loads(marker.read_text('utf-8'))
            if value.get('format')!='vela-folder-cache' or value.get('version')!=1 or not value.get('folderId'):
                raise ValueError('Incompatible portable folder cache.')
            if not (directory/'catalog.sqlite').is_file():
                raise ValueError('The portable cache is missing its catalog. Restore its complete folder.')
            with self.connection() as db:
                for folder in db.execute('SELECT * FROM folders'):
                    folder_root = self.resolve_root(folder).resolve()
                    if folder['id']==value['folderId'] and folder_root.is_dir() and folder_root!=source:
                        raise ValueError('This portable cache belongs to a different connected folder. Relink that folder instead.')
                    if folder_root==source and folder['id']!=value['folderId']:
                        raise ValueError('This source is already added. Use its Portable cache option instead of importing another identity.')
        elif directory.exists() and any(directory.iterdir()):
            if (directory/'vela-library.json').is_file():
                raise ValueError('This is another complete library, not a per-folder cache. Open it from Library storage, or choose an empty cache folder.')
            raise ValueError('Choose an empty cache folder or a matching portable folder cache. Existing files will not be overwritten.')
        return directory

    def attach_cache(self, folder_id, directory):
        directory = Path(directory).resolve()
        if directory == self.root:
            with self.lock,self.connection() as db:
                if not db.execute('SELECT 1 FROM folders WHERE id=?',(folder_id,)).fetchone():
                    raise ValueError('Unknown media folder.')
                for table, key in [('previews','media_id'),('faces','id')]:
                    query = 'SELECT p.* FROM previews p JOIN media m ON m.id=p.media_id WHERE m.folder_id=?' if table=='previews' else 'SELECT f.* FROM faces f JOIN media m ON m.id=f.media_id WHERE m.folder_id=?'
                    for row in list(db.execute(query,(folder_id,))):
                        if not row['file'].startswith('@'): continue
                        old = self.resource_path(db,row['file'])
                        tail = row['file'].split('/',1)[1]
                        new = (self.root/tail).resolve()
                        if not new.is_relative_to(self.root): raise ValueError('Invalid cache entry.')
                        if old.is_file() and old!=new:
                            new.parent.mkdir(parents=True,exist_ok=True)
                            if not new.exists(): shutil.copyfile(old,new)
                        db.execute(f'UPDATE {table} SET file=? WHERE {key}=?',(tail,row[key]))
                db.execute('DELETE FROM folder_caches WHERE folder_id=?',(folder_id,))
            self.cache_dirty=True
            return True
        if directory.is_relative_to(self.root): raise ValueError('Choose a separate per-folder cache, not a subfolder of the main library.')
        directory.mkdir(parents=True,exist_ok=True)
        marker = directory/'vela-folder-cache.json'
        if marker.exists():
            value = json.loads(marker.read_text('utf-8'))
            if value.get('format')!='vela-folder-cache' or value.get('version')!=1 or value.get('folderId')!=folder_id: raise ValueError('This portable cache belongs to another media folder. Choose an empty folder or its matching media folder.')
        else:
            if any(directory.iterdir()): raise ValueError('Choose an empty cache folder. For a media folder, use the automatic .vela-cache option; originals will not be overwritten.')
            marker.write_text(json.dumps({'format':'vela-folder-cache','version':1,'folderId':folder_id}),'utf-8')
        with self.lock,self.connection() as db:
            folder = db.execute('SELECT * FROM folders WHERE id=?',(folder_id,)).fetchone()
            if not folder: raise ValueError('Unknown media folder.')
            try: relative = os.path.relpath(directory,self.resolve_root(folder))
            except ValueError: relative = ''
            # Resolve old derivatives before changing the cache pointer.
            copies = []
            for table, key in [('previews','media_id'),('faces','id')]:
                query = 'SELECT p.* FROM previews p JOIN media m ON m.id=p.media_id WHERE m.folder_id=?' if table=='previews' else 'SELECT f.* FROM faces f JOIN media m ON m.id=f.media_id WHERE m.folder_id=?'
                for row in db.execute(query,(folder_id,)):
                    try: old = self.resource_path(db,row['file'])
                    except (OSError,ValueError): continue
                    tail = row['file'].split('/',1)[1] if row['file'].startswith('@') else row['file']
                    new = (directory/tail).resolve()
                    if not new.is_relative_to(directory): raise ValueError('Invalid cache entry.')
                    copies.append((table,key,row[key],old,new,f'@{folder_id}/{tail}'))
            db.execute('INSERT OR REPLACE INTO folder_caches VALUES(?,?,?)',(folder_id,str(directory),relative))
            for table,key,identity,old,new,name in copies:
                if old.is_file() and old!=new:
                    new.parent.mkdir(parents=True,exist_ok=True)
                    if not new.exists(): shutil.copyfile(old,new)
                db.execute(f'UPDATE {table} SET file=? WHERE {key}=?',(name,identity))
        self.cache_dirty = True
        self.export_caches()
        return True

    def mount_cache(self, source, directory, background=False):
        source,directory = Path(source).resolve(),Path(directory).resolve()
        if not source.is_dir(): raise ValueError('The selected media folder is unavailable.')
        if self.validate_cache(source,directory) is None:
            return self.scan({'path':str(source),'background':background})
        marker = directory/'vela-folder-cache.json'
        if not marker.exists():
            result = self.scan({'path':str(source),'background':background})
            self.attach_cache(result['folderId'],directory)
            return result
        manifest = json.loads(marker.read_text('utf-8'))
        if manifest.get('format')!='vela-folder-cache' or manifest.get('version')!=1: raise ValueError('Incompatible portable folder cache.')
        folder_id = manifest['folderId']
        snapshot = directory/'catalog.sqlite'
        if not snapshot.is_file(): raise ValueError('The portable cache is missing its catalog. Restore its complete folder.')
        saved = sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True); saved.row_factory=sqlite3.Row
        try:
            folder = saved.execute('SELECT * FROM folders WHERE id=?',(folder_id,)).fetchone()
            if not folder: raise ValueError('The saved cache does not contain its source folder.')
            with self.lock,self.connection() as db:
                # Reject aliasing one folder identity to two live originals.
                previous = db.execute('SELECT * FROM folders WHERE id=?',(folder_id,)).fetchone()
                if previous and self.resolve_root(previous).is_dir() and self.resolve_root(previous).resolve()!=source: raise ValueError('This folder is already connected elsewhere. Relink it instead of adding a second copy.')
                for local in db.execute('SELECT * FROM folders'):
                    if local['id']!=folder_id and self.resolve_root(local).resolve()==source: raise ValueError('This source is already added. Use its Portable cache option, or import before adding it.')
                try: relative = os.path.relpath(source,self.root)
                except ValueError: relative = ''
                db.execute('INSERT INTO folders VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET root=excluded.root,relative_root=excluded.relative_root',(folder_id,folder['name'],str(source),relative,folder['added']))
                for table in ['people','person_exemplars','person_negatives','person_separate','person_alias','media','previews','faces','face_assignments','rejected_faces','locations','capture_dates','media_categories']:
                    if not saved.execute('SELECT name FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone(): continue
                    for row in saved.execute(f'SELECT * FROM {table}'):
                        if table=='media' and row['folder_id']!=folder_id: raise ValueError('Invalid source identity in portable cache.')
                        allowed = {column[1] for column in db.execute(f'PRAGMA table_info({table})')}
                        if any(column not in allowed for column in row.keys()): raise ValueError('Unexpected columns in portable cache.')
                        columns = ','.join(row.keys()); placeholders = ','.join('?' for _ in row)
                        db.execute(f'INSERT OR IGNORE INTO {table}({columns}) VALUES({placeholders})',tuple(row))
                me=saved.execute("SELECT value FROM settings WHERE key='myPerson'").fetchone()
                if me and not self.setting(db,'myPerson',''):
                    db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',('myPerson',me[0]))
                try: cache_relative = os.path.relpath(directory,source)
                except ValueError: cache_relative = ''
                db.execute('INSERT OR REPLACE INTO folder_caches VALUES(?,?,?)',(folder_id,str(directory),cache_relative))
        finally: saved.close()
        result = self.scan({'path':str(source),'folderId':folder_id,'background':background})
        self.cache_dirty=True; self.export_caches(); return result

    def export_caches(self):
        with self.lock,self.connection() as db:
            self.cache_dirty = False
            for row in list(db.execute('SELECT folder_id FROM folder_caches')):
                temporary = None
                try:
                    folder_id=row['folder_id']; directory=self.cache_root(db,folder_id)
                    temporary=directory/'catalog.snapshot.tmp'
                    destination=sqlite3.connect(temporary)
                    try:
                        db.backup(destination)
                        destination.execute('PRAGMA journal_mode=DELETE'); destination.execute('PRAGMA foreign_keys=ON')
                        destination.execute('DELETE FROM media WHERE folder_id!=?',(folder_id,))
                        destination.execute('DELETE FROM folder_caches WHERE folder_id!=?',(folder_id,))
                        destination.execute('DELETE FROM folders WHERE id!=?',(folder_id,))
                        destination.execute('DELETE FROM people WHERE id NOT IN (SELECT person_id FROM faces)')
                        destination.execute('DELETE FROM settings WHERE key="diskScan"')
                        destination.commit(); destination.execute('VACUUM')
                    finally: destination.close()
                    temporary.replace(directory/'catalog.sqlite')
                    self.cache_error=''
                except (OSError,ValueError,sqlite3.Error) as error:
                    self.cache_error=f'Portable cache sync: {error}'; self.cache_dirty=True
                    if temporary:
                        try: temporary.unlink(missing_ok=True)
                        except OSError: pass

    def cache_loop(self):
        while not self.closing:
            # Export batches, not once per thumbnail, to avoid random disk I/O.
            for _ in range(60):
                if self.closing: return
                time.sleep(.5)
            if self.cache_dirty and not self.all_paused: self.export_caches()
