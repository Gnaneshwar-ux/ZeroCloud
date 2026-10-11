import sys,time
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(sys.argv[1])/'engine'))
from portable_engine import Engine
work=Path(sys.argv[2]);work.mkdir(parents=True,exist_ok=True)
disk=work/'disk';disk.mkdir(exist_ok=True)
image=Image.new('RGB',(24,24),(100,160,190));image.save(disk/'seed.jpg')
import shutil
for index in range(2000): shutil.copyfile(disk/'seed.jpg',disk/f'photo-{index}.jpg')
engine=Engine(work/'catalog')
engine.dispatch('indexControl',{'paused':True});assert engine.dispatch('startScan',{'path':str(disk)})['backgroundScan']
time.sleep(.3);assert engine.dispatch('scanStatus',{})['state']=='paused';assert not engine.load({})['items']
engine.dispatch('shutdown',{})
engine=Engine(work/'catalog')
try:
    assert engine.dispatch('scanStatus',{})['state']=='paused'
    engine.dispatch('indexControl',{'paused':False})
    deadline=time.time()+30
    while time.time()<deadline and engine.dispatch('scanStatus',{})['done']<100:time.sleep(.005)
    engine.dispatch('indexControl',{'paused':True})
    deadline=time.time()+10
    while time.time()<deadline and engine.dispatch('scanStatus',{})['state']=='scanning':time.sleep(.02)
    paused=engine.dispatch('scanStatus',{})
    assert paused['state'] in ['paused','complete'],paused
    stored=len(engine.load({})['items']);assert stored>0
    engine.dispatch('indexControl',{'paused':False})
    deadline=time.time()+30
    while time.time()<deadline and engine.dispatch('scanStatus',{})['state'] in ['scanning','paused']:time.sleep(.1)
    status=engine.dispatch('scanStatus',{});assert status['state']=='complete',status
    assert len(engine.load({})['items'])==2001
    assert engine.dispatch('storage',{})['originalCopies']==0
    print(f'PASS disk indexing: asynchronous 2001-file scan, pause before start, saved job restored on restart, mid-scan stop ({stored} retained), resumed without duplicates, no original copies')
finally:engine.dispatch('shutdown',{})
