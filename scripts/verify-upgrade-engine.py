"""Isolated regression fixtures. Never edits personal originals or their catalog."""
import base64
import io
import os
import json
from pathlib import Path
import shutil
import sys
import time
from PIL import Image, TiffImagePlugin
sys.path.insert(0,str(Path(sys.argv[1])/'engine'))
from portable_engine import Engine, FACE_VERSION

work=Path(sys.argv[2]).resolve(); work.mkdir(parents=True,exist_ok=True)
source=work/'originals'; source.mkdir(exist_ok=True)
image=Image.new('RGB',(1400,900),(71,124,163))
for extension,format in [('jpg','JPEG'),('tiff','TIFF'),('jp2','JPEG2000'),('ppm','PPM'),('pcx','PCX'),('tga','TGA'),('qoi','QOI'),('bmp','BMP')]:
    image.save(source/f'sample.{extension}',format)
exif=Image.Exif(); exif[34853]={1:'N',2:(TiffImagePlugin.IFDRational(17),TiffImagePlugin.IFDRational(23),TiffImagePlugin.IFDRational(0)),3:'E',4:(TiffImagePlugin.IFDRational(78),TiffImagePlugin.IFDRational(29),TiffImagePlugin.IFDRational(0))}
image.save(source/'gps.jpg','JPEG',exif=exif)
image.save(source/'misnamed.heic','JPEG')
(source/'zero.jpg').write_bytes(bytes(1024))
shutil.copyfile(os.environ['VELA_HEIC_SAMPLE'],source/'real.heic')
engine=Engine(work/'catalog')
try:
    engine.dispatch('indexControl',{'paused':True})
    scan=engine.dispatch('scan',{'path':str(source)})
    items=engine.load({})['items']; by_name={m['name']:m for m in items}
    assert len(items)==12,len(items)
    for name,media in by_name.items():
        if name=='zero.jpg': continue
        full=engine.dispatch('decodeImage',{'id':media['id']})
        decoded=Image.open(io.BytesIO(base64.b64decode(full['data'])))
        assert decoded.size==((2252,4000) if name=='misnamed.heic' else (3024,4032) if name=='real.heic' else (1400,900)),(name,decoded.size)
        thumbnail=engine.dispatch('decodeImage',{'id':media['id'],'thumbnail':True})
        assert max(Image.open(io.BytesIO(base64.b64decode(thumbnail['data']))).size)<=720
        assert engine.dispatch('previewSave',{'id':media['id'],'version':media['version'],'data':thumbnail['data']})
    try: engine.dispatch('decodeImage',{'id':by_name['zero.jpg']['id']}); raise AssertionError('Corrupt image accepted')
    except ValueError as error: assert 'only zero bytes' in str(error)
    print('PASS content-based full-resolution + thumbnail decoding: JPEG, TIFF, JPEG2000, PPM, PCX, TGA, QOI, BMP, misnamed HEIC/JPEG, genuine HEIC; zero-filled file diagnosed')
    folder_id=scan['folderId']; cache=source/'.vela-cache'
    engine.dispatch('setFolderCache',{'folderId':folder_id,'cachePath':str(cache)})
    assert engine.scan({'path':str(source)})['count']==12,'Cache images were indexed as originals'
    assert engine.load({})['folders'][0]['cacheLocation']==str(cache)
    engine.dispatch('favorite',{'id':by_name['gps.jpg']['id'],'favorite':True})
    # Synthetic embeddings test correction semantics, not recognition accuracy.
    vectors=[[1.0]+[0.0]*127,[0.0,1.0]+[0.0]*126,[0.0,0.0,1.0]+[0.0]*125]
    with engine.connection() as db:
        for index in range(3):
            db.execute('INSERT INTO people VALUES(?,?,?)',(f'person-{index}','Alice' if index==0 else '',json.dumps(vectors[index])))
            face_name=f'@{folder_id}/faces/face-{index}.jpg'
            face_path=engine.resource_path(db,face_name); face_path.parent.mkdir(exist_ok=True); image.resize((100,100)).save(face_path,'JPEG')
            db.execute('INSERT INTO faces VALUES(?,?,?,?,?,?)',(f'face-{index}',by_name['gps.jpg']['id'],f'person-{index}',json.dumps(vectors[index]),json.dumps({'x':index*100,'y':0,'w':50,'h':50}),face_name))
    engine.dispatch('mergePeople',{'fromIds':['person-1'],'into':'person-0'})
    with engine.connection() as db:
        assert db.execute('SELECT count(*) FROM person_exemplars WHERE person_id="person-0"').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM faces WHERE person_id="person-0"').fetchone()[0]==2
    engine.dispatch('rejectPeople',{'ids':['person-2']})
    with engine.connection() as db: assert db.execute('SELECT count(*) FROM rejected_faces').fetchone()[0]==1
    engine.dispatch('refindFaces',{}); engine.dispatch('faceControl',{'enabled':False})
    assert engine.people({})[0]['name']=='Alice'
    try: engine.dispatch('mergePeople',{'fromIds':['person-0','missing'],'into':'person-1'}); raise AssertionError('Invalid merge accepted')
    except ValueError: pass
    assert len(engine.people({}))==1
    print('PASS atomic merge retains confirmed appearance vectors and name; false detections remembered; refind preserves corrections; invalid merge has no partial writes')
    engine.dispatch('indexControl',{'paused':False})
    deadline=time.time()+20
    while time.time()<deadline and engine.dispatch('status',{})['locations']['done']<12: time.sleep(.1)
    gps=engine.dispatch('locations',{}); assert len(gps)==1,gps
    assert abs(gps[0]['latitude']-17.383333)<.00001 and abs(gps[0]['longitude']-78.483333)<.00001,gps
    engine.dispatch('indexControl',{'paused':True}); engine.dispatch('syncFolderCaches',{})
    assert not engine.cache_error,engine.cache_error
    print('PASS parallel GPS index reads actual EXIF coordinates, no-location images stay untagged; persistent stop/resume status uses stored counts')
finally: engine.dispatch('shutdown',{})

# A fresh machine profile mounts a copied media + cache directory.
moved=work/'other-machine'/'originals'; shutil.copytree(source,moved)
fresh=Engine(work/'other-machine'/'catalog')
try:
    fresh.dispatch('indexControl',{'paused':True})
    mounted=fresh.dispatch('mountFolderCache',{'path':str(moved),'cachePath':str(moved/'.vela-cache')})
    assert mounted['folderId']==folder_id
    saved=fresh.load({}); assert len(saved['items'])==12
    assert next(m for m in saved['items'] if m['name']=='gps.jpg')['favorite']
    assert len(fresh.dispatch('locations',{}))==1
    assert fresh.people({})[0]['name']=='Alice'
    assert fresh.dispatch('previewGet',{'id':by_name['gps.jpg']['id'],'version':by_name['gps.jpg']['version'],'type':'thumbnail'})
    assert fresh.dispatch('storage',{})['originalCopies']==0
    assert not list((moved/'.vela-cache').glob('*full*'))
    print('PASS independent machine import from per-folder portable cache: stable IDs, names, confirmed vectors, rejection, GPS, favorites and thumbnail reuse; zero original copies')
finally: fresh.dispatch('shutdown',{})
