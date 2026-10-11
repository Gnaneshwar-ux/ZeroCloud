"""Local, conservative identity consolidation and persistent detection corrections."""
import json
import threading
import uuid
import time
import numpy as np
from face_matching import diverse, unit, learned_limits


class PeopleOrganizer:
    def __init__(self, engine):
        self.engine = engine
        self.guard = threading.Lock()
        self.again=False
        self.force_review=False
        self.feedback_event=threading.Event()
        self.state = {'state': 'idle', 'merged': 0, 'checked': 0, 'total': 0, 'error': ''}
        with engine.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS person_review(first TEXT,second TEXT,distance REAL,PRIMARY KEY(first,second))')
            db.execute('CREATE TABLE IF NOT EXISTS person_learning(person_id TEXT PRIMARY KEY,radius REAL,confirmations INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS person_feedback(id TEXT PRIMARY KEY,first TEXT,second TEXT,same INTEGER,state TEXT,error TEXT,created INTEGER)')
            db.execute("UPDATE person_feedback SET state='queued' WHERE state='working'")

    def start_feedback(self):
        threading.Thread(target=self.feedback_loop,daemon=True).start()

    def enqueue(self,args):
        first,second=args['id'],args['candidate']
        if first==second or not isinstance(args.get('same'),bool):raise ValueError('Choose two different groups and Yes or No.')
        key=str(uuid.uuid4())
        with self.engine.lock,self.engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for person in (first,second):
                if not db.execute('SELECT 1 FROM people WHERE id=? UNION SELECT 1 FROM person_alias WHERE old_id=?',(person,person)).fetchone():raise ValueError('This group changed. Refresh the review.')
            db.execute('INSERT INTO person_feedback VALUES(?,?,?,?,?,?,?)',(key,first,second,int(args['same']),'queued','',int(time.time()*1000)))
        self.feedback_event.set()
        return {'saved':True,'id':key}

    def feedback_loop(self):
        e=self.engine
        while not e.closing:
            with e.connection() as db:row=db.execute("SELECT * FROM person_feedback WHERE state='queued' ORDER BY created,rowid LIMIT 1").fetchone()
            if not row:self.feedback_event.wait(.5);self.feedback_event.clear();continue
            try:
                with e.lock,e.connection() as db:
                    db.execute("UPDATE person_feedback SET state='working' WHERE id=?",(row['id'],))
                    def resolve(key):
                        visited=set()
                        while key not in visited:
                            visited.add(key);alias=db.execute('SELECT person_id FROM person_alias WHERE old_id=?',(key,)).fetchone()
                            if not alias:break
                            key=alias[0]
                        return key
                    first,second=resolve(row['first']),resolve(row['second'])
                if first!=second:e.dispatch('reviewPerson',{'id':first,'candidate':second,'same':bool(row['same'])})
                elif not row['same']:raise ValueError('These groups have already combined. Use Edit group to separate the incorrect appearances.')
                with e.connection() as db:db.execute("UPDATE person_feedback SET state='complete',error='' WHERE id=?",(row['id'],))
                # The pending identities were protected during earlier passes.
                self.start()
            except Exception as error:
                with e.connection() as db:db.execute("UPDATE person_feedback SET state='failed',error=? WHERE id=?",(str(error),row['id']))

    def reviews(self,args=None):
        e=self.engine
        skipped={tuple(sorted(str(pair).split(':',1))) for pair in (args or {}).get('skip',[])}
        with e.connection() as db:
            active={r[0] for r in db.execute('SELECT DISTINCT f.person_id FROM faces f JOIN media m ON m.id=f.media_id WHERE m.present=1')}
            denied={tuple(sorted(r)) for r in db.execute('SELECT first,second FROM person_separate')}
            locked={key for r in db.execute("SELECT first,second FROM person_feedback WHERE state IN ('queued','working')") for key in r}
            rows=[r for r in db.execute('SELECT * FROM person_review ORDER BY distance,first,second') if r['first'] in active-locked and r['second'] in active-locked and tuple(sorted((r['first'],r['second']))) not in denied|skipped]
            result=[]
            for r in rows[:24]:
                pair={'first':r['first'],'second':r['second'],'distance':r['distance']}
                for key in ('first','second'):
                    pair[key+'Faces']=[f[0] for f in db.execute('SELECT f.id FROM faces f JOIN media m ON m.id=f.media_id WHERE f.person_id=? AND m.present=1 ORDER BY f.id LIMIT 4',(r[key],))]
                result.append(pair)
            errors=[dict(r) for r in db.execute("SELECT id,error FROM person_feedback WHERE state='failed' ORDER BY created DESC LIMIT 3")]
            pending=db.execute("SELECT count(*) FROM person_feedback WHERE state IN ('queued','working')").fetchone()[0]
            return {'total':len(rows),'pairs':result,'pending':pending,'errors':errors}

    def start(self,background=False):
        if not background:self.force_review=True
        if not self.guard.acquire(blocking=False):
            self.again=True
            return dict(self.state)
        self.state = {'state': 'working', 'merged': 0, 'checked': 0, 'total': 0, 'error': ''}
        threading.Thread(target=self.run, args=(background,), daemon=True).start()
        return dict(self.state)

    def run(self,background=False):
        combined=0
        try:
            for _ in range(3):
                self.state['merged']=0
                self.run_pass(background)
                added=self.state['merged'];combined+=added
                if self.state['state']=='error' or not added or self.engine.closing:break
                self.state['state']='working'
            self.state['merged']=combined
            if self.state['state']!='error':self.state['state']='complete'
        except Exception as error:self.state.update(state='error',error=str(error))
        finally:
            # A correction made during a pass must not be lost behind a busy lock.
            again=self.again;self.again=False
            self.guard.release()
            if again and not self.engine.closing:self.start(background=not self.force_review)
            else:self.force_review=False

    def run_pass(self,background=False):
        e = self.engine
        try:
            while background and not self.force_review and (e.paused or e.all_paused) and not e.closing:
                self.state['paused']=True;time.sleep(.25)
            self.state['paused']=False
            if e.closing:return
            with e.connection() as db:
                rows = list(db.execute('SELECT p.id,p.name,f.id AS face_id,f.embedding,f.media_id FROM people p JOIN faces f ON f.person_id=p.id JOIN media m ON m.id=f.media_id WHERE m.present=1 ORDER BY p.id,f.id'))
                denied = {(r[0], r[1]) for r in db.execute('SELECT first,second FROM person_separate')}
                negatives = {}
                for r in db.execute('SELECT person_id,embedding FROM person_negatives'):
                    negatives.setdefault(r[0], []).append(unit(json.loads(r[1])))
                me = e.setting(db, 'myPerson', '')
                limits=learned_limits(db)
            groups = {}
            for r in rows:
                g = groups.setdefault(r['id'], {'name': r['name'], 'values': [], 'media': set(), 'faces':set()})
                g['values'].append(r['embedding']); g['media'].add(r['media_id']);g['faces'].add(r['face_id'])
            ids = list(groups)
            if len(ids) < 2:
                return
            samples = [np.asarray([unit(json.loads(v)) for v in diverse(groups[k]['values'], 48)]) for k in ids]
            centers = np.asarray([unit(np.mean(bank, axis=0)) for bank in samples])
            suggestions={}
            anchors=[i for i,k in enumerate(ids) if k==me or groups[k]['name'] or len(groups[k]['values'])>=4]
            # A small fragment need not cover every pose in a large established
            # identity. Check ALL its appearances against a fixed reference bank.
            # Never grow the reference during this pass (avoids similarity chains).
            attachments={}
            for i,k in enumerate(ids):
                if k==me or groups[k]['name']:continue
                ranked=[]
                incoming=np.asarray([unit(json.loads(v)) for v in groups[k]['values']])
                for j in anchors:
                    other=ids[j]
                    if i==j or len(groups[k]['values'])>=len(groups[other]['values']):continue
                    if (k,other) in denied or (other,k) in denied or groups[k]['media']&groups[other]['media']:continue
                    if groups[k]['name'] and groups[other]['name'] and groups[k]['name'].casefold()!=groups[other]['name'].casefold():continue
                    nearest=(1-np.einsum('ik,jk->ij',incoming,samples[j],optimize=False)).min(axis=1)
                    distance=float(np.median(nearest))
                    if distance>=.42 or float(np.quantile(nearest,.9))>=.48:continue
                    if any(owner in negatives and float(np.min(1-np.asarray(negatives[owner])@bank.T))<=distance+.03 for owner,bank in [(other,incoming),(k,samples[j])]):continue
                    ranked.append((distance,float(np.quantile(nearest,.9)),float(nearest.max()),j))
                ranked.sort()
                if ranked:
                    d,q,w,j=ranked[0]
                    radius=limits.get(ids[j],.32)
                    if d<radius and q<min(.42,radius+.04) and w<min(.44,radius+.08) and (len(ranked)==1 or ranked[1][0]-d>=.04):attachments[i]=j
                    else:
                        for d,q,w,j in ranked[:2]:suggestions[tuple(sorted((ids[i],ids[j])))]=d
            # Blocked matrix products keep temporary memory bounded. Only close
            # centroid neighbours enter the more expensive exemplar comparison.
            edges = {}; self.state['total'] = len(ids)
            for start in range(0, len(ids), 128):
                self.state['paused']=background and not self.force_review and (e.paused or e.all_paused)
                while background and not self.force_review and (e.paused or e.all_paused) and not e.closing: time.sleep(.25)
                self.state['paused']=False
                if e.closing: return
                scores = 1-np.einsum('ik,jk->ij',centers[start:start+128],centers,optimize=False)
                for offset, distances in enumerate(scores):
                    i = start+offset
                    for j in np.flatnonzero(distances < .46):
                        j = int(j)
                        if j <= i: continue
                        a,b = ids[i],ids[j]
                        if (a,b) in denied or (b,a) in denied or groups[a]['media'] & groups[b]['media']: continue
                        if groups[a]['name'] and groups[b]['name'] and groups[a]['name'].casefold()!=groups[b]['name'].casefold(): continue
                        pair = 1-np.einsum('ik,jk->ij',samples[i],samples[j],optimize=False)
                        nearest = np.concatenate((pair.min(axis=0),pair.min(axis=1)))
                        if any(k in negatives and float(np.min(1-np.asarray(negatives[k]) @ other.T)) <= float(pair.min())+.03 for k,other in [(a,samples[j]),(b,samples[i])]): continue
                        if nearest.min()<.4 and np.median(nearest)<.42 and np.quantile(nearest,.9)<.48:
                            suggestions[tuple(sorted((a,b)))]=float(np.median(nearest))
                        if distances[j]>=.34:continue
                        if nearest.min() >= .30 or np.median(nearest) >= .32 or np.quantile(nearest,.9) >= .42: continue
                        edges[i,j] = float(distances[j])
                    self.state['checked'] = i+1
            # A near-tie with another identity is not an automatic Yes. Complete
            # linkage prevents A-B-C chains from pulling unrelated people together.
            neighbours = {i: [] for i in range(len(ids))}
            for (i,j),d in edges.items(): neighbours[i].append((d,j)); neighbours[j].append((d,i))
            owner = list(range(len(ids))); members = {i:{i} for i in range(len(ids))}
            # Attach only to an established identity and forbid co-occurring
            # fragments/explicit No across the resulting set.
            for i,j in sorted(attachments.items(),key=lambda item:len(groups[ids[item[1]]]['values']),reverse=True):
                left,right=owner[j],owner[i]
                if left==right:continue
                if any(groups[ids[a]]['media']&groups[ids[b]]['media'] or (ids[a],ids[b]) in denied or (ids[b],ids[a]) in denied for a in members[left] for b in members[right]):continue
                members[left]|=members[right];del members[right]
                for a in members[left]:owner[a]=left
            for (i,j),distance in sorted(edges.items(),key=lambda item:item[1]):
                left,right = owner[i],owner[j]
                if left == right: continue
                union = members[left] | members[right]
                if any(tuple(sorted((a,b))) not in edges for a in members[left] for b in members[right]): continue
                # Include an entire mutually compatible close set before testing
                # ambiguity: three near-identical fragments are not three rivals.
                changed=True
                while changed:
                    changed=False
                    nearby={other for k in union for score,other in neighbours[k] if other not in union and score-distance<.04}
                    for other in sorted(nearby):
                        candidate=members[owner[other]]
                        if all(tuple(sorted((a,b))) in edges for a in union for b in candidate if a!=b):
                            union |= candidate;changed=True
                if any(any(other not in union and score-distance < .04 for score,other in neighbours[k]) for k in union): continue
                roots={owner[k] for k in union}
                members[left] = union
                for root in roots-{left}:del members[root]
                for k in union: owner[k] = left
            for cluster in members.values():
                self.state['paused']=background and not self.force_review and (e.paused or e.all_paused)
                while background and not self.force_review and (e.paused or e.all_paused) and not e.closing: time.sleep(.25)
                self.state['paused']=False
                if len(cluster)<2 or e.closing: continue
                keys = [ids[k] for k in cluster]
                target = max(keys,key=lambda k:(k==me,bool(groups[k]['name']),len(groups[k]['media'])))
                # Validate current assignments under the same lock used by indexing.
                with e.lock:
                    with e.connection() as db:
                        existing = {r[0] for r in db.execute('SELECT id FROM people')}
                        keys = [k for k in keys if k in existing]
                        blocked = {(r[0],r[1]) for r in db.execute('SELECT first,second FROM person_separate')}
                        media = {k:{r[0] for r in db.execute('SELECT media_id FROM faces WHERE person_id=?',(k,))} for k in keys}
                        if target not in keys or len(keys)<2 or any((a,b) in blocked or media[a]&media[b] for a in keys for b in keys if a!=b): continue
                        locked={key for r in db.execute("SELECT first,second FROM person_feedback WHERE state IN ('queued','working')") for key in r}
                        if any(key in locked for key in keys):continue
                        if any({r[0] for r in db.execute('SELECT f.id FROM faces f JOIN media m ON m.id=f.media_id WHERE f.person_id=? AND m.present=1',(k,))}!=groups[k]['faces'] for k in keys): continue
                        if any(db.execute('SELECT name FROM people WHERE id=?',(k,)).fetchone()[0]!=groups[k]['name'] for k in keys): continue
                    e.dispatch('mergePeople',{'into':target,'fromIds':[k for k in keys if k!=target],'automatic':True})
                self.state['merged'] += len(keys)-1
            with e.lock,e.connection() as db:
                active={r[0] for r in db.execute('SELECT DISTINCT person_id FROM faces')}
                db.execute('DELETE FROM person_review')
                # One closest unresolved pairing per identity, not every edge in
                # a dense similarity graph. Recompute after future organization.
                used=set();pending=[]
                for (a,b),d in sorted(suggestions.items(),key=lambda item:item[1]):
                    if a not in active or b not in active or a in used or b in used:continue
                    pending.append((a,b,d));used.update((a,b))
                db.executemany('INSERT INTO person_review VALUES(?,?,?)',pending)
                self.state['review']=db.execute('SELECT count(*) FROM person_review').fetchone()[0]
        except Exception as error:
            self.state.update(state='error',error=str(error))
        finally:
            pass

    def edit(self,args):
        e=self.engine
        ids=list(dict.fromkeys(args.get('faceIds',[])))
        if not ids or len(ids)>200: raise ValueError('Select between 1 and 200 face appearances.')
        action=args.get('action')
        if action not in ('move','new','reject'): raise ValueError('Choose a correction action.')
        with e.lock,e.connection() as db:
            rows=[db.execute('SELECT f.*,m.fingerprint FROM faces f JOIN media m ON m.id=f.media_id WHERE f.id=?',(key,)).fetchone() for key in ids]
            if any(r is None for r in rows): raise ValueError('A face changed during indexing. Reopen the group.')
            sources={r['person_id'] for r in rows}
            target=args.get('target')
            if action=='new':
                target=str(uuid.uuid4())
                db.execute('INSERT INTO people(id,name,embedding) VALUES(?,?,?)',(target,str(args.get('name','')).strip()[:120],rows[0]['embedding']))
            if action=='move' and (not target or not db.execute('SELECT 1 FROM people WHERE id=?',(target,)).fetchone()): raise ValueError('Choose an existing person.')
            for r in rows:
                if action=='reject':
                    db.execute('INSERT OR IGNORE INTO rejected_faces VALUES(?,?,?)',(r['media_id'],r['fingerprint'],r['box']))
                    db.execute('DELETE FROM face_assignments WHERE media_id=? AND fingerprint=? AND box=?',(r['media_id'],r['fingerprint'],r['box']))
                    db.execute('DELETE FROM faces WHERE id=?',(r['id'],))
                else:
                    db.execute('UPDATE faces SET person_id=? WHERE id=?',(target,r['id']))
                    db.execute('INSERT OR REPLACE INTO face_assignments VALUES(?,?,?,?)',(r['media_id'],r['fingerprint'],r['box'],target))
            # Rebuild from remaining appearances: wrongly assigned prototypes must
            # not keep drawing this face back into its old group.
            for key in sources | ({target} if action!='reject' else set()):
                values=[r[0] for r in db.execute('SELECT embedding FROM faces WHERE person_id=?',(key,))]
                db.execute('DELETE FROM person_exemplars WHERE person_id=?',(key,))
                if values:
                    db.execute('UPDATE people SET embedding=? WHERE id=?',(values[0],key))
                    db.executemany('INSERT OR IGNORE INTO person_exemplars VALUES(?,?)',[(key,v) for v in diverse(values)])
                    positive=np.asarray([unit(json.loads(v)) for v in diverse(values)])
                    negative=[r[0] for r in db.execute('SELECT embedding FROM person_negatives WHERE person_id=?',(key,))]
                    db.execute('DELETE FROM person_negatives WHERE person_id=?',(key,))
                    db.executemany('INSERT OR IGNORE INTO person_negatives VALUES(?,?)',[(key,v) for v in negative if float(np.min(1-positive@unit(json.loads(v))))>.005])
                elif action=='reject' and not db.execute('SELECT 1 FROM face_assignments WHERE person_id=?',(key,)).fetchone():
                    db.execute('DELETE FROM person_alias WHERE person_id=? OR old_id=?',(key,key))
                    db.execute('DELETE FROM person_separate WHERE first=? OR second=?',(key,key))
                    db.execute('DELETE FROM people WHERE id=?',(key,))
                    if e.setting(db,'myPerson','')==key:db.execute('DELETE FROM settings WHERE key="myPerson"')
                elif action!='reject' and key!=target and not db.execute('SELECT 1 FROM face_assignments WHERE person_id=?',(key,)).fetchone():
                    # Fully moved groups disappear, including their stale seed.
                    # Redirect the active explorer and remembered aliases.
                    db.execute('UPDATE person_alias SET person_id=? WHERE person_id=?',(target,key))
                    db.execute('INSERT OR REPLACE INTO person_alias VALUES(?,?)',(key,target))
                    if e.setting(db,'myPerson','')==key:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('myPerson',target))
                    for other in [r[0] for r in db.execute('SELECT second FROM person_separate WHERE first=?',(key,)) if r[0]!=target]:
                        db.execute('INSERT OR IGNORE INTO person_separate VALUES(?,?)',(target,other))
                        db.execute('INSERT OR IGNORE INTO person_separate VALUES(?,?)',(other,target))
                    db.execute('DELETE FROM person_separate WHERE first=? OR second=?',(key,key))
                    db.execute('DELETE FROM people WHERE id=?',(key,))
            if action!='reject':
                for source in sources-{target}:
                    if db.execute('SELECT 1 FROM people WHERE id=?',(source,)).fetchone():
                        for a,b in [(source,target),(target,source)]: db.execute('INSERT OR IGNORE INTO person_separate VALUES(?,?)',(a,b))
        e.cache_dirty=True
        if action!='reject':
            for source in sources-{target}:
                with e.connection() as db:exists=db.execute('SELECT 1 FROM people WHERE id=?',(source,)).fetchone()
                if exists:e.dispatch('reviewPerson',{'id':source,'candidate':target,'same':False})
        return {'personId':target if action!='reject' else None,'people':e.people({})}
