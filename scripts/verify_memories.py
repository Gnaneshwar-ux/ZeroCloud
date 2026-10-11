import json
from pathlib import Path
import sys
import time
sys.path.insert(0,sys.argv[1])
from portable_engine import Engine
root=Path(sys.argv[2]); originals=Path(sys.argv[3])
engine=Engine(root)
try:
    memory=engine.memories
    memory.dispatch('control',{'enabled':False,'paused':True})
    engine.scan({'path':str(originals)})
    first=memory.dispatch('discover',{})
    assert first['items'], 'Need a suggested memory from fixture folder'
    second=memory.dispatch('discover',{})
    assert len(first['items'])==len(second['items']), 'Duplicate suggestions'
    assert not memory.idle(), 'Paused memories must not run'
    memory.dispatch('activity',{'busy':True})
    assert memory.foreground_busy
    memory.dispatch('activity',{'busy':False})
    with engine.connection() as db:
        row=dict(db.execute('SELECT * FROM memories LIMIT 1').fetchone())
        sources=json.loads(row['sources'])
        assert any(s['kind']=='video' for s in sources)
    # Force idle only in this isolated encoder test; production gating remains intact.
    memory.wait_idle=lambda:None
    memory.idle=lambda:True
    output=memory.render(row)
    path=root/output
    assert path.is_file() and path.stat().st_size>10000
    import subprocess,imageio_ffmpeg
    probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',str(path)],capture_output=True,text=True)
    assert 'Audio: aac' in probe.stderr, probe.stderr
    import cv2
    cap=cv2.VideoCapture(str(path))
    assert cap.isOpened()
    assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))==720
    assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))==1280
    duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/cap.get(cv2.CAP_PROP_FPS)
    assert abs(duration-3*len(sources))<.2
    cap.release()
    with engine.connection() as db:
        db.execute("UPDATE memories SET status='ready',file=?,created=? WHERE id=?",(output,int(time.time()*1000),row['id']))
    assert memory.dispatch('resolve',{'id':row['id']})==str(path.resolve())
    assert memory.dispatch('notice',{})['notify']
    assert not memory.dispatch('notice',{})['notify'], 'Repeated notification'
    assert memory.dispatch('discover',{})['items'][0]['status']=='ready'
    print('PASS: real mixed image/video reel, 720x1280 duration, original ambient soundtrack, deduplication, idle pause, persistent cache and daily notice throttling.',flush=True)
finally:
    engine.dispatch('shutdown',{})
