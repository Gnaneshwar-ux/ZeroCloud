"""Small offline visual suggestions from cached previews, never original copies."""
import json,re,threading,time
from pathlib import Path

MODEL_VERSION='mobilenetv2-imagenet-v1'
VALID={'screenshots','landscapes','beaches','food','documents','animals','architecture','plants'}
def categories_for_scores(labels, probabilities):
    groups={key:0.0 for key in VALID}
    for index in sorted(range(len(probabilities)),key=lambda i:float(probabilities[i]),reverse=True)[:5]:
        probability=float(probabilities[index]); label=labels[index].lower();tags=[]
        if re.search(r'\balp\b|cliff|reef|geyser|lakeside|promontory|sandbar|seashore|valley|volcano',label):tags.append('landscapes')
        if re.search(r'seashore|sandbar|coral reef|promontory',label):tags.append('beaches')
        if label.startswith(('n077','n078','n076')) or 'espresso' in label:tags.append('food')
        if index<398:tags.append('animals')
        if re.search(r'castle|church|monastery|mosque|palace|stupa|triumphal arch|bell cote',label):tags.append('architecture')
        if re.search(r'daisy|rapeseed|lady.s slipper|mushroom|fungus|agaric|bolete|acorn|buckeye',label):tags.append('plants')
        for tag in tags:groups[tag]+=probability
    return sorted(tag for tag,score in groups.items() if score>=.35)

class Categories:
    def __init__(self,engine,preview_version):
        self.engine=engine;self.preview_version=preview_version;self.state='Waiting for previews';self.model=None;self.labels=[]
        with engine.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS media_categories(media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,version TEXT,categories TEXT,source TEXT,error TEXT,revision INTEGER,model_version TEXT)')
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def classify(self,file):
        import numpy as np,onnxruntime as ort
        from PIL import Image,ImageOps
        if self.model is None:
            directory=Path(__file__).parent/'models'
            self.labels=(directory/'synset.txt').read_text('utf-8').splitlines()
            options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
            self.model=ort.InferenceSession(str(directory/'mobilenetv2-7.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        with Image.open(file) as original:
            image=ImageOps.fit(original.convert('RGB'),(224,224),method=Image.Resampling.BILINEAR)
            pixels=np.asarray(image,dtype=np.float32)/255
        pixels=(pixels-np.array([.485,.456,.406],dtype=np.float32))/np.array([.229,.224,.225],dtype=np.float32)
        scores=self.model.run(None,{self.model.get_inputs()[0].name:pixels.transpose(2,0,1)[None]})[0].reshape(-1)
        exp=np.exp(scores-scores.max());probabilities=exp/exp.sum()
        return categories_for_scores(self.labels,probabilities)
    def changes(self,args):
        with self.engine.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT c.* FROM media_categories c JOIN media m ON m.id=c.media_id AND m.fingerprint=c.version WHERE m.present=1 AND c.revision>? ORDER BY revision LIMIT 250',(int(args.get('since',0)),))]
            done=db.execute('SELECT count(*) FROM media_categories c JOIN media m ON m.id=c.media_id AND m.fingerprint=c.version WHERE m.present=1').fetchone()[0]
            total=db.execute("SELECT count(*) FROM media WHERE present=1 AND kind IN ('image','raw','video')").fetchone()[0]
        for row in rows:row['categories']=json.loads(row['categories'])
        return {'updates':rows,'revision':rows[-1]['revision'] if rows else int(args.get('since',0)),'state':self.state,'done':done,'total':total}
    def assign(self,args):
        tags=sorted(set(args.get('categories',[])))
        if not set(tags)<=VALID:raise ValueError('Unknown category')
        ids=list(dict.fromkeys(args.get('ids',[])))
        if not ids or len(ids)>100:raise ValueError('Select 1–100 items')
        with self.engine.lock,self.engine.connection() as db:
            for media_id in ids:
                row=db.execute('SELECT fingerprint FROM media WHERE id=? AND present=1',(media_id,)).fetchone()
                if not row:raise ValueError('Item is no longer available')
                revision=db.execute('SELECT coalesce(max(revision),0)+1 FROM media_categories').fetchone()[0]
                db.execute('INSERT OR REPLACE INTO media_categories VALUES(?,?,?,?,?,?,?)',(media_id,row[0],json.dumps(tags),'user',None,revision,MODEL_VERSION))
        self.engine.cache_dirty=True
        return True
    def loop(self):
        import psutil
        while not self.engine.closing:
            if self.engine.all_paused or self.engine.paused or psutil.cpu_percent(interval=None)>50:
                self.state='Paused';time.sleep(1);continue
            with self.engine.connection() as db:
                row=db.execute("SELECT m.id,m.fingerprint,m.path,p.file FROM media m JOIN previews p ON p.media_id=m.id AND p.version=(m.bytes||':'||m.modified||':'||?) LEFT JOIN media_categories c ON c.media_id=m.id WHERE m.present=1 AND m.kind IN ('image','raw','video') AND (c.version IS NULL OR c.version!=m.fingerprint OR (c.source!='user' AND c.model_version!=?)) ORDER BY p.accessed DESC LIMIT 1",(self.preview_version,MODEL_VERSION)).fetchone()
                file=self.engine.resource_path(db,row['file']) if row else None
            if not row:self.state='Waiting for previews';time.sleep(2);continue
            self.state='Categorizing previews';error=None;tags=[]
            try:
                tags=self.classify(file)
                if re.search(r'screenshot|screen[ _-]?shot|screen[ _-]?capture',row['path'],re.I):tags.append('screenshots')
            except Exception as reason:error=str(reason)
            with self.engine.lock,self.engine.connection() as db:
                fresh=db.execute('SELECT fingerprint FROM media WHERE id=?',(row['id'],)).fetchone()
                manual=db.execute('SELECT source,version FROM media_categories WHERE media_id=?',(row['id'],)).fetchone()
                if fresh and fresh[0]==row['fingerprint'] and not (manual and manual['source']=='user' and manual['version']==fresh[0]):
                    revision=db.execute('SELECT coalesce(max(revision),0)+1 FROM media_categories').fetchone()[0]
                    db.execute('INSERT OR REPLACE INTO media_categories VALUES(?,?,?,?,?,?,?)',(row['id'],row['fingerprint'],json.dumps(sorted(set(tags))),'model',error,revision,MODEL_VERSION))
            self.engine.cache_dirty=True;time.sleep(.3)
