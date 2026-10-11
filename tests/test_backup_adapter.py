import json, os, sqlite3, tempfile, time, unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from unittest.mock import patch
from backup_service import BackupService
from disk_backup.devices import Device
from disk_backup.hashing import sha256_file, publish_without_overwrite
from disk_backup.indexer import index_volume


class AdapterTests(unittest.TestCase):
    def setUp(self):
        root=Path.cwd()/'work'/'backup-adapter-temp'
        root.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=root)
        self.root=Path(self.temporary.name)
        self.source=self.root/'source';self.source.mkdir()
        self.target=self.root/'vault';self.target.mkdir()
        self.service=BackupService(self.root/'state')

    def tearDown(self):
        self.service.dispatch('shutdown',{})
        self.temporary.cleanup()

    def wait(self):
        self.service.thread.join(30)
        self.assertFalse(self.service.thread.is_alive())
        return self.service.dispatch('overview',{})['tasks'][0]

    def inventory(self):
        identifier=self.service.dispatch('inventoryStart',{'paths':[str(self.source)]})['id']
        task=self.wait();self.assertEqual(task['state'],'completed',task.get('error'))
        return identifier

    def make_backup(self, inventory, **extra):
        identifier=self.service.dispatch('backupPlan',{'inventoryId':inventory,'target':str(self.target),**extra})['id']
        self.service.dispatch('backupRun',{'id':identifier})
        self.assertEqual(self.wait()['state'],'completed')
        return identifier

    def test_round_trip_dedup_routes_search_and_confirmed_cleanup(self):
        (self.source/'photo.jpg').write_bytes(b'photo contents')
        (self.source/'renamed.jpg').write_bytes(b'photo contents')
        (self.source/'call.mp3').write_bytes(b'call contents')
        inventory=self.inventory()
        folder=self.service.dispatch('inventoryGet',{'id':inventory})['folders'][0][0]
        job=self.make_backup(inventory,routes=[{'folder':folder,'category':'audio','destination':'Calls'}])
        stored=self.service.dispatch('overview',{})['jobs'][0]
        self.assertEqual(stored['copied_files'],2);self.assertEqual(stored['skipped_files'],1)
        self.assertTrue((self.target/'Calls'/'call.mp3').exists())
        self.assertEqual(len(list(self.source.iterdir())),3)
        matches=self.service.dispatch('search',{'query':'call','categories':['audio']})
        self.assertTrue(matches);self.assertEqual(matches[0]['original_source'],str(self.source/'call.mp3'))
        session=self.service.dispatch('cleanupPlan',{'mode':'backup','id':job})['id']
        selection=[str(self.source/'call.mp3')]
        with self.assertRaises(ValueError):self.service.dispatch('cleanupRun',{'confirmation':'DELETE'})
        preview=self.service.dispatch('cleanupPreview',{'id':session,'paths':selection})
        self.service.dispatch('cleanupRun',{'challenge':preview['challenge'],'confirmation':'DELETE'})
        task=self.wait();self.assertEqual(task['result']['deleted'],1)
        self.assertFalse((self.source/'call.mp3').exists())
        self.assertTrue((self.target/'Calls'/'call.mp3').exists())
        with self.assertRaises(ValueError):self.service.dispatch('cleanupRun',{'challenge':preview['challenge'],'confirmation':'DELETE'})
        restore=self.service.dispatch('restorePlan',{'id':job,'categories':['audio'],'original':True,'deletedOnly':True})['id']
        self.service.dispatch('restoreRun',{'id':restore});self.wait()
        self.assertEqual((self.source/'call.mp3').read_bytes(),b'call contents')

    def test_changed_source_and_missing_proof_are_kept(self):
        file=self.source/'document.txt';file.write_bytes(b'original document')
        job=self.make_backup(self.inventory())
        session=self.service.dispatch('cleanupPlan',{'mode':'backup','id':job})['id']
        file.write_bytes(b'changed document!')
        preview=self.service.dispatch('cleanupPreview',{'id':session,'paths':[str(file)]})
        self.service.dispatch('cleanupRun',{'challenge':preview['challenge'],'confirmation':'DELETE'})
        task=self.wait();self.assertEqual(task['result']['deleted'],0);self.assertTrue(task['result']['errors'])
        self.assertEqual(file.read_bytes(),b'changed document!')
        proof=self.service.catalog.connection.execute('SELECT proof_path FROM cleanup_items WHERE session_id=?',(session,)).fetchone()[0]
        Path(proof).unlink()
        preview=self.service.dispatch('cleanupPreview',{'id':session,'paths':[str(file)]})
        self.service.dispatch('cleanupRun',{'challenge':preview['challenge'],'confirmation':'DELETE'});self.wait()
        self.assertTrue(file.exists())

    def test_restore_conflict_is_not_overwritten(self):
        file=self.source/'document.txt';file.write_bytes(b'original')
        job=self.make_backup(self.inventory())
        file.write_bytes(b'different live file')
        restore=self.service.dispatch('restorePlan',{'id':job,'original':True,'deletedOnly':False})['id']
        self.service.dispatch('restoreRun',{'id':restore});self.wait()
        self.assertEqual(file.read_bytes(),b'different live file')
        self.assertEqual(self.service.dispatch('overview',{})['restores'][0]['conflict_files'],1)

    def test_pause_resume_and_restart_preserve_job(self):
        for i in range(12):(self.source/f'{i}.txt').write_text(str(i)*1000)
        inventory=self.inventory()
        job=self.service.dispatch('backupPlan',{'inventoryId':inventory,'target':str(self.target)})['id']
        from disk_backup import backup as core
        original=core.copy_and_hash
        def slower(*args,**kwargs):
            time.sleep(.08);return original(*args,**kwargs)
        with patch.object(core,'copy_and_hash',slower):
            self.service.dispatch('backupRun',{'id':job});time.sleep(.12);self.service.dispatch('pause',{});self.wait()
        jobs=self.service.dispatch('overview',{})['jobs'];self.assertEqual(jobs[0]['status'],'paused')
        self.assertLess(jobs[0]['copied_files'],12)
        self.service.dispatch('shutdown',{});self.service=BackupService(self.root/'state')
        self.service.dispatch('backupRun',{'id':job});self.wait()
        self.assertEqual(self.service.dispatch('overview',{})['jobs'][0]['copied_files'],12)
        self.assertEqual(len(list(self.target.rglob('*.txt'))),12)

    def test_route_escape_and_nested_destination_are_rejected(self):
        (self.source/'file.txt').write_text('content')
        inventory=self.inventory()
        for route in ['../escape','/absolute','C:/outside']:
            with self.assertRaises(ValueError):self.service.dispatch('backupPlan',{'inventoryId':inventory,'target':str(self.target),'routes':[{'folder':'source','category':'documents','destination':route}]})
        with self.assertRaises(ValueError):self.service.dispatch('backupPlan',{'inventoryId':inventory,'target':str(self.source/'inside')})
        with self.assertRaises(ValueError):self.service.dispatch('resolveStored',{'location':str(self.source/'file.txt')})

    def test_publication_refuses_racing_target(self):
        target=self.target/'file.txt';target.write_bytes(b'keep me')
        partial=self.target/'owned.partial';partial.write_bytes(b'replacement')
        with self.assertRaises(FileExistsError):publish_without_overwrite(partial,target)
        self.assertEqual(target.read_bytes(),b'keep me')

    def test_indexed_source_is_not_used_as_its_own_backup(self):
        file=self.source/'file.txt';file.write_bytes(b'content')
        device=Device('shared','Shared disk','fixed',str(self.root),None,None)
        index_volume(self.service.catalog,device,excluded_roots=(self.root/'state',))
        job=self.make_backup(self.inventory())
        item=self.service.catalog.connection.execute('SELECT * FROM backup_items WHERE job_id=?',(job,)).fetchone()
        self.assertEqual(item['status'],'copied');self.assertNotEqual(item['target_path'],str(file))
        self.assertEqual(sha256_file(Path(item['target_path'])),sha256_file(file))

    def test_state_cannot_be_opened_by_two_writers(self):
        if os.name!='nt':self.skipTest('Windows state lock')
        with self.assertRaises(RuntimeError):BackupService(self.root/'state')

    def test_phone_cleanup_hashes_changed_source_even_when_metadata_matches(self):
        from disk_backup.cleanup import CleanupManager
        proof=self.target/'proof.jpg';proof.write_bytes(b'old photo')
        staged=self.root/'staged.jpg';staged.write_bytes(b'new photo')
        manager=CleanupManager(self.service.catalog)
        identifier=manager._create_session('mtp:fake','mtp','fake','backed_up',0,1,
            [('DCIM/photo.jpg','photo.jpg',9,0,'.jpg','images',sha256_file(proof),str(proof),1)],source_device_id='mtp:fake')
        with patch('backup_service.collect_entries',return_value=[object()]),patch('backup_service.copy_entry_to',return_value=staged):
            with self.assertRaisesRegex(ValueError,'content changed'):
                self.service.guard_cleanup(self.service.catalog,identifier,'DCIM/photo.jpg')
        self.assertTrue(proof.exists())
        self.assertEqual(manager.pending_items(identifier)[0]['status'],'pending')

    def test_dismissed_cleanup_cannot_get_new_delete_challenge(self):
        file=self.source/'file.txt';file.write_bytes(b'keep')
        job=self.make_backup(self.inventory())
        session=self.service.dispatch('cleanupPlan',{'mode':'backup','id':job})['id']
        self.service.dispatch('cleanupIgnore',{'id':session,'cancel':True})
        with self.assertRaises(ValueError):self.service.dispatch('cleanupPreview',{'id':session,'paths':[str(file)]})
        self.assertTrue(file.exists())

    def test_history_offsets_reach_older_sessions_and_totals_are_global(self):
        file=self.source/'file.txt';file.write_bytes(b'content')
        inventory=self.inventory()
        for i in range(205):self.service.dispatch('backupPlan',{'inventoryId':inventory,'target':str(self.target)})
        first=self.service.dispatch('overview',{})
        second=self.service.dispatch('overview',{'offsets':{'backup':200}})
        self.assertEqual(first['totals']['backup'],205);self.assertEqual(len(first['jobs']),200);self.assertEqual(len(second['jobs']),5)
        self.assertFalse(set(job['id'] for job in first['jobs'])&set(job['id'] for job in second['jobs']))


if __name__=='__main__':unittest.main()
