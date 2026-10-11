"""Offline, bounded memory reels. Originals are read-only; output is a derivative."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave
import datetime
import random
import uuid
import textwrap
import urllib.request
import urllib.parse
from memory_story import select_story,contextual_pool
from memory_score import SCORES,write_score

STYLES = {
    'daydream': {'label':'Daydream', 'seconds':3.5, 'transition':'fade', 'bpm':80},
    'postcards': {'label':'Postcards', 'seconds':2.75, 'transition':'smoothleft', 'bpm':117},
    'spotlight': {'label':'Spotlight', 'seconds':2.5, 'transition':'fade', 'bpm':120},
    'joyful': {'label':'Good times', 'seconds':2.75, 'transition':'smoothup', 'bpm':112},
    'goldenhour': {'label':'Golden hour', 'seconds':3.5, 'transition':'fade', 'bpm':88},
    'cinema': {'label':'Scenic cinema', 'seconds':4, 'transition':'fadeblack', 'bpm':76},
}
TRACKS = {
    'beach': {'title':'Moonlight Beach','filename':'Moonlight Beach.mp3','isrc':'USUAN2000005','bpm':117,'mood':'joyful'},
    'cinematic': {'title':'All This','filename':'All This.mp3','isrc':'USUAN1300001','bpm':137,'mood':'cinematic'},
    'carefree': {'title':'Carefree','filename':'Carefree.mp3','isrc':'USUAN1400037','bpm':96,'mood':'joyful'},
    'riley': {'title':'Life of Riley','filename':'Life of Riley.mp3','isrc':'USUAN1400054','bpm':102,'mood':'joyful'},
    'beetle': {'title':'Daily Beetle','filename':'Daily Beetle.mp3','isrc':'USUAN1500025','bpm':100,'mood':'joyful'},
}
LICENSE = 'https://creativecommons.org/licenses/by/4.0/'

RECIPE = 'memory-v1-portrait720-motion-music'

class Memories:
    def __init__(self, engine):
        self.engine = engine
        self.last_activity = time.monotonic()
        self.foreground_busy = False
        self.state = 'Waiting for idle time'
        self.current = ''
        self.process = None
        self.forced_id = None
        self.geocoder = None
        self.place_names = {}
        self.music_state = 'Original soundtracks · offline ready'
        self.music_thread = None
        self.create_lock = threading.Lock()
        self.auto_attempt = 0
        with engine.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, title TEXT, category TEXT, sources TEXT,
                status TEXT, file TEXT, created INTEGER, error TEXT DEFAULT '');''')
            db.execute("UPDATE memories SET status='queued' WHERE status='rendering'")
            db.execute("UPDATE memories SET status='queued',error='' WHERE status='failed' AND error LIKE '%_pillow_heif%' AND error LIKE '%Application Control%'")
            columns={r['name'] for r in db.execute('PRAGMA table_info(memories)')}
            for name in ['recipe','style','soundtrack','credits','parent_id','selection_hash']:
                if name not in columns: db.execute(f"ALTER TABLE memories ADD COLUMN {name} TEXT DEFAULT ''")
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def config(self):
        with self.engine.connection() as db:
            return {'enabled': self.engine.setting(db,'memoriesEnabled','true') == 'true',
                    'paused': self.engine.setting(db,'memoriesPaused','false') == 'true',
                    'onlineMusic': self.engine.setting(db,'memoriesOnlineMusic','false') == 'true'}

    def place_name(self, latitude, longitude):
        key=(round(latitude,2),round(longitude,2))
        if key in self.place_names: return self.place_names[key]
        try:
            import reverse_geocoder as rg
            dataset=Path(rg.__file__).parent/'rg_cities1000.csv'
            if not dataset.is_file(): return 'A place to remember'
            if self.geocoder is None: self.geocoder=rg.RGeocoder(mode=1,verbose=False,stream=io.StringIO(dataset.read_text('utf-8')))
            place=self.geocoder.query([(latitude,longitude)])[0]
            lat=math.radians(latitude); dlat=math.radians(float(place['lat'])-latitude);dlon=math.radians(float(place['lon'])-longitude)
            distance=6371*2*math.asin(min(1,math.sqrt(math.sin(dlat/2)**2+math.cos(lat)*math.cos(math.radians(float(place['lat'])))*math.sin(dlon/2)**2)))
            name=f"Near {place['name']}" if distance<75 else 'A place to remember'
        except Exception: name='A place to remember'
        self.place_names[key]=name
        return name

    def candidates(self, all_sources=False):
        with self.engine.connection() as db:
            rows = [dict(r) for r in db.execute("SELECT m.*,c.captured_at FROM media m LEFT JOIN capture_dates c ON c.media_id=m.id AND c.version=m.fingerprint WHERE m.present=1 AND m.kind IN ('image','raw','video') ORDER BY c.captured_at,m.id")]
            for r in rows:
                try:r['capture_time']=datetime.datetime.fromisoformat(r['captured_at']).replace(tzinfo=datetime.timezone.utc).timestamp()*1000 if r['captured_at'] else None
                except ValueError:r['capture_time']=None
            by_id = {r['id']:r for r in rows}
            groups = []
            for p in db.execute('SELECT id,name FROM people ORDER BY name,id'):
                ids = [r[0] for r in db.execute('SELECT DISTINCT media_id FROM faces WHERE person_id=? ORDER BY media_id',(p['id'],))]
                groups.append(('people',p['name'] or 'Together', [by_id[i] for i in ids if i in by_id]))
            places = {}
            for r in db.execute('SELECT media_id,latitude,longitude FROM locations WHERE latitude IS NOT NULL AND longitude IS NOT NULL'):
                if r['media_id'] in by_id:
                    key = (round(r['latitude'],1),round(r['longitude'],1))
                    places.setdefault(key,[]).append(by_id[r['media_id']])
            for key,items in sorted(places.items()):
                point=db.execute('SELECT latitude,longitude FROM locations WHERE media_id=?',(items[0]['id'],)).fetchone()
                groups.append(('places',self.place_name(point['latitude'],point['longitude']),items))
            sessions = {}
            gps={r['media_id']:(r['latitude'],r['longitude']) for r in db.execute('SELECT media_id,latitude,longitude FROM locations WHERE latitude IS NOT NULL AND longitude IS NOT NULL')}
            def trip_title(items,directory):
                points=[gps[r['id']] for r in items if r['id'] in gps]
                if points:
                    names={self.place_name(*point) for point in points}
                    name=' & '.join(sorted(names)[:2])
                else:name='Camera-date trip'
                dates=[r['captured_at'][:10] for r in items if r['captured_at']]
                date=f'{min(dates)} – {max(dates)}' if dates and min(dates)!=max(dates) else dates[0] if dates else ''
                return f'{name} · {date}' if date else name
            for r in rows:
                if r['capture_time'] is None: continue
                key = (r['folder_id'], str(Path(r['path']).parent))
                sessions.setdefault(key,[]).append(r)
            for (_,directory),items in sessions.items():
                batch=[]
                for r in items:
                    if batch and r['capture_time']-batch[-1]['capture_time'] > 3*86400000:
                        groups.append(('trips',trip_title(batch,directory),batch)); batch=[]
                    batch.append(r)
                if batch: groups.append(('trips',trip_title(batch,directory),batch))
        result=[]
        for category,title,items in groups:
            # Even chronological coverage; avoid repeated copies of the same content.
            unique={r['fingerprint'] or r['id']:r for r in items}
            items=sorted(unique.values(),key=lambda r:(r['capture_time'] is None,r['capture_time'] or 0,r['id']))
            if len(items)<4: continue
            pool=items
            if len(items)>12: items=[items[round(i*(len(items)-1)/11)] for i in range(12)]
            sources=[{'id':r['id'],'fingerprint':r['fingerprint'],'modified':r['modified'],'kind':r['kind']} for r in items]
            identity=sorted((s['fingerprint'] or s['id'],s['kind']) for s in sources)
            signature=hashlib.sha256((RECIPE+json.dumps(identity,sort_keys=True)).encode()).hexdigest()[:32]
            result.append({'id':signature,'title':title,'category':category,'sources':sources,'pool':[{'id':r['id'],'fingerprint':r['fingerprint'],'modified':r['modified'],'kind':r['kind']} for r in pool] if all_sources else sources})
        return result

    def variation(self, args):
        # User-triggered generation is bounded and serialized, not infinite autoplay.
        with self.create_lock:
            candidates=self.candidates(all_sources=True)
            if args.get('selection'):
                candidates=[c for c in candidates if c['id']==args['selection'] and c['category']==args.get('category')]
            with self.engine.connection() as db:
                pending=db.execute("SELECT count(*) FROM memories WHERE status IN ('queued','rendering')").fetchone()[0]
                if pending>=8: raise ValueError('Eight reels are already queued. Let one finish before adding another.')
                parent=db.execute('SELECT * FROM memories WHERE id=?',(args.get('id',''),)).fetchone()
                if parent:
                    key=parent['parent_id'] or parent['id']
                    candidates=[c for c in candidates if c['id']==key]
                    if not candidates:
                        sources=json.loads(parent['sources'])
                        valid=[s for s in sources if db.execute('SELECT 1 FROM media WHERE id=? AND present=1 AND fingerprint=?',(s['id'],s['fingerprint'])).fetchone()]
                        if len(valid)>=4: candidates=[{'id':key,'title':parent['title'].split(' · ')[0],'category':parent['category'],'pool':valid}]
                category=args.get('category','all')
                if category!='all': candidates=[c for c in candidates if category in c['category'].split(',')]
                if not candidates: raise ValueError('Add at least four distinct photos or videos in a person, place or camera-date session, then try again.')
                last=json.loads(self.engine.setting(db,'memorySurpriseRecent','[]'))
                available=[c for c in candidates if c['id'] not in last[-3:]] or candidates
                rng=random.Random(uuid.uuid4().hex)
                # Pick subject types evenly: hundreds of people must not drown out trips.
                subject=rng.choice(sorted({c['category'] for c in available}))
                group=rng.choice([c for c in available if c['category']==subject]); pool=group['pool']; title=group['title']; categories=group['category'];focus=None
                if categories=='people':pool=contextual_pool(self.engine,pool)
                # A place can become a people-at-that-place story, not only a location reel.
                if not args.get('selection') and 'places' in categories and rng.random()<.65:
                    members={s['id'] for s in pool}; options=[]
                    for person in db.execute('SELECT id,name FROM people'):
                        ids={r[0] for r in db.execute('SELECT DISTINCT media_id FROM faces WHERE person_id=?',(person['id'],))}
                        subset=[s for s in pool if s['id'] in ids and s['id'] in members]
                        if len(subset)>=4: options.append((person['id'],person['name'] or 'Together',subset))
                    if options:
                        focus,person,subset=rng.choice(options);title=f'{person} · {title}';categories='people,places'
                        # Keep the place context: scenery and friends should not
                        # disappear just because a familiar person was chosen.
                for attempt in range(12):
                    style=args.get('style','auto')
                    if style=='auto':
                        recent=[r[0] for r in db.execute("SELECT style FROM memories WHERE style!='' ORDER BY created DESC LIMIT 2")]
                        style=rng.choice([key for key in STYLES if key not in recent] or list(STYLES))
                    if style not in STYLES: raise ValueError('Choose a supported reel style.')
                    count=min(len(pool),rng.randint(6,10));chosen,story=select_story(self.engine,pool,count,rng,focus)
                    signature=hashlib.sha256(json.dumps([style,[(s['id'],s['fingerprint']) for s in chosen]]).encode()).hexdigest()
                    if not db.execute('SELECT 1 FROM memories WHERE selection_hash=?',(signature,)).fetchone(): break
                else: raise ValueError('All nearby variations have already been used. Add more media or choose another style.')
                key=uuid.uuid4().hex; seed=rng.randrange(1,2**31)
                mood=args.get('music','auto')
                if mood not in ('auto','joyful','relaxed','cinematic'):raise ValueError('Choose a supported music mood.')
                db.execute('INSERT INTO memories(id,title,category,sources,status,created,recipe,style,parent_id,selection_hash) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (key,title,categories,json.dumps(chosen),'queued',int(time.time()*1000),json.dumps({'version':3,'seed':seed,'musicMood':mood,'story':story}),style,group['id'],signature))
                if args.get('force',True):
                    forced=json.loads(self.engine.setting(db,'memoryForced','[]'));forced.append(key)
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memoryForced',json.dumps(forced)))
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memorySurpriseRecent',json.dumps((last+[group['id']])[-20:])))
            return {**self.overview(),'requestedId':key}

    def download_music(self):
        if not self.config()['onlineMusic']: raise ValueError('Enable optional online music first. Photos are never uploaded.')
        if self.music_thread and self.music_thread.is_alive(): return self.overview()
        self.music_state='Downloading optional licensed music…'
        def work():
            try:
                import imageio_ffmpeg
                directory=self.engine.root/'memories'/'music';directory.mkdir(parents=True,exist_ok=True)
                class Redirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        parsed=urllib.parse.urlparse(newurl)
                        if parsed.scheme!='https' or parsed.hostname not in ['incompetech.com','www.incompetech.com']: raise ValueError('Music redirect was not trusted.')
                        return super().redirect_request(req,fp,code,msg,headers,newurl)
                opener=urllib.request.build_opener(Redirect())
                for key,track in TRACKS.items():
                    target=directory/f'{key}.m4a'
                    if target.is_file(): continue
                    if self.engine.closing or not self.config()['onlineMusic']: raise InterruptedError('Music download stopped.')
                    url='https://incompetech.com/music/royalty-free/mp3-royaltyfree/'+urllib.parse.quote(track['filename'])
                    with tempfile.TemporaryDirectory(prefix='download-',dir=directory) as temporary:
                        raw=Path(temporary)/'music.mp3';start=time.monotonic();size=0
                        with opener.open(urllib.request.Request(url,headers={'User-Agent':'ZeroCloud/0.2 (optional soundtrack download)'}),timeout=15) as response,raw.open('wb') as out:
                            while True:
                                if self.engine.closing or not self.config()['onlineMusic']: raise InterruptedError('Music download stopped.')
                                chunk=response.read(64*1024)
                                if not chunk: break
                                size+=len(chunk)
                                if size>25*1024*1024 or time.monotonic()-start>60: raise ValueError('Music download exceeded its safety limit.')
                                out.write(chunk)
                        compressed=Path(temporary)/'music.m4a'
                        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-y','-threads','1','-protocol_whitelist','file,pipe','-i',str(raw),'-t','90','-vn','-c:a','aac','-b:a','128k',str(compressed)],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        compressed.replace(target)
                        target.with_suffix('.json').write_text(json.dumps({**track,'artist':'Kevin MacLeod','license':LICENSE,'source':url}),encoding='utf8')
                self.music_state='Optional music cached · offline ready'
            except Exception as error: self.music_state=f'Music unavailable · original soundtrack will be used. {str(error)[:160]}'
        self.music_thread=threading.Thread(target=work,daemon=True);self.music_thread.start()
        return self.overview()

    def dispatch(self, action, args):
        if action=='activity':
            self.foreground_busy=bool(args.get('busy'))
            if self.foreground_busy: self.last_activity=time.monotonic()
            return True
        if action=='control':
            with self.engine.connection() as db:
                for key in ['enabled','paused','onlineMusic']:
                    if key in args: db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memories'+key[0].upper()+key[1:],json.dumps(bool(args[key]))))
            return self.overview()
        if action=='options':
            return {'options':[{'id':c['id'],'title':c['title'],'category':c['category'],'count':len(c['pool'])} for c in self.candidates(all_sources=True)]}
        if action=='create':
            if not args.get('selection'):raise ValueError('Choose a person, place or trip first.')
            return self.variation(args)
        if action in ['surprise','variation']: return self.variation(args)
        if action=='downloadMusic': return self.download_music()
        if action=='discover':
            candidates=self.candidates()
            with self.engine.connection() as db:
                # Duplicate suppression is independent of people/place label changes.
                for item in candidates[:100]:
                    db.execute('INSERT OR IGNORE INTO memories(id,title,category,sources,status,created) VALUES(?,?,?,?,?,?)',
                               (item['id'],item['title'],item['category'],json.dumps(item['sources']),'suggested',int(time.time()*1000)))
                    saved=db.execute('SELECT category FROM memories WHERE id=?',(item['id'],)).fetchone()
                    categories=set(saved['category'].split(',')); categories.add(item['category'])
                    db.execute('UPDATE memories SET category=? WHERE id=?',(','.join(sorted(categories)),item['id']))
                    if item['category']=='places': db.execute("UPDATE memories SET title=? WHERE id=? AND title LIKE 'Around %'",(item['title'],item['id']))
            return self.overview()
        if action=='queue':
            with self.engine.connection() as db:
                row=db.execute('SELECT status FROM memories WHERE id=?',(args['id'],)).fetchone()
                if not row: raise ValueError('Memory is no longer available.')
                style=args.get('style','auto');mood=args.get('music','auto')
                if style!='auto' and style not in STYLES:raise ValueError('Choose a supported reel style.')
                if mood not in ('auto','joyful','relaxed','cinematic'):raise ValueError('Choose a supported music mood.')
                if row['status'] in ('suggested','failed') and (style!='auto' or mood!='auto'):
                    saved=db.execute('SELECT recipe FROM memories WHERE id=?',(args['id'],)).fetchone()[0]
                    recipe=json.loads(saved) if saved else {'version':3,'seed':random.SystemRandom().randrange(1,2**31)}
                    if mood!='auto':recipe['musicMood']=mood;recipe.pop('musicKey',None)
                    db.execute('UPDATE memories SET recipe=?,style=CASE WHEN ?!=\'auto\' THEN ? ELSE style END WHERE id=?',(json.dumps(recipe),style,style,args['id']))
                db.execute("UPDATE memories SET status='queued',error='' WHERE id=? AND status IN ('suggested','failed')",(args['id'],))
                if args.get('force') and row['status']!='ready':
                    forced=json.loads(self.engine.setting(db,'memoryForced','[]'))
                    if args['id'] not in forced: forced.append(args['id'])
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memoryForced',json.dumps(forced)))
            return self.overview()
        if action=='ack':
            with self.engine.connection() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memoriesSeen',str(int(time.time()*1000))))
            return True
        if action=='resolve':
            with self.engine.connection() as db:
                row=db.execute("SELECT file FROM memories WHERE id=? AND status='ready'",(args['id'],)).fetchone()
            if not row: raise FileNotFoundError('Memory is not ready.')
            file=(self.engine.root/row['file']).resolve()
            if not file.is_relative_to(self.engine.root/'memories') or not file.is_file(): raise FileNotFoundError('Saved memory is missing.')
            return str(file)
        if action=='overview': return self.overview()
        if action=='notice':
            result=self.overview()
            if result['notify']:
                with self.engine.connection() as db: db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memoriesNotified',str(int(time.time()*1000))))
            return result
        raise ValueError('Unknown memory action')

    def overview(self):
        with self.engine.connection() as db:
            # Old saved reels keep their files; only replace the stale coordinate label.
            for row in db.execute("SELECT id,title,sources FROM memories WHERE title LIKE '%Around %' OR title='A place to remember'").fetchall():
                try:
                    source_ids=[s['id'] for s in json.loads(row['sources'])]
                    point=None
                    for source_id in source_ids:
                        point=db.execute('SELECT latitude,longitude FROM locations WHERE media_id=? AND latitude IS NOT NULL AND longitude IS NOT NULL',(source_id,)).fetchone()
                        if point:break
                    if point:
                        name=self.place_name(point['latitude'],point['longitude'])
                        if name!='A place to remember':
                            prefix=row['title'].split('Around ')[0] if 'Around ' in row['title'] else ''
                            db.execute('UPDATE memories SET title=? WHERE id=?',(prefix+name,row['id']))
                except (ValueError,KeyError,TypeError):pass
            items=[dict(r) for r in db.execute("SELECT id,title,category,status,created,error,style,soundtrack,credits,parent_id FROM memories WHERE status!='suggested' ORDER BY created DESC LIMIT 300")]
            seen=int(self.engine.setting(db,'memoriesSeen','0'))
            notified=int(self.engine.setting(db,'memoriesNotified','0'))
            fresh=any(r['status']=='ready' and r['created']>seen for r in items)
            notify=fresh and int(time.time()*1000)-notified>=86400000
        size=sum(p.stat().st_size for p in (self.engine.root/'memories').glob('*.mp4')) if (self.engine.root/'memories').exists() else 0
        directory=self.engine.root/'memories'/'music'
        cached=[key for key in TRACKS if (directory/f'{key}.m4a').is_file()]
        return {**self.config(),'items':items,'state':self.state,'current':self.current,'cacheBytes':size,'budgetBytes':512*1024*1024,'notify':notify,'musicState':self.music_state,'cachedMusic':cached}

    def idle(self):
        import psutil
        if self.engine.closing or self.config()['paused']: return False
        if self.forced_id: return True
        indexing=not self.engine.all_paused and (self.engine.status['state'] in ['loading','processing'] or self.engine.scan_state['state']=='scanning' or self.engine.location_status['state']=='processing' or self.engine.capture_dates.state=='Reading capture dates' or self.engine.categories.state=='Categorizing previews')
        return not (self.engine.paused or self.foreground_busy or indexing) and time.monotonic()-self.last_activity>20 and psutil.cpu_percent(interval=None)<40

    def wait_idle(self):
        while not self.engine.closing:
            if self.idle(): return
            self.state='Paused · waiting for low CPU and no viewing'
            time.sleep(1)
        raise InterruptedError('App is closing; generation will resume later.')

    def encode(self, command):
        import psutil
        with tempfile.TemporaryFile() as errors:
            self.process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            child=psutil.Process(self.process.pid)
            try:
                if os.name=='nt':
                    try: child.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    except psutil.Error: pass
                suspended=False
                while self.process.poll() is None:
                    if self.engine.closing:
                        if suspended: child.resume()
                        self.process.terminate(); self.process.wait(timeout=5)
                        raise InterruptedError('Generation interrupted safely.')
                    idle=self.idle()
                    if not idle and not suspended: child.suspend(); suspended=True; self.state='Paused · waiting for low CPU and no viewing'
                    elif idle and suspended: child.resume(); suspended=False; self.state='Creating reel'
                    time.sleep(.5)
                if self.process.returncode:
                    errors.seek(0); raise RuntimeError(errors.read().decode(errors='replace')[-1400:])
            finally:
                if self.process.poll() is None:
                    try: child.resume()
                    except psutil.Error: pass
                    self.process.terminate(); self.process.wait(timeout=5)
                self.process=None

    @staticmethod
    def soundtrack(target, seconds, style='daydream', seed=0):
        key={'daydream':'dream','postcards':'coastal','spotlight':'celebration','joyful':'sunny','goldenhour':'golden','cinema':'dream'}.get(style,'sunny')
        write_score(target,seconds,key,seed)

    def prepare_recipe(self,row):
        row=dict(row);recipe=json.loads(row['recipe']) if row['recipe'] else {'version':3,'seed':random.SystemRandom().randrange(1,2**31)}
        rng=random.Random(recipe['seed']);sources=json.loads(row['sources'])
        with self.engine.connection() as db:
            style=row['style']
            if style not in STYLES:
                recent=[r[0] for r in db.execute("SELECT style FROM memories WHERE style!='' ORDER BY created DESC LIMIT 2")]
                style=rng.choice([key for key in STYLES if key not in recent] or list(STYLES))
        if 'story' not in recipe:
            pool=contextual_pool(self.engine,sources)
            sources,recipe['story']=select_story(self.engine,pool,min(10,len(sources)),rng)
        if 'musicKey' not in recipe:
            mood=recipe.get('musicMood','auto')
            if mood=='auto':mood='cinematic' if style=='cinema' else 'relaxed' if style in ('daydream','goldenhour') else 'joyful'
            directory=self.engine.root/'memories'/'music'
            with self.engine.connection() as db:
                previous=json.loads(self.engine.setting(db,'memoryRecentScores','[]'))
                keys=[key for key,v in SCORES.items() if v['mood']==mood]
                fresh=[key for key in keys if key not in previous[-2:]] or keys
                # Licensed downloads supplement, never monopolize, the original library.
                cached=[key for key,v in TRACKS.items() if v.get('mood','joyful')==mood and (directory/f'{key}.m4a').is_file() and 'licensed:'+key not in previous[-2:]]
                key='licensed:'+rng.choice(cached) if self.config()['onlineMusic'] and cached and rng.random()<.65 else rng.choice(fresh)
                recipe['musicKey']=key
                recipe['musicOffset']=rng.randrange(0,30) if key.startswith('licensed:') else 0
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('memoryRecentScores',json.dumps((previous+[key])[-12:])))
        recipe['version']=3;row.update(recipe=json.dumps(recipe),style=style,sources=json.dumps(sources))
        with self.engine.connection() as db:db.execute('UPDATE memories SET recipe=?,style=?,sources=? WHERE id=?',(row['recipe'],style,row['sources'],row['id']))
        return row

    def render(self,row):
        # Suggested, forced and surprise reels now use the same story editor.
        return self.render_variation(self.prepare_recipe(row))

    def render_variation(self, row):
        import imageio_ffmpeg
        from PIL import Image,ImageOps,ImageFilter,ImageDraw,ImageFont
        if not row['recipe'] or 'musicKey' not in json.loads(row['recipe']):row=self.prepare_recipe(row)
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe();style=row['style'] or 'daydream';recipe=json.loads(row['recipe']);rng=random.Random(recipe['seed'])
        design=STYLES[style];sources=json.loads(row['sources']);directory=self.engine.root/'memories';directory.mkdir(exist_ok=True)
        width,height=(1280,720) if style=='cinema' else (720,1280)
        score_key=recipe['musicKey'];track_key=score_key.split(':',1)[1] if score_key.startswith('licensed:') else ''
        cached=directory/'music'/f'{track_key}.m4a'
        external=bool(track_key) and self.config()['onlineMusic'] and cached.is_file()
        track=TRACKS[track_key] if external else None
        original_key=score_key if score_key in SCORES else {'daydream':'dream','postcards':'coastal','spotlight':'celebration','joyful':'sunny','goldenhour':'golden','cinema':'dream'}.get(style,'sunny')
        bpm=track['bpm'] if track else SCORES[original_key]['bpm']
        beats=max(3,round(design['seconds']*bpm/60));seconds=round(beats*60/bpm*24)/24
        score_title=track['title'] if track else SCORES[original_key]['title']
        credits=f"{track['title']} — Kevin MacLeod (incompetech.com)\nCC BY 4.0 · {LICENSE}\nEdited excerpt; original photos and videos unchanged." if track else f'{score_title} · original ZeroCloud instrumental arrangement\nComposed locally for this reel · no music download required'
        with self.engine.connection() as db: db.execute('UPDATE memories SET soundtrack=?,credits=? WHERE id=?',(score_title,credits,row['id']))
        base=[ffmpeg,'-y','-threads','1','-filter_threads','1','-protocol_whitelist','file,pipe']
        with tempfile.TemporaryDirectory(prefix='render-',dir=directory) as temporary:
            work=Path(temporary);clips=[];durations=[];motion_plan=[];transition_plan=[]
            def font(size):
                try:return ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',size)
                except OSError:return ImageFont.load_default(size=size)
            def card(title,lines,path):
                canvas=Image.new('RGB',(width,height),(13,22,35));draw=ImageDraw.Draw(canvas)
                for y in range(height):draw.line((0,y,width,y),fill=(17+int(y/height*12),29+int(y/height*12),48+int(y/height*24)))
                x=int(width*.085);top=int(height*.125)
                draw.rounded_rectangle((x,top,x+64,top+64),radius=20,fill=(74,116,182));draw.text((x+92,top+18),'ZEROCLOUD MEMORIES',font=font(23),fill=(169,199,242))
                wrapped='\n'.join(textwrap.wrap(title[:100],width=38 if width>height else 23));draw.multiline_text((x,int(height*.32)),wrapped,font=font(48 if width>height else 52),fill=(237,243,252),spacing=16)
                y=int(height*.56)
                for line in lines:
                    for piece in textwrap.wrap(line,width=70 if width>height else 44):draw.text((x,y),piece,font=font(23),fill=(176,195,220));y+=38
                    y+=12
                canvas.save(path)
            def still(path,duration,index,motion):
                target=work/f'clip-{index}.mp4';frames=round(duration*24)
                # Six restrained movements, bounded inside the safe padded canvas.
                progress=f'on/{max(1,frames-1)}';z=f'1.008+0.025*{progress}' if motion%2==0 else f'1.033-0.025*{progress}'
                x='(iw-iw/zoom)/2';y='(ih-ih/zoom)/2'
                if motion in (2,3):x=f'(iw-iw/zoom)*({progress if motion==2 else "1-"+progress})'
                if motion in (4,5):y=f'(ih-ih/zoom)*({progress if motion==4 else "1-"+progress})'
                motion_plan.append({'source':str(index),'motion':['push-in','pull-back','pan-left','pan-right','rise','settle'][motion%6],'seconds':duration})
                self.encode(base+['-i',str(path),'-vf',f"scale={width*2}:{height*2},zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps=24,setsar=1",'-t',str(duration),'-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1',str(target)])
                clips.append(target);durations.append(duration)
            self.wait_idle();self.state='Creating a new memory';self.current=f"{row['title']} · opening"
            card(row['title'],[design['label'],f'{len(sources)} moments · a new perspective'],work/'opening.jpg');still(work/'opening.jpg',2,'opening',0)
            movements=list(range(6));rng.shuffle(movements)
            for index,source in enumerate(sources):
                self.wait_idle();self.state='Creating a new memory';self.current=f"{row['title']} · {index+1}/{len(sources)}"
                with self.engine.connection() as db:
                    current=db.execute('SELECT fingerprint FROM media WHERE id=?',(source['id'],)).fetchone()
                    if not current or current['fingerprint']!=source['fingerprint']: raise ValueError('A source changed. Make another version with the current media.')
                    original=self.engine.original(db,source['id'])
                # Beat-aligned but not metronomic: a breathing scenic pace and
                # shorter middle beats, with a longer final photograph.
                shot_beats=beats+(1 if index in (0,len(sources)-1) else [-1,0,1][index%3])
                shot_seconds=round(max(2.25,min(4.75,shot_beats*60/bpm))*24)/24
                if source['kind']=='video':
                    import cv2
                    capture=cv2.VideoCapture(str(original))
                    try:
                        fps=capture.get(cv2.CAP_PROP_FPS) or 25;duration=capture.get(cv2.CAP_PROP_FRAME_COUNT)/fps;choices=[]
                        for fraction in rng.sample([.1,.25,.4,.55,.7,.85],4):
                            start=max(0,duration-shot_seconds)*fraction;capture.set(cv2.CAP_PROP_POS_MSEC,start*1000);ok,frame=capture.read()
                            if ok:choices.append((cv2.Laplacian(cv2.cvtColor(cv2.resize(frame,(160,90)),cv2.COLOR_BGR2GRAY),cv2.CV_64F).var(),start))
                        start=max(choices)[1] if choices else 0
                    finally:capture.release()
                    target=work/f'clip-{index}.mp4'
                    # Extend short excerpts by holding their last frame, not failing the timeline.
                    self.encode(base+['-ss',str(start),'-i',str(original),'-t',str(shot_seconds),'-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x111d30,setsar=1,fps=24,tpad=stop_mode=clone:stop_duration={shot_seconds}', '-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1',str(target)])
                    clips.append(target);durations.append(shot_seconds);motion_plan.append({'source':source['id'],'motion':'video-highlight','seconds':shot_seconds})
                else:
                    decoded=self.engine._dispatch('decodeImage',{'id':source['id'],'memory':True})
                    with Image.open(io.BytesIO(base64.b64decode(decoded['data']))) as original_image:
                        image=original_image.convert('RGB')
                        if original_image.info.get('icc_profile'):
                            try:
                                from PIL import ImageCms
                                image=ImageCms.profileToProfile(image,ImageCms.ImageCmsProfile(io.BytesIO(original_image.info['icc_profile'])),ImageCms.createProfile('sRGB'),outputMode='RGB')
                            except (OSError,ValueError):pass
                        background=ImageOps.fit(image,(width,height)).filter(ImageFilter.GaussianBlur(32))
                        palette={'joyful':(235,217,187),'postcards':(226,222,207),'goldenhour':(70,49,32)}
                        shade=Image.new('RGB',(width,height),palette.get(style,(13,22,35)));background=Image.blend(background,shade,.55)
                        bounds=(620,1100) if style=='postcards' else (width-60,height-120 if height>width else height-70)
                        foreground=ImageOps.contain(image,bounds);x=(width-foreground.width)//2;y=(height-foreground.height)//2
                        if style=='postcards':ImageDraw.Draw(background).rounded_rectangle((x-12,y-12,x+foreground.width+12,y+foreground.height+12),radius=8,fill=(250,247,239))
                        background.paste(foreground,(x,y));background.save(work/f'{index}.jpg',quality=93)
                    still(work/f'{index}.jpg',shot_seconds,index,movements[index%6])
            self.wait_idle();card('Until next time',credits.split('\n'),work/'credits.jpg');still(work/'credits.jpg',4 if external else 2.5,'credits',0)
            overlap=round(max(.25,min(.5,30/bpm))*24)/24;total=sum(durations)-overlap*(len(clips)-1)
            music=cached if external else work/'score.wav'
            if not external:write_score(music,total,original_key,recipe['seed'])
            command=base+['-filter_complex_threads','1']
            for clip in clips:command+=['-i',str(clip)]
            command+=['-stream_loop','-1']+(['-ss',str(recipe.get('musicOffset',0))] if external else [])+['-i',str(music)]
            filters=[f'[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,fps=24,format=yuv420p[v{i}]' for i in range(len(clips))]
            elapsed=durations[0];previous='v0'
            for i in range(1,len(clips)):
                offset=elapsed-overlap;label=f'joined{i}'
                choices=['fade','smoothleft','smoothup','smoothright','circleopen'] if style in ('joyful','postcards','spotlight') else ['fade','dissolve','fadeblack','smoothdown']
                transition='fade' if i in (1,len(clips)-1) else choices[(i+recipe['seed'])%len(choices)]
                if i not in (1,len(clips)-1) and (transition==transition_plan[-1] or (i==len(clips)-2 and transition=='fade')):
                    alternatives=[name for name in choices if name!=transition_plan[-1] and (i!=len(clips)-2 or name!='fade')]
                    transition=alternatives[(i+recipe['seed'])%len(alternatives)]
                transition_plan.append(transition)
                filters.append(f'[{previous}][v{i}]xfade=transition={transition}:duration={overlap}:offset={offset:.4f}[{label}]');previous=label;elapsed+=durations[i]-overlap
            filters.append(f'[{len(clips)}:a]volume=0.65,afade=t=in:d=1.5,afade=t=out:st={max(0,total-2):.3f}:d=2[audio]')
            output=work/'finished.mp4';self.current=f"{row['title']} · transitions and music"
            self.encode(command+['-filter_complex',';'.join(filters),'-map',f'[{previous}]','-map','[audio]','-t',f'{total:.3f}','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1','-c:a','aac','-b:a','128k','-metadata',f'comment={credits}','-movflags','+faststart',str(output)])
            used=sum(p.stat().st_size for p in directory.glob('*.mp4'))
            if used+output.stat().st_size>512*1024*1024:raise ValueError('Memory cache reached its 512 MB budget. Existing reels were kept.')
            output.replace(directory/f"{row['id']}.mp4")
            (directory/f"{row['id']}.json").write_text(json.dumps({'title':row['title'],'style':style,'sources':sources,'credits':credits,'recipe':recipe,'motion':motion_plan,'transitions':transition_plan,'duration':total,'width':width,'height':height},ensure_ascii=False),encoding='utf8')
        return f"memories/{row['id']}.mp4"

    def loop(self):
        last_discovery=0
        while not self.engine.closing:
            try:
                with self.engine.connection() as db:
                    forced=json.loads(self.engine.setting(db,'memoryForced','[]'))
                    forced=[key for key in forced if db.execute("SELECT 1 FROM memories WHERE id=? AND status='queued'",(key,)).fetchone()]
                self.forced_id=forced[0] if forced else None
                if not self.idle(): self.state='Waiting for idle time · low CPU and no viewing'; time.sleep(2); continue
                if not self.forced_id and time.time()-last_discovery>600:
                    self.dispatch('discover',{}); last_discovery=time.time()
                with self.engine.connection() as db:
                    config=self.config()
                    last=int(self.engine.setting(db,'memoryAutoLast','0'))
                    if not self.forced_id and config['enabled'] and time.time()-last>86400:
                        db.execute("UPDATE memories SET status='queued' WHERE id=(SELECT id FROM memories WHERE status='suggested' ORDER BY created LIMIT 1)")
                        if db.execute('SELECT changes()').fetchone()[0]:
                            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memoryAutoLast',str(int(time.time()))))
                    row=db.execute("SELECT * FROM memories WHERE status='queued' AND (? IS NULL OR id=?) ORDER BY created LIMIT 1",(self.forced_id,self.forced_id)).fetchone()
                if not row:
                    if config['enabled'] and time.time()-last>86400 and time.monotonic()-self.auto_attempt>600:
                        self.auto_attempt=time.monotonic()
                        try:self.variation({'force':False})
                        except ValueError:pass
                    self.state='Up to date · waiting for new memories';time.sleep(3);continue
                total=sum(p.stat().st_size for p in (self.engine.root/'memories').glob('*.mp4'))
                if total>450*1024*1024: self.state='Memory cache is full · generation stopped (512 MB budget)'; time.sleep(10); continue
                with self.engine.connection() as db: db.execute("UPDATE memories SET status='rendering' WHERE id=?",(row['id'],))
                file=self.render(row)
                with self.engine.connection() as db:
                    db.execute("UPDATE memories SET status='ready',file=?,created=?,error='' WHERE id=?",(file,int(time.time()*1000),row['id']))
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('memoryAutoLast',str(int(time.time()))))
                self.current=''; self.state='New memory ready'
            except InterruptedError:
                with self.engine.connection() as db: db.execute("UPDATE memories SET status='queued' WHERE status='rendering'")
            except Exception as error:
                self.state='Generation needs attention'
                with self.engine.connection() as db: db.execute("UPDATE memories SET status='failed',error=? WHERE status='rendering'",(str(error),))
                time.sleep(5)
