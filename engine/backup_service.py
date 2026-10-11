"""ZeroCloud JSON-lines adapter for the locally vendored Disk Backup Manager.

Own process, one disk-heavy operation at a time, independent of the media engine.
No network listener. No job resumes or deletes files without an explicit UI action.
"""
from __future__ import annotations
import dataclasses, hashlib, json, os, secrets, sqlite3, sys, threading, time, uuid
from datetime import date
from pathlib import Path, PurePosixPath
from disk_backup.backup import BackupManager, estimate_selected, validate_target_location
from disk_backup.cleanup import CleanupManager
from disk_backup.config import AppPaths
from disk_backup.database import Catalog, utc_now
from disk_backup.devices import Device, connected_devices, filesystem_devices
from disk_backup.hashing import sha256_file
from disk_backup.indexer import index_volume
from disk_backup.media import CATEGORY_DESTINATIONS, CATEGORY_LABELS
from disk_backup.mtp import collect_entry_groups, collect_entries, copy_entry_to, list_children
from disk_backup.restore import RestoreManager
from disk_backup.search import search_catalog, index_health
from disk_backup.selection import SourceFile, collect_filesystem_sources, mtp_sources, categorize_destinations, summarize


def rows(cursor):
    return [dict(row) for row in cursor]


def absolute(value):
    path = Path(str(value))
    if not path.is_absolute():
        raise ValueError('Choose an absolute folder path.')
    return path.resolve()


def within(path, root):
    return path == root or root in path.parents


class BackupService:
    def __init__(self, directory):
        self.paths = AppPaths.from_state_root(Path(__file__).parent.parent, absolute(directory))
        self.paths.ensure()
        self.state_lock = (self.paths.state/'zerocloud.run.lock').open('a+b')
        if self.state_lock.tell()==0:
            self.state_lock.write(b'0');self.state_lock.flush()
        self.state_lock.seek(0)
        if os.name=='nt':
            import msvcrt
            try:
                msvcrt.locking(self.state_lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:
                self.state_lock.close()
                raise RuntimeError('This backup state folder is already open in another ZeroCloud instance.') from exc
        self.catalog = Catalog(self.paths.database)
        self.catalog.connection.execute('''CREATE TABLE IF NOT EXISTS zerocloud_tasks
            (id TEXT PRIMARY KEY, kind TEXT NOT NULL, args TEXT NOT NULL, state TEXT NOT NULL,
            done INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, current TEXT DEFAULT '',
            result TEXT, error TEXT, updated TEXT NOT NULL)''')
        with self.catalog.transaction() as db:
            for table in ['backup_jobs', 'restore_jobs', 'cleanup_sessions']:
                db.execute(f"UPDATE {table} SET status='paused' WHERE status='running'")
            db.execute("UPDATE zerocloud_tasks SET state='paused' WHERE state='running'")
        self.thread = None
        self.stop = threading.Event()
        self.active = None
        self.progress = {}
        self.challenges = {}
        self.inventories = self.paths.state / 'inventories'
        self.inventories.mkdir(exist_ok=True)
        self.closed = False

    def available(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError('Pause the current disk operation and wait for its current file to finish first.')

    def start(self, kind, args):
        self.available()
        task_id = str(args.get('taskId') or uuid.uuid4())
        payload = dict(args)
        payload.pop('taskId', None)
        # A destructive challenge is never stored or reusable after restart.
        payload.pop('challenge', None)
        with self.catalog.transaction() as db:
            db.execute('''INSERT INTO zerocloud_tasks(id,kind,args,state,updated) VALUES(?,?,?,'running',?)
                ON CONFLICT(id) DO UPDATE SET state='running',error=NULL,updated=excluded.updated''',
                (task_id, kind, json.dumps(payload), utc_now()))
        self.stop.clear()
        self.active = task_id
        self.progress = {'id':task_id,'kind':kind,'state':'running','done':0,'bytes':0,'current':''}
        self.thread = threading.Thread(target=self.run, args=(task_id, kind, payload), daemon=True)
        self.thread.start()
        return {'id':task_id}

    def run(self, task_id, kind, args):
        catalog = None
        com = False
        try:
            if os.name == 'nt':
                import pythoncom
                pythoncom.CoInitialize()
                com = True
            catalog = Catalog(self.paths.database)
            def progress(done, total=0, byte_count=0, current=''):
                self.progress = {'id':task_id,'kind':kind,'state':'pausing' if self.stop.is_set() else 'running',
                    'done':done,'total':total,'bytes':byte_count,'current':str(current)}
                if self.stop.is_set():
                    raise KeyboardInterrupt()
            progress(0)
            if kind == 'inventory':
                paths = args.get('paths') or []
                if not paths:
                    raise ValueError('Select at least one source folder.')
                if args.get('deviceId'):
                    def phone_progress(current, count, byte_count):
                        progress(count, 0, byte_count, current)
                    groups = collect_entry_groups(args['deviceId'], paths, phone_progress)
                    distinct = {entry.logical_path:entry for _, group in groups for entry in group}
                    entries = mtp_sources(distinct.values())
                else:
                    requested = [absolute(path) for path in paths]
                    for path in requested:
                        if within(path, self.paths.state):
                            raise ValueError('Application state cannot be selected as a source.')
                        if not path.exists():
                            raise ValueError('A selected source folder is not connected.')
                    entries = []
                    # Reuse the engine's iterator, but report per-file progress and deduplicate overlapping selections.
                    seen = set()
                    for path in requested:
                        from disk_backup.selection import _filesystem_entry
                        from disk_backup.devices import iter_files
                        files = [path] if path.is_file() else iter_files(path,(self.paths.state,Path(__file__).parent.parent))
                        for file in files:
                            key = str(file.resolve()).casefold()
                            if key in seen:
                                continue
                            seen.add(key)
                            relative = Path(file.name) if path.is_file() else Path(path.name or 'Drive') / file.relative_to(path)
                            entries.append(_filesystem_entry(file, relative))
                            progress(len(entries), 0, 0, file.name)
                if not entries:
                    raise ValueError('No readable files were found in this selection.')
                self.save_inventory(task_id, entries, args)
                result = self.inventory_summary(task_id,catalog=catalog)
            elif kind == 'index':
                device = self.resolve_device(args)
                result = dataclasses.asdict(index_volume(catalog, device, full=bool(args.get('full')),
                    excluded_roots=(self.paths.state, Path(__file__).parent.parent), workers=2,
                    progress=lambda count, byte_count, hashed:progress(count, 0, byte_count, f'{hashed} bytes hashed')))
            elif kind == 'backup':
                result = dataclasses.asdict(BackupManager(catalog, self.paths.staging).run_job(args['id'], progress))
            elif kind == 'restore':
                result = dataclasses.asdict(RestoreManager(catalog, self.paths.staging).run_job(args['id'], progress))
            elif kind == 'cleanup':
                manager = CleanupManager(catalog)
                manager.set_session_status(args['id'], 'running')
                result = {'deleted':0,'freedBytes':0,'errors':[]}
                session = manager.session(args['id'])
                filesystem_paths = args['paths'] if session['source_kind']!='mtp' else []
                if session['source_kind']=='mtp':
                    # Verify each source's bytes, then use one Windows Shell batch per 20 files.
                    for offset in range(0,len(args['paths']),20):
                        verified = []
                        for position, source in enumerate(args['paths'][offset:offset+20],offset):
                            progress(position,len(args['paths']),result['freedBytes'],source)
                            try:
                                self.guard_cleanup(catalog,args['id'],source)
                                verified.append(source)
                            except Exception as exc:
                                manager.note_item_error(args['id'],source,str(exc))
                                result['errors'].append({'path':source,'error':str(exc)})
                        if self.stop.is_set():
                            raise KeyboardInterrupt()
                        deleted, errors, freed = manager.delete_proven_mtp_batch(args['id'],verified)
                        result['deleted'] += len(deleted)
                        result['freedBytes'] += freed
                        for source, error in errors.items():
                            manager.note_item_error(args['id'],source,error)
                            result['errors'].append({'path':source,'error':error})
                for index, source in enumerate(filesystem_paths):
                    progress(index, len(args['paths']), result['freedBytes'], source)
                    try:
                        self.guard_cleanup(catalog, args['id'], source)
                        result['freedBytes'] += manager.delete_proven_item(args['id'], source)
                        result['deleted'] += 1
                    except Exception as exc:
                        manager.note_item_error(args['id'], source, str(exc))
                        result['errors'].append({'path':source,'error':str(exc)})
                pending = len(manager.pending_items(args['id']))
                manager.set_session_status(args['id'], 'paused' if pending else 'completed')
                progress(len(args['paths']), len(args['paths']), result['freedBytes'])
            else:
                raise ValueError('Unknown disk operation.')
            state = 'paused' if self.stop.is_set() or result.get('status') == 'paused' else 'completed'
            self.finish(catalog, task_id, state, result=result)
        except KeyboardInterrupt:
            if catalog:
                if kind == 'cleanup':
                    CleanupManager(catalog).set_session_status(args['id'], 'paused')
                self.finish(catalog, task_id, 'paused')
        except Exception as exc:
            if catalog:
                self.finish(catalog, task_id, 'failed', error=str(exc))
            else:
                self.progress = {'id':task_id,'state':'failed','error':str(exc)}
        finally:
            if catalog:
                catalog.close()
            if com:
                import pythoncom
                pythoncom.CoUninitialize()

    def finish(self, catalog, task_id, state, result=None, error=None):
        with catalog.transaction() as db:
            db.execute('UPDATE zerocloud_tasks SET state=?,done=?,bytes=?,current=?,result=?,error=?,updated=? WHERE id=?',
                (state,self.progress.get('done',0),self.progress.get('bytes',0),self.progress.get('current',''),
                 json.dumps(result) if result is not None else None,error,utc_now(),task_id))
        self.progress = {**self.progress,'state':state,'error':error,'result':result}

    def resolve_device(self, args, target=False):
        if args.get('deviceId'):
            device = next((item for item in connected_devices() if item.id == args['deviceId']), None)
            if not device:
                raise ValueError('The selected device is disconnected. Connect and unlock it, then refresh devices.')
            return device
        path = absolute(args.get('path') or args.get('target'))
        if target:
            volumes = rows(self.catalog.connection.execute('SELECT * FROM volumes'))
            eligible = [item for item in volumes if item['kind']!='mtp' and within(path,Path(item['root']).resolve())]
            if eligible:
                item = max(eligible,key=lambda item:len(item['root']))
                return Device(item['id'],item['label'],item['kind'],item['root'],None,None)
            device = next((item for item in filesystem_devices() if within(path, Path(item.root).resolve())), None)
            if device:
                return device
        if not path.is_dir():
            raise ValueError('Select an existing source or restore folder.')
        disk = next((item for item in filesystem_devices() if Path(item.root).resolve()==path),None)
        if disk:
            return disk
        return Device('folder:'+hashlib.sha256(str(path).casefold().encode()).hexdigest()[:20],path.name or str(path),'fixed',str(path),None,None)

    def save_inventory(self, identifier, entries, args):
        file = self.inventories / f'{identifier}.json'
        temporary = file.with_suffix('.tmp')
        temporary.write_text(json.dumps({'args':args,'entries':[dataclasses.asdict(entry) for entry in entries]}),encoding='utf-8')
        temporary.replace(file)

    def inventory(self, identifier, catalog=None):
        if not (catalog or self.catalog).connection.execute("SELECT 1 FROM zerocloud_tasks WHERE id=? AND kind='inventory'",(identifier,)).fetchone():
            raise ValueError('Unknown source inventory.')
        payload = json.loads((self.inventories / f'{identifier}.json').read_text(encoding='utf-8'))
        return payload['args'], [SourceFile(**entry) for entry in payload['entries']]

    def selected(self, args, catalog=None):
        source, entries = self.inventory(args['inventoryId'],catalog)
        categories = set(args.get('categories', CATEGORY_LABELS))
        folders = set(args.get('folders') or [])
        majors = set(args.get('majorFolders') or [])
        omitted = set(args.get('excludedPaths') or [])
        selected = [entry for entry in entries if entry.category in categories
            and (not folders or entry.parent_folder in folders)
            and (not majors or entry.major_folder in majors) and entry.source_path not in omitted]
        return source, selected

    def inventory_summary(self, identifier, args=None, catalog=None):
        source, entries = self.selected({**(args or {}),'inventoryId':identifier},catalog)
        offset = max(0,int((args or {}).get('offset',0)))
        query = str((args or {}).get('query','')).casefold()
        files = [entry for entry in entries if query in (entry.name+' '+entry.parent_folder).casefold()]
        return {'id':identifier,'source':source,'count':len(entries),'bytes':sum(entry.size for entry in entries),
            'categories':summarize(entries,'category'),'folders':summarize(entries,'parent_folder'),
            'majorFolders':summarize(entries,'major_folder'),'files':[dataclasses.asdict(entry) for entry in files[offset:offset+100]],'filteredCount':len(files)}

    def plan_backup(self, args):
        self.available()
        source, entries = self.selected(args)
        if not entries:
            raise ValueError('Select at least one file category and source folder.')
        target = absolute(args['target'])
        if target == Path(target.anchor):
            raise ValueError('Choose a dedicated backup destination folder, not a drive root.')
        if within(target,self.paths.state) or within(self.paths.state,target):
            raise ValueError('Keep the destination vault separate from backup state.')
        if not source.get('deviceId'):
            validate_target_location([absolute(path) for path in source['paths']], target)
            if any(within(target,absolute(path)) for path in source['paths']):
                raise ValueError('Destination cannot be inside a source.')
        routes = {}
        for route in args.get('routes') or []:
            folder = str(route.get('destination','')).replace('\\','/').rstrip('/')
            if not folder or Path(folder).is_absolute() or PurePosixPath(folder).is_absolute() or ':' in folder or '..' in PurePosixPath(folder).parts:
                raise ValueError('Routing folders must be safe relative paths inside the destination.')
            routes[(route['folder'],route['category'])] = folder
        if args.get('organized',True):
            entries = categorize_destinations(entries, destination_folders=routes)
        device = self.resolve_device({'path':str(target)},target=True)
        estimate = dataclasses.asdict(estimate_selected(self.catalog, entries, 'mtp' if source.get('deviceId') else 'filesystem'))
        manager = BackupManager(self.catalog,self.paths.staging)
        selection = {key:args.get(key) for key in ['categories','folders','majorFolders','organized','routes']}
        if source.get('deviceId'):
            identifier = manager.create_mtp_job(source['deviceId'],source['paths'],[],device,target,selection,entries)
        else:
            identifier = manager.create_job([absolute(path) for path in source['paths']],device,target,entries,selection)
        return {'id':identifier,'estimate':estimate}

    def guard_cleanup(self, catalog, session_id, source):
        item = catalog.connection.execute('SELECT * FROM cleanup_items WHERE session_id=? AND source_path=? AND status=\'pending\'',(session_id,source)).fetchone()
        if not item:
            raise ValueError('The selected cleanup item is no longer pending.')
        proof = absolute(item['proof_path'] or '')
        session = CleanupManager(catalog).session(session_id)
        if session['status'] not in {'planned','paused','running'}:
            raise ValueError('This cleanup review is not active.')
        if session['source_kind'] != 'mtp':
            path = absolute(source)
            if path == proof or (path.exists() and proof.exists() and os.path.samefile(path,proof)):
                raise ValueError('Source and proof are the same file. Deletion refused.')
            if within(path,self.paths.state):
                raise ValueError('Application state cannot be cleaned.')
        else:
            # Strengthen the original engine: hash the actual device source, not just size/mtime.
            live = collect_entries(session['source_device_id'],[source])
            if len(live) != 1:
                raise ValueError('The source cannot be uniquely verified on the device.')
            staged = copy_entry_to(live[0],self.paths.staging/'cleanup-verification'/str(uuid.uuid4()))
            try:
                if sha256_file(staged) != item['sha256']:
                    raise ValueError('Device source content changed; deletion refused.')
            finally:
                staged.unlink(missing_ok=True)
        return item

    def dispatch(self, op, args):
        db = self.catalog.connection
        if op == 'overview':
            db.execute('BEGIN')
            try:
                offsets = args.get('offsets') or {}
                backup_offset = max(0,int(offsets.get('backup',0)))
                restore_offset = max(0,int(offsets.get('restore',0)))
                cleanup_offset = max(0,int(offsets.get('cleanup',0)))
                tasks = rows(db.execute('SELECT * FROM zerocloud_tasks ORDER BY updated DESC LIMIT 30'))
                for task in tasks:
                    task.pop('args',None)
                    task['result'] = json.loads(task['result']) if task['result'] else None
                return {'statePath':str(self.paths.state),'active':self.progress if self.thread and self.thread.is_alive() else None,
                    'jobs':rows(db.execute('SELECT * FROM backup_jobs ORDER BY updated_at DESC LIMIT 200 OFFSET ?',(backup_offset,))),
                    'restores':rows(db.execute('SELECT * FROM restore_jobs ORDER BY updated_at DESC LIMIT 200 OFFSET ?',(restore_offset,))),
                    'cleanups':rows(db.execute('SELECT s.*,COUNT(i.source_path) total_files,SUM(i.status=\'deleted\') deleted_files,COALESCE(SUM(i.freed_bytes),0) freed_bytes FROM cleanup_sessions s LEFT JOIN cleanup_items i ON i.session_id=s.id GROUP BY s.id ORDER BY s.updated_at DESC LIMIT 200 OFFSET ?',(cleanup_offset,))),
                    'totals':{'backup':db.execute('SELECT COUNT(*) FROM backup_jobs').fetchone()[0],
                        'restore':db.execute('SELECT COUNT(*) FROM restore_jobs').fetchone()[0],
                        'cleanup':db.execute('SELECT COUNT(*) FROM cleanup_sessions').fetchone()[0],
                        'copied':db.execute('SELECT COALESCE(SUM(copied_files),0) FROM backup_jobs').fetchone()[0],
                        'skipped':db.execute('SELECT COALESCE(SUM(skipped_files),0) FROM backup_jobs').fetchone()[0]},
                    'tasks':tasks,'volumes':rows(db.execute('SELECT * FROM volumes')),'categories':CATEGORY_LABELS,'destinations':CATEGORY_DESTINATIONS,
                    'health':[{**dataclasses.asdict(item),'recommendation':item.recommendation} for item in index_health(self.catalog)]}
            finally:
                db.rollback()
        if op == 'devices':
            return [dataclasses.asdict(device) for device in connected_devices()]
        if op == 'phoneBrowse':
            return [{'name':name,'directory':folder,'size':size} for name,folder,size in list_children(args['deviceId'],str(args.get('path','')))]
        if op == 'inventoryStart':
            return self.start('inventory',args)
        if op == 'inventoryGet':
            filters = {key:value for key,value in args.items() if key!='id'}
            return self.inventory_summary(args['id'],filters)
        if op == 'backupPlan':
            return self.plan_backup(args)
        if op == 'backupRun':
            if not db.execute('SELECT 1 FROM backup_jobs WHERE id=?',(args['id'],)).fetchone():
                raise ValueError('Unknown backup job.')
            return self.start('backup',args)
        if op == 'indexStart':
            return self.start('index',args)
        if op == 'taskResume':
            task = db.execute('SELECT * FROM zerocloud_tasks WHERE id=?',(args['id'],)).fetchone()
            if not task or task['kind'] == 'cleanup':
                raise ValueError('Cleanup needs a fresh selection and confirmation; it never resumes deletion automatically.')
            return self.start(task['kind'],{**json.loads(task['args']),'taskId':task['id']})
        if op == 'pause':
            self.stop.set()
            return True
        if op == 'details':
            kind = args.get('kind','backup')
            table, key = {'backup':('backup_items','job_id'),'restore':('restore_items','restore_id'),'cleanup':('cleanup_items','session_id')}[kind]
            offset = max(0,int(args.get('offset',0)))
            data = rows(db.execute(f'SELECT * FROM {table} WHERE {key}=? ORDER BY rowid LIMIT 100 OFFSET ?',(args['id'],offset)))
            return {'items':data,'total':db.execute(f'SELECT COUNT(*) FROM {table} WHERE {key}=?',(args['id'],)).fetchone()[0]}
        if op == 'restorePlan':
            self.available()
            backup = db.execute('SELECT * FROM backup_jobs WHERE id=?',(args['id'],)).fetchone()
            if not backup:
                raise ValueError('Unknown backup job.')
            if args.get('original',True):
                if backup['source_kind'] == 'mtp':
                    device = self.resolve_device({'deviceId':json.loads(backup['source_ref'])['device_id']})
                else:
                    path = absolute(json.loads(backup['source_ref'])[0])
                    device = Device('restore-original','Original locations','fixed',path.anchor,None,None)
            else:
                device = self.resolve_device(args)
            identifier = RestoreManager(self.catalog,self.paths.staging).create_job(args['id'],device,set(args.get('categories') or CATEGORY_LABELS),deleted_only=bool(args.get('deletedOnly',True)))
            return {'id':identifier,'preview':rows(RestoreManager(self.catalog,self.paths.staging).preview(identifier))}
        if op == 'restoreRun':
            if not db.execute('SELECT 1 FROM restore_jobs WHERE id=?',(args['id'],)).fetchone():
                raise ValueError('Unknown restore job.')
            return self.start('restore',args)
        if op == 'cleanupPlan':
            self.available()
            manager = CleanupManager(self.catalog)
            if args.get('mode') == 'backup':
                identifier = manager.create_from_job(args['id'])
            elif args.get('mode') == 'duplicates':
                identifier = manager.create_duplicates(args['volumeId'],min(1000,int(args.get('limit',200))))
            elif args.get('deviceId'):
                source, entries = self.inventory(args['inventoryId'])
                identifier = manager.create_largest_mtp(args['deviceId'],entries,int(args.get('threshold',100))*1024**2,min(1000,int(args.get('limit',200))))
            else:
                identifier = manager.create_largest(args['volumeId'],int(args.get('threshold',100))*1024**2,min(1000,int(args.get('limit',200))))
            return {'id':identifier}
        if op == 'cleanupIgnore':
            self.available()
            manager = CleanupManager(self.catalog)
            if args.get('cancel'):
                manager.cancel_session(args['id'])
            elif args.get('folder'):
                manager.ignore_folder(args['id'],args['paths'][0])
            else:
                for source in args['paths']:
                    item = db.execute('SELECT status FROM cleanup_items WHERE session_id=? AND source_path=?',(args['id'],source)).fetchone()
                    if item and item['status'] == 'pending':
                        manager.mark_item(args['id'],source,'ignored','keep')
            return True
        if op == 'cleanupBackup':
            self.available()
            session = CleanupManager(self.catalog).session(args['id'])
            if session['source_kind'] == 'mtp':
                source = {'deviceId':session['source_device_id'],'paths':args['paths']}
            else:
                source = {'paths':args['paths']}
            return self.start('inventory',source)
        if op == 'cleanupAttachProof':
            self.available()
            manager = CleanupManager(self.catalog)
            for source in args['paths']:
                manager.attach_latest_proof(args['id'],source)
            return True
        if op == 'cleanupPreview':
            self.available()
            if CleanupManager(self.catalog).session(args['id'])['status'] not in {'planned','paused','running'}:
                raise ValueError('This review was dismissed or completed. Create a new review queue.')
            requested = list(dict.fromkeys(args.get('paths') or []))
            if not requested or len(requested)>500:
                raise ValueError('Select between 1 and 500 reviewed files.')
            items = []
            for source in requested:
                item = db.execute("SELECT * FROM cleanup_items WHERE session_id=? AND source_path=? AND status='pending'",(args['id'],source)).fetchone()
                if not item or not item['sha256'] or not item['proof_path']:
                    raise ValueError('Every selected file needs a recorded backup or duplicate proof. Back it up first.')
                items.append(dict(item))
            token = secrets.token_urlsafe(32)
            self.challenges[token] = (time.monotonic()+120,args['id'],requested)
            return {'challenge':token,'items':items,'bytes':sum(item['size'] for item in items)}
        if op == 'cleanupRun':
            self.available()
            challenge = self.challenges.pop(args.get('challenge',''),None)
            if not challenge or challenge[0]<time.monotonic() or args.get('confirmation')!='DELETE':
                raise ValueError('Cleanup confirmation expired. Review the selected files again.')
            return self.start('cleanup',{'id':challenge[1],'paths':challenge[2]})
        if op == 'search':
            results = search_catalog(self.catalog,str(args.get('query','')),limit=500,categories=set(args.get('categories') or []) or None,
                location=str(args.get('location','')),modified_from=date.fromisoformat(args['from']) if args.get('from') else None,
                modified_to=date.fromisoformat(args['to']) if args.get('to') else None)
            return [dataclasses.asdict(item) for item in results]
        if op == 'resolveStored':
            location = str(args['location'])
            known = db.execute('SELECT 1 FROM backup_items WHERE target_path=? OR duplicate_of=?',(location,location)).fetchone()
            if not known:
                for volume in db.execute('SELECT * FROM volumes'):
                    try:
                        relative = absolute(location).relative_to(absolute(volume['root'])).as_posix()
                    except ValueError:
                        continue
                    if db.execute('SELECT 1 FROM files WHERE volume_id=? AND relative_path=?',(volume['id'],relative)).fetchone():
                        known = True
                        break
            if not known:
                raise ValueError('Only catalogued backup/search files can be opened.')
            return str(absolute(location))
        if op == 'stateBackup':
            self.available()
            return {'path':str(self.catalog.create_backup('zerocloud-manual'))}
        if op == 'shutdown':
            self.stop.set()
            if self.thread:
                self.thread.join(12)
            if self.thread and self.thread.is_alive():
                raise RuntimeError('A file operation is finishing. Wait before moving state or closing the app.')
            self.catalog.close()
            self.state_lock.close()
            self.closed = True
            return True
        raise ValueError('Unsupported backup operation.')


def main():
    service = BackupService(sys.argv[1])
    if os.name == 'nt':
        import pythoncom
        pythoncom.CoInitialize()
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            result = service.dispatch(request['op'],request.get('args') or {})
            print(json.dumps({'id':request['id'],'result':result}),flush=True)
            if service.closed:
                break
        except Exception as exc:
            print(json.dumps({'id':request.get('id'),'error':str(exc)}),flush=True)


if __name__ == '__main__':
    main()
