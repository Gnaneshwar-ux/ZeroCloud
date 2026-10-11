import base64
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from portable_engine import Engine


class FolderAdditionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.source=self.root/'originals';self.source.mkdir()
        (self.source/'photo.jpg').write_bytes(b'fixture original, never modified')
        self.engine=Engine(self.root/'library')
        self.engine.dispatch('indexControl',{'paused':True})

    def tearDown(self):
        self.engine.dispatch('shutdown',{})
        self.temp.cleanup()

    def wait_scan(self):
        deadline=time.monotonic()+10
        while self.engine.scan_state['state'] in ['scanning','paused']:
            if time.monotonic()>deadline: self.fail(str(self.engine.scan_state))
            time.sleep(.02)
        self.assertEqual(self.engine.scan_state['state'],'complete',self.engine.scan_state)

    def test_same_main_cache_mount_reuses_catalog_and_ids(self):
        first=self.engine.mount_cache(self.source,self.engine.root)
        item=self.engine.load({})['items'][0]
        self.engine.dispatch('favorite',{'id':item['id'],'favorite':True})
        self.engine.preview_save({'id':item['id'],'version':item['version'],'data':base64.b64encode(b'tiny derivative').decode()})
        second=self.engine.mount_cache(self.source,self.engine.root)
        self.assertEqual(first['folderId'],second['folderId'])
        saved=self.engine.load({})['items'][0]
        self.assertTrue(saved['favorite']);self.assertTrue(saved['thumbnailReady'])
        self.assertFalse((self.engine.root/'vela-folder-cache.json').exists())

    def test_add_main_cache_runs_in_background_and_can_pause_resume(self):
        started=time.monotonic()
        self.assertTrue(self.engine.start_scan({'path':str(self.source),'cachePath':str(self.engine.root)})['backgroundScan'])
        self.assertLess(time.monotonic()-started,1)
        self.assertEqual(self.engine.scan_state['state'],'paused')
        self.engine.dispatch('indexControl',{'paused':False});self.wait_scan()
        self.assertEqual(len(self.engine.load({})['items']),1)
        self.assertFalse((self.engine.root/'vela-folder-cache.json').exists())

    def test_invalid_nonempty_cache_does_not_register_source(self):
        cache=self.root/'occupied';cache.mkdir();(cache/'keep.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError,'empty cache'):
            self.engine.start_scan({'path':str(self.source),'cachePath':str(cache)})
        self.assertEqual(self.engine.load({})['folders'],[])
        self.assertEqual((cache/'keep.txt').read_text(),'keep')
        with self.engine.connection() as db:
            self.assertIsNone(db.execute('SELECT * FROM settings WHERE key="diskScan"').fetchone())

    def test_distinct_portable_cache_can_return_to_shared_library(self):
        cache=self.root/'portable'
        result=self.engine.mount_cache(self.source,cache)
        item=self.engine.load({})['items'][0]
        self.engine.preview_save({'id':item['id'],'version':item['version'],'data':base64.b64encode(b'tiny derivative').decode()})
        self.engine.attach_cache(result['folderId'],self.engine.root)
        self.assertEqual(self.engine.preview_get({'id':item['id'],'version':item['version'],'type':'thumbnail'})['data'],base64.b64encode(b'tiny derivative').decode())
        self.assertIsNone(self.engine.load({})['folders'][0]['cacheLocation'])
        self.assertTrue((cache/'vela-folder-cache.json').is_file())

    def test_new_portable_cache_still_mounts_on_another_profile(self):
        cache=self.root/'portable'
        self.engine.mount_cache(self.source,cache)
        original=self.engine.load({})['items'][0]
        self.engine.dispatch('favorite',{'id':original['id'],'favorite':True})
        self.engine.export_caches()
        other=Engine(self.root/'other-profile')
        try:
            other.mount_cache(self.source,cache)
            restored=other.load({})['items'][0]
            self.assertEqual(original['id'],restored['id']);self.assertTrue(restored['favorite'])
        finally: other.dispatch('shutdown',{})


if __name__=='__main__':unittest.main()
