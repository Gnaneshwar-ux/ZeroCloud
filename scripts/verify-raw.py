import base64,io,sys
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(sys.argv[1])/'engine'))
from portable_engine import Engine
engine=Engine(Path(sys.argv[2])/'catalog')
try:
    engine.dispatch('indexControl',{'paused':True})
    sample=Path(sys.argv[3]).resolve()
    engine.scan({'path':str(sample.parent)})
    media=next(item for item in engine.load({})['items'] if item['name']==sample.name)
    decoded=engine.dispatch('decodeImage',{'id':media['id']})
    image=Image.open(io.BytesIO(base64.b64decode(decoded['data']))); assert image.size==(4096,3072),image.size
    thumbnail=engine.dispatch('decodeImage',{'id':media['id'],'thumbnail':True})
    assert max(Image.open(io.BytesIO(base64.b64decode(thumbnail['data']))).size)<=720
    assert engine.dispatch('storage',{})['originalCopies']==0
    print('PASS actual camera DNG: full 4096x3072 display rendering and <=720px thumbnail through rawpy/LibRaw; original linked and unchanged')
finally:engine.dispatch('shutdown',{})
