"""Six original stereo instrumental arrangements; bounded one-second synthesis."""
import math
import wave
import numpy as np

SCORES={
 'sunny':{'title':'Sunlit smiles','mood':'joyful','bpm':112,'instrument':'marimba'},
 'coastal':{'title':'Ocean postcards','mood':'joyful','bpm':104,'instrument':'pluck'},
 'roadtrip':{'title':'Windows down','mood':'joyful','bpm':120,'instrument':'pop'},
 'celebration':{'title':'Little celebrations','mood':'joyful','bpm':118,'instrument':'bells'},
 'golden':{'title':'Golden afternoon','mood':'relaxed','bpm':88,'instrument':'acoustic'},
 'dream':{'title':'Quiet horizons','mood':'cinematic','bpm':76,'instrument':'pads'},
}

def write_score(target,seconds,key='sunny',seed=0):
 design=SCORES[key];rng=np.random.default_rng(seed);rate=22050
 bpm=design['bpm'];instrument=design['instrument'];transpose=int(rng.choice([0,2,4,5,7]))
 progressions=[[(0,4,7),(7,11,14),(9,12,16),(5,9,12)],[(0,4,7),(5,9,12),(9,12,16),(7,11,14)],[(5,9,12),(0,4,7),(7,11,14),(9,12,16)]]
 progression=progressions[seed%3];motifs=[[0,2,1,4,2,1,0,4],[2,1,0,2,4,2,1,0],[0,1,2,4,2,4,1,2]];motif=motifs[(seed//3)%3]
 def hz(notes):return 261.625565*2**((np.asarray(notes)+transpose)/12)
 with wave.open(str(target),'wb') as out:
  out.setnchannels(2);out.setsampwidth(2);out.setframerate(rate)
  for second in range(math.ceil(seconds)):
   n=min(rate,max(0,round((seconds-second)*rate)))
   if not n:break
   t=second+np.arange(n)/rate;beat=t*bpm/60;bar=np.floor(beat/4).astype(int)
   chord=np.asarray(progression)[bar%4];previous=np.asarray(progression)[(bar-1)%4]
   blend=np.clip((beat%4)/.45,0,1)
   pad=np.zeros(n)
   for j in range(3):
    pad+=(np.sin(2*np.pi*hz(chord[:,j])/2*t)*blend+np.sin(2*np.pi*hz(previous[:,j])/2*t)*(1-blend))*.035
   subdivision=1 if instrument=='pads' else 2;step=np.floor(beat*subdivision).astype(int);phase=beat*subdivision%1
   scale=np.asarray([0,2,4,7,9]);notes=scale[np.asarray(motif)[step%8]]+12
   lead_frequency=hz(notes);attack=1-np.exp(-phase*75)
   decay={'marimba':7,'pluck':9,'pop':3,'bells':4,'acoustic':8,'pads':2}[instrument]
   envelope=attack*np.exp(-phase*decay)
   lead=np.sin(2*np.pi*lead_frequency*t)
   if instrument=='marimba':lead+=.28*np.sin(2*np.pi*lead_frequency*3*t)*np.exp(-phase*15)
   elif instrument=='bells':lead+=.35*np.sin(2*np.pi*lead_frequency*2.76*t)+.15*np.sin(2*np.pi*lead_frequency*4.1*t)
   elif instrument=='pop':lead+=.35*np.sin(2*np.pi*lead_frequency*2*t)+.18*np.sin(2*np.pi*lead_frequency*3*t)
   elif instrument in ('pluck','acoustic'):lead+=.4*np.sin(2*np.pi*lead_frequency*2*t)+.2*np.sin(2*np.pi*lead_frequency*4*t)
   lead*=envelope*.13
   # Breaks and a small second-half lift make an arrangement, not a static loop.
   lift=np.where(t<seconds*.15,.45,np.where(t>seconds*.55,1,.8));lead*=lift
   bass=np.sin(2*np.pi*hz(chord[:,0])/4*t)*np.exp(-(beat%1)*3)*.085
   drums=np.zeros(n)
   if instrument!='pads':
    phase_beat=beat%1
    kick=np.sin(2*np.pi*(48*t+2*(1-np.exp(-phase_beat*25))))*np.exp(-phase_beat*25)*.14
    backbeat=(np.floor(beat).astype(int)%2==1)
    clap=rng.normal(0,.035,n)*np.exp(-phase_beat*22)*backbeat
    shaker=rng.normal(0,.009,n)*np.exp(-(beat*4%1)*25)
    if instrument in ('pluck','acoustic'):kick*=.55;clap*=.5
    drums=(kick+clap+shaker)*lift
   fade=np.minimum(1,t/1.4)*np.clip((seconds-t)/1.8,0,1)
   pan=.25*np.sin(2*np.pi*t/9)
   left=(pad+bass+drums+lead*(.8+pan))*fade
   right=(pad+bass+drums+lead*(.8-pan))*fade
   signal=np.stack((left,right),axis=1)
   signal=np.tanh(signal*1.4)*.82
   out.writeframes((signal*32767).astype('<i2').tobytes())
