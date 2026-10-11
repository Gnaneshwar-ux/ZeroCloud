"""Content-aware, bounded story selection using local metadata and small previews."""
import json,re
from pathlib import Path
import numpy as np
from PIL import Image,ImageOps

SCENERY={'landscapes','beaches','plants','architecture'}

def annotate(engine,pool,rng,limit=96):
 # Sample through the whole event, not only its first page. Always retain some
 # random choices so another version is not the same chronological slice.
 if len(pool)>limit:
  stride=[pool[round(i*(len(pool)-1)/(limit//2-1))] for i in range(limit//2)]
  chosen={r['id']:r for r in stride+rng.sample(pool,limit//2)};pool=list(chosen.values())
 ids=[s['id'] for s in pool];features={}
 if not ids:return []
 with engine.connection() as db:
  marks=','.join('?' for _ in ids)
  for r in db.execute(f"SELECT m.id,m.path,m.folder_id,c.captured_at,t.categories,p.file FROM media m LEFT JOIN capture_dates c ON c.media_id=m.id AND c.version=m.fingerprint LEFT JOIN media_categories t ON t.media_id=m.id AND t.version=m.fingerprint LEFT JOIN previews p ON p.media_id=m.id AND p.version LIKE (m.bytes||':'||m.modified||':%') WHERE m.id IN ({marks})",ids):
   tags=set(json.loads(r['categories'] or '[]'));skip=bool(tags&{'screenshots','documents'} or re.search(r'screenshot|screen.?capture',r['path'],re.I))
   features[r['id']]={'tags':tags,'people':set(),'quality':.5,'hash':None,'aspect':1,'date':r['captured_at'] or '', 'session':(r['folder_id'],str(Path(r['path']).parent)), 'skip':skip}
   if r['file']:
    try:
     file=engine.resource_path(db,r['file'])
     with Image.open(file) as image:
      image=ImageOps.exif_transpose(image);features[r['id']]['aspect']=image.width/max(1,image.height)
      gray=np.asarray(image.convert('L').resize((96,64)),dtype=np.float32)
      sharp=float(np.mean(np.abs(np.diff(gray,axis=0)))+np.mean(np.abs(np.diff(gray,axis=1))))
      exposed=float(np.mean((gray>18)&(gray<240)))
      features[r['id']]['quality']=.65*min(1,sharp/18)+.35*exposed
      tiny=np.asarray(image.convert('L').resize((9,8)));features[r['id']]['hash']=tiny[:,1:]>tiny[:,:-1]
    except (OSError,ValueError):pass
  for r in db.execute(f'SELECT media_id,person_id FROM faces WHERE media_id IN ({marks})',ids):
   if r['media_id'] in features:features[r['media_id']]['people'].add(r['person_id'])
 return [{**s,**features.get(s['id'],{'tags':set(),'people':set(),'quality':.5,'hash':None,'aspect':1,'date':'','skip':False})} for s in pool]

def select_story(engine,pool,count,rng,focus=None):
 items=[s for s in annotate(engine,pool,rng) if not s['skip']]
 # Respect exclusions rather than padding a reel with screenshots/documents.
 if len(items)<4:raise ValueError('This selection has fewer than four suitable photo/video moments. Screenshots and documents are excluded from reels.')
 # Prefer one coherent outing over jumping among years of a person's photos.
 sessions={}
 for shot in items:
  if shot['date']:sessions.setdefault((*shot.get('session',('','')),shot['date'][:10]),[]).append(shot)
 options=[shots for shots in sessions.values() if len(shots)>=4]
 if len(sessions)>1 and options:items=rng.choice(options)
 used=set();chosen=[];seen_people=set();scene_count=0
 scenery=[s for s in items if s['tags']&SCENERY and not s['people']]
 roles=['scene','people','people','detail','scene','people','detail','people','scene','people','detail','scene']
 count=min(count,len(items));jitter={s['id']:rng.random()*.2 for s in items}
 for index in range(count):
  role=roles[index%len(roles)]
  candidates=[s for s in items if s['id'] not in used]
  def score(s):
   value=s['quality']+jitter[s['id']]+.12*len(s['people']-seen_people)
   if role=='scene' and s['tags']&SCENERY:value+=.65
   if role=='people' and s['people']:value+=.55
   if focus and focus in s['people']:value+=.15
   if focus and role=='people' and sum(focus in p['people'] for p in chosen)<2 and focus in s['people']:value+=.7
   if role=='detail' and not s['people']:value+=.2
   if s['kind']=='video':value+=.15 if index not in (0,count-1) else -.1
   if index==2 and s['kind']=='video' and not any(p['kind']=='video' for p in chosen):value+=.75
   for previous in chosen:
    if s['hash'] is not None and previous['hash'] is not None and np.count_nonzero(s['hash']!=previous['hash'])<=5:value-=2
   if chosen and s['people'] and s['people']==chosen[-1]['people']:value-=.25
   return value
  shot=max(candidates,key=score);chosen.append(shot);used.add(shot['id']);seen_people|=shot['people']
  if shot['tags']&SCENERY:scene_count+=1
 # A scenic opening/ending bookends the people in the middle when available.
 if scenery and chosen[-1]['people']:
  for i in range(len(chosen)-2,0,-1):
   if chosen[i]['tags']&SCENERY and not chosen[i]['people']:chosen[i],chosen[-1]=chosen[-1],chosen[i];break
 sources=[{k:s[k] for k in ('id','fingerprint','modified','kind')} for s in chosen]
 notes={'people':len(seen_people),'scenicMoments':scene_count,'selection':'Sharpness/exposure, scene categories, varied people and near-duplicate suppression; inferred categories may be imperfect.'}
 return sources,notes

def contextual_pool(engine,pool):
 """Expand a person story only with media from that same folder/date session."""
 if not pool:return pool
 with engine.connection() as db:
  marks=','.join('?' for _ in pool[:96])
  anchors=list(db.execute(f'SELECT m.folder_id,m.path,c.captured_at FROM media m JOIN capture_dates c ON c.media_id=m.id AND c.version=m.fingerprint WHERE m.id IN ({marks})',[s['id'] for s in pool[:96]]))
  sessions={(r['folder_id'],str(Path(r['path']).parent),r['captured_at'][:10]) for r in anchors if r['captured_at']}
  if not sessions:return pool
  result={s['id']:s for s in pool}
  for folder,directory,day in sorted(sessions)[:12]:
   for r in db.execute("SELECT m.id,m.fingerprint,m.modified,m.kind,m.path FROM media m JOIN capture_dates c ON c.media_id=m.id AND c.version=m.fingerprint WHERE m.folder_id=? AND m.present=1 AND m.kind IN ('image','raw','video') AND substr(c.captured_at,1,10)=? LIMIT 256",(folder,day)):
    if str(Path(r['path']).parent)==directory:result[r['id']]={k:r[k] for k in ('id','fingerprint','modified','kind')}
 return list(result.values())
