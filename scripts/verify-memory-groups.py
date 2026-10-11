import sys
sys.path.insert(0,sys.argv[1])
from portable_engine import Engine
engine=Engine(sys.argv[2])
try:
    memory=engine.memories
    memory.dispatch('control',{'enabled':False,'paused':True})
    with engine.connection() as db:
        rows=db.execute('SELECT id FROM media ORDER BY id').fetchall()[:4]
        db.execute("INSERT OR IGNORE INTO people VALUES('test-person','Test person','[]')")
        for i,row in enumerate(rows):
            db.execute('INSERT OR REPLACE INTO faces VALUES(?,?,?,?,?,?)',(f'test-face-{i}',row['id'],'test-person','[]','[]','unused.jpg'))
            db.execute('INSERT OR REPLACE INTO locations VALUES(?,?,?,?,?)',(row['id'],'test',17.38,78.48,''))
    candidates=memory.candidates()
    assert {'people','places','trips'} <= {c['category'] for c in candidates}
    overview=memory.dispatch('discover',{})
    assert any('people' in r['category'] for r in overview['items'])
    assert any('places' in r['category'] for r in overview['items'])
    assert len(overview['items'])==len(memory.dispatch('discover',{})['items'])
    print('PASS: people, GPS places and trip-session suggestions; shared content retains category membership without duplicate reels.')
finally: engine.dispatch('shutdown',{})
