"""Ephemeral, lossless view transport: opaque URLs, never image data in JSON."""
import collections
from pathlib import Path
import struct
import tempfile
import threading
import uuid
import zlib


def chunk(stream,kind,data):
    stream.write(struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff))


def write_png(image,stream,profile=None):
    """Fixed filter 0 is fast and pixel-exact; preserve alpha and ICC profiles."""
    if image.mode not in ('RGB','RGBA'):raise ValueError('View requires RGB or RGBA')
    width,height=image.size;channels=len(image.getbands())
    stream.write(b'\x89PNG\r\n\x1a\n')
    chunk(stream,b'IHDR',struct.pack('!2I5B',width,height,8,6 if channels==4 else 2,0,0,0))
    if profile:chunk(stream,b'iCCP',b'ICC\0\0'+zlib.compress(profile))
    compressor=zlib.compressobj(level=0)
    rowbytes=width*channels
    for top in range(0,height,64):
        block=image.crop((0,top,width,min(top+64,height)))
        pixels=block.tobytes();block.close()
        filtered=bytearray((rowbytes+1)*(len(pixels)//rowbytes))
        for row in range(len(pixels)//rowbytes):filtered[row*(rowbytes+1)+1:(row+1)*(rowbytes+1)]=pixels[row*rowbytes:(row+1)*rowbytes]
        encoded=compressor.compress(filtered)
        if encoded:chunk(stream,b'IDAT',encoded)
    tail=compressor.flush()
    if tail:chunk(stream,b'IDAT',tail)
    chunk(stream,b'IEND',b'')


class DecodedViews:
    def __init__(self):
        self.temp=tempfile.TemporaryDirectory(prefix='zerocloud-view-')
        self.root=Path(self.temp.name)
        self.entries=collections.OrderedDict();self.lock=threading.RLock();self.jobs={}
        self.retired=set()
        self.closed=False

    def get(self,key):
        with self.lock:
            result=self.entries.get(key)
            if result and result['path'].is_file():
                self.entries.move_to_end(key)
                return {'url':'vela://decoded/'+result['token'],'mime':'image/png'}
        return None

    def produce(self,key,load):
        # Deduplicate RUNNING decodes, not the unrelated queue ahead of them.
        with self.lock:
            if self.closed:raise ValueError('Viewer is closing')
            saved=self.get(key)
            if saved:return saved
            event=self.jobs.get(key)
            owner=event is None
            if owner:event=threading.Event();self.jobs[key]=event
        if not owner:
            if not event.wait(60):raise TimeoutError('Image decoding timed out')
            saved=self.get(key)
            if saved:return saved
            raise ValueError('Original image conversion failed')
        token=uuid.uuid4().hex;file=self.root/(token+'.png')
        try:
            image,profile=load()
            try:
                with file.open('wb') as stream:write_png(image,stream,profile)
            finally:image.close()
            with self.lock:
                self.entries[key]={'path':file,'token':token,'bytes':file.stat().st_size}
                # One very large current image is allowed; never retain a set
                # of unbounded full-size copies. Files are outside portable cache.
                while len(self.entries)>3 or (len(self.entries)>1 and sum(e['bytes'] for e in self.entries.values())>128*1024*1024):
                    _,old=self.entries.popitem(last=False)
                    try:old['path'].unlink(missing_ok=True)
                    except OSError:self.retired.add(old['path']) # A response can still hold this file.
                # Retry only retired completed files, never another worker's
                # currently-writing image in the shared temporary directory.
                for orphan in list(self.retired):
                    try:orphan.unlink(missing_ok=True);self.retired.discard(orphan)
                    except OSError:pass
            return self.get(key)
        except Exception:
            try:file.unlink(missing_ok=True)
            except OSError:pass
            raise
        finally:
            with self.lock:self.jobs.pop(key,None);event.set()

    def resolve(self,token):
        with self.lock:
            for result in self.entries.values():
                if result['token']==token and result['path'].is_file():return str(result['path'])
        raise FileNotFoundError('View expired. Reopen the image.')

    def close(self):
        with self.lock:self.closed=True;pending=list(self.jobs.values())
        for event in pending:event.wait(5)
        self.temp.cleanup()
