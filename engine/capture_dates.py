"""Read capture metadata without treating file modification dates as camera dates."""
import datetime
import re
import os
import logging
import time
import subprocess
import threading
from pathlib import Path

def normalize(value):
    if not value: return None
    text=str(value).strip().strip('\x00')
    text=re.sub(r'^(\d{4}):(\d{2}):(\d{2})',r'\1-\2-\3',text).replace(' ','T',1)
    try:
        date=datetime.datetime.fromisoformat(text.replace('Z','+00:00'))
        if not 1900<=date.year<=datetime.datetime.now().year+1: return None
        return date.isoformat()
    except ValueError: return None

def read_capture(source,kind):
    if kind=='video':
        import imageio_ffmpeg
        result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-protocol_whitelist','file,pipe','-i',str(source)],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=12,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for text in re.findall(r'^\s*creation_time\s*:\s*(.+)$',result.stderr,re.M):
            parsed=normalize(text)
            if parsed and int(parsed[:4])>=1980:return parsed,'video creation metadata'
        return None,'unknown'
    from PIL import Image
    try:
        with Image.open(source) as image:
            exif=image.getexif(); tags=dict(exif)
            try: tags.update(exif.get_ifd(34665))
            except (ValueError,KeyError,TypeError): pass
            for key,label,offset in [(36867,'EXIF DateTimeOriginal',36881),(36868,'EXIF DateTimeDigitized',36882)]:
                parsed=normalize(tags.get(key))
                if parsed:
                    zone=str(tags.get(offset,'')).strip()
                    if re.fullmatch(r'[+-]\d{2}:\d{2}',zone): parsed=normalize(parsed+zone) or parsed
                    return parsed,label
    except (OSError,ValueError): pass
    import exifread
    logging.getLogger('exifread').setLevel(logging.ERROR)
    with open(source,'rb') as stream: tags=exifread.process_file(stream,details=False,extract_thumbnail=False)
    for key,label in [('EXIF DateTimeOriginal','EXIF DateTimeOriginal'),('EXIF DateTimeDigitized','EXIF DateTimeDigitized')]:
        parsed=normalize(tags.get(key))
        if parsed:return parsed,label
    return None,'unknown'

class CaptureDates:
    def __init__(self,engine):
        self.engine=engine
        self.state='Waiting';self.current=''
        with engine.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS capture_dates(media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,version TEXT,captured_at TEXT,source TEXT,error TEXT,revision INTEGER);
            CREATE INDEX IF NOT EXISTS capture_revision ON capture_dates(revision);''')
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def changes(self,args):
        with self.engine.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT c.* FROM capture_dates c JOIN media m ON m.id=c.media_id WHERE m.present=1 AND c.version=m.fingerprint AND c.revision>? ORDER BY revision LIMIT 250',(int(args.get('since',0)),))]
            total=db.execute("SELECT count(*) FROM media WHERE present=1 AND kind IN ('image','raw','video')").fetchone()[0]
            done=db.execute('SELECT count(*) FROM capture_dates c JOIN media m ON m.id=c.media_id WHERE m.present=1 AND c.version=m.fingerprint').fetchone()[0]
            dated=db.execute('SELECT count(*) FROM capture_dates c JOIN media m ON m.id=c.media_id WHERE m.present=1 AND c.version=m.fingerprint AND c.captured_at IS NOT NULL').fetchone()[0]
        return {'updates':rows,'revision':rows[-1]['revision'] if rows else int(args.get('since',0)),'state':self.state,'current':self.current,'done':done,'total':total,'dated':dated}

    def loop(self):
        while not self.engine.closing:
            if self.engine.paused or self.engine.all_paused:
                self.state='Paused';time.sleep(.5);continue
            with self.engine.connection() as db:
                row=db.execute("SELECT m.* FROM media m LEFT JOIN capture_dates c ON c.media_id=m.id WHERE m.present=1 AND m.kind IN ('image','raw','video') AND (c.version IS NULL OR c.version!=m.fingerprint) ORDER BY m.kind='video',m.id LIMIT 1").fetchone()
            if not row:
                self.state='Complete';self.current='';time.sleep(1);continue
            self.state='Reading capture dates';self.current=Path(row['path']).name
            captured=None;label='unknown';error=None
            try:
                with self.engine.connection() as db: source=self.engine.original(db,row['id'])
                captured,label=read_capture(source,row['kind'])
            except Exception as reason: error=str(reason)
            with self.engine.lock,self.engine.connection() as db:
                fresh=db.execute('SELECT fingerprint FROM media WHERE id=?',(row['id'],)).fetchone()
                if fresh and fresh[0]==row['fingerprint']:
                    revision=db.execute('SELECT coalesce(max(revision),0)+1 FROM capture_dates').fetchone()[0]
                    db.execute('INSERT OR REPLACE INTO capture_dates VALUES(?,?,?,?,?,?)',(row['id'],row['fingerprint'],captured,label,error,revision))
            self.engine.cache_dirty=True
            time.sleep(.025)
