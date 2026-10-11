"""Bounded, normalized multi-prototype matching with persistent negative examples."""
import json
import numpy as np

def unit(values):
    vector=np.asarray(values,dtype=np.float32)
    norm=np.linalg.norm(vector)
    if vector.ndim!=1 or not np.isfinite(vector).all() or norm<1e-9: raise ValueError('Invalid face embedding')
    return vector/norm

def diverse(values,limit=48):
    vectors=np.asarray([unit(json.loads(value)) for value in dict.fromkeys(values)],dtype=np.float32)
    if not len(vectors): return []
    chosen=[0];nearest=1-vectors@vectors[0]
    while len(chosen)<min(limit,len(vectors)):
        nearest[chosen]=-1;index=int(np.argmax(nearest))
        if nearest[index]<.005:break
        chosen.append(index);nearest=np.minimum(nearest,1-vectors@vectors[index])
    return [json.dumps(vectors[index].tolist()) for index in chosen]

def banks(db):
    result={}
    for row in db.execute('SELECT id,embedding FROM people UNION ALL SELECT person_id AS id,embedding FROM person_exemplars'):
        result.setdefault(row['id'],[]).append(row['embedding'])
    return {key:np.asarray([unit(json.loads(v)) for v in diverse(values)],dtype=np.float32) for key,values in result.items()}

def learned_limits(db):
    # Only explicit human confirmations calibrate an identity's radius.
    return {r['person_id']:float(r['radius']) for r in db.execute('SELECT person_id,radius FROM person_learning')}

def match(vector,prototypes,negatives,limits=None):
    vector=unit(vector);scores=sorted((float(np.min(1-bank@vector)),key) for key,bank in prototypes.items() if len(bank))
    if not scores or scores[0][0]>=(limits or {}).get(scores[0][1],.32):return None
    distance,key=scores[0]
    # Ambiguous nearest identities remain separate, rather than contaminating a person.
    if len(scores)>1 and scores[1][0]-distance<.035:return None
    denied=negatives.get(key)
    if denied is not None and len(denied) and float(np.min(1-denied@vector))<=distance+.03:return None
    return key
