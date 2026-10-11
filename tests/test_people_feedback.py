"""Fast input is durable; costly face grouping is handled by a worker."""
import contextlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from people_organizer import PeopleOrganizer


class FeedbackEngine:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.closing = False
        with self.connection() as db:
            db.executescript('CREATE TABLE people(id TEXT PRIMARY KEY);'
                             'CREATE TABLE person_alias(old_id TEXT,person_id TEXT);'
                             'CREATE TABLE applied(first TEXT,second TEXT,same INTEGER);')
            db.executemany('INSERT INTO people VALUES(?)', [('a',), ('b',)])

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / 'catalog.sqlite', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def dispatch(self, op, args):
        if op != 'reviewPerson':
            raise AssertionError(op)
        with self.connection() as db:
            db.execute('INSERT INTO applied VALUES(?,?,?)',
                       (args['id'], args['candidate'], args['same']))


class FeedbackTests(unittest.TestCase):
    def test_save_before_background_processing_and_recover_interrupted_work(self):
        with tempfile.TemporaryDirectory() as root:
            engine = FeedbackEngine(root)
            organizer = PeopleOrganizer(engine)
            saved = organizer.enqueue({'id': 'a', 'candidate': 'b', 'same': False})
            with engine.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM applied').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT state FROM person_feedback').fetchone()[0], 'queued')
                db.execute("UPDATE person_feedback SET state='working'")
            resumed = PeopleOrganizer(engine)
            resumed.start = lambda **kwargs: None
            resumed.start_feedback()
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with engine.connection() as db:
                        state = db.execute('SELECT state FROM person_feedback WHERE id=?', (saved['id'],)).fetchone()[0]
                    if state == 'complete':
                        break
                    time.sleep(.05)
                self.assertEqual(state, 'complete')
                with engine.connection() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM applied').fetchone()[0], 1)
            finally:
                engine.closing = True
                resumed.feedback_event.set()
                time.sleep(.1)

    def test_invalid_feedback_is_not_acknowledged(self):
        with tempfile.TemporaryDirectory() as root:
            organizer = PeopleOrganizer(FeedbackEngine(root))
            for args in [{'id':'a','candidate':'a','same':True},
                         {'id':'a','candidate':'b','same':'yes'},
                         {'id':'a','candidate':'missing','same':False}]:
                with self.assertRaises(ValueError):
                    organizer.enqueue(args)


if __name__ == '__main__':
    unittest.main()
