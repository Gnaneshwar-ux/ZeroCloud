import os,sys,time,subprocess
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from portable_engine import Engine
from capture_dates import read_capture
from PIL import Image
import imageio_ffmpeg
work=Path(sys.argv[2]);originals=work/'originals';originals.mkdir(parents=True,exist_ok=True)
for name,date in [('taken-2018.jpg','2018:04:12 09:30:00'),('taken-2021.jpg','2021:11:06 18:15:00')]:
    exif=Image.Exif();exif[36867]=date;exif[306]='2026:10:11 12:00:00'
    Image.new('RGB',(800,600),(80,140,180)).save(originals/name,exif=exif)
    os.utime(originals/name,(1791693156,1791693156))
Image.new('RGB',(600,800),(150,100,180)).save(originals/'no-date.png')
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-f','lavfi','-i','color=c=blue:s=320x240:r=24','-t','1','-metadata','creation_time=2019-07-05T20:30:00Z','-c:v','libx264','-threads','1',str(originals/'taken-2019.mp4')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
assert read_capture(originals/'taken-2018.jpg','image')[0].startswith('2018-04-12')
assert read_capture(originals/'no-date.png','image')[0] is None
assert read_capture(originals/'taken-2019.mp4','video')[0].startswith('2019-07-05')
engine=Engine(work/'catalog')
try:
    engine.memories.dispatch('control',{'enabled':False,'paused':True})
    engine.scan({'path':str(originals)})
    end=time.time()+30
    while time.time()<end and engine.capture_dates.changes({})['done']<4:time.sleep(.2)
    assert engine.capture_dates.changes({})['done']==4
    rows={r['name']:r for r in engine.load({})['items']}
    assert rows['taken-2018.jpg']['captureDate']=='2018-04-12'
    assert rows['taken-2021.jpg']['captureDate']=='2021-11-06'
    assert rows['taken-2019.mp4']['captureDate']=='2019-07-05'
    assert rows['no-date.png']['captureDate'] is None
    assert rows['no-date.png']['date']=='Unknown capture date'
    folder_id=engine.load({})['folders'][0]['id']
    cache=work/'portable';cache.mkdir(exist_ok=True)
    engine.attach_cache(folder_id,cache)
    engine.export_caches()
    import sqlite3
    saved=sqlite3.connect(cache/'catalog.sqlite')
    assert saved.execute('SELECT count(*) FROM capture_dates WHERE captured_at IS NOT NULL').fetchone()[0]==3
    saved.close()
    engine.dispatch('indexControl',{'paused':True})
    print('PASS: original EXIF dates override copied-file and EXIF modification dates; video creation date, unknown-date handling, persistent catalog and global pause.')
finally:engine.dispatch('shutdown',{})
