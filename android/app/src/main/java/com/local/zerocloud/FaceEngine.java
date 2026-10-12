package com.local.zerocloud;

import android.database.Cursor;
import android.graphics.Bitmap;
import org.json.JSONArray;
import org.opencv.android.OpenCVLoader;
import org.opencv.android.Utils;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;
import org.opencv.objdetect.FaceDetectorYN;
import org.opencv.objdetect.FaceRecognizerSF;
import java.io.*;
import java.nio.*;
import java.util.*;

/** YuNet detection + normalized SFace embeddings. Feedback changes clustering, not model weights. */
final class FaceEngine {
    final Catalog db;
    FaceDetectorYN detector;
    FaceRecognizerSF recognizer;
    FaceEngine(Catalog c){db=c;}
    synchronized void initialize()throws Exception{
        if(detector!=null)return;if(!OpenCVLoader.initLocal())throw new IOException("OpenCV could not initialize on this device.");
        detector=FaceDetectorYN.create(model("face_detection_yunet_2023mar.onnx"),"",new Size(1024,1024),.90f,.3f,5000);
        recognizer=FaceRecognizerSF.create(model("face_recognition_sface_2021dec.onnx"),"");
    }
    String model(String name)throws Exception{File root=new File(db.context.getFilesDir(),"models");root.mkdirs();File file=new File(root,name);if(!file.isFile()){File tmp=new File(root,name+".part");try(InputStream in=db.context.getAssets().open("models/"+name);OutputStream out=new FileOutputStream(tmp)){byte[] b=new byte[65536];int n;while((n=in.read(b))>=0)out.write(b,0,n);}if(!tmp.renameTo(file))throw new IOException("Could not prepare offline face model.");}return file.getAbsolutePath();}
    static byte[] pack(float[] f){ByteBuffer b=ByteBuffer.allocate(f.length*4).order(ByteOrder.LITTLE_ENDIAN);for(float v:f)b.putFloat(v);return b.array();}
    static float[] unpack(byte[] bytes){if(bytes==null||bytes.length%4!=0)return new float[0];FloatBuffer b=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();float[] f=new float[b.remaining()];b.get(f);return f;}
    static double distance(float[] a,float[] b){if(a.length==0||a.length!=b.length)return 2;double dot=0,an=0,bn=0;for(int i=0;i<a.length;i++){dot+=a[i]*b[i];an+=a[i]*a[i];bn+=b[i]*b[i];}return an==0||bn==0?2:1-dot/Math.sqrt(an*bn);}
    static void normalize(float[] f){double n=0;for(float v:f)n+=v*v;n=Math.sqrt(n);if(n>0)for(int i=0;i<f.length;i++)f[i]/=n;}
    static final class Group {String id,name,face;boolean me;ArrayList<float[]> vectors=new ArrayList<>();HashSet<String> media=new HashSet<>();double nearest(float[] f){double best=2;for(float[] v:vectors)best=Math.min(best,distance(f,v));return best;}}
    ArrayList<Group> groups(){LinkedHashMap<String,Group> map=new LinkedHashMap<>();try(Cursor c=db.getReadableDatabase().rawQuery("SELECT p.id,p.name,p.me,f.id,f.media,f.vector FROM people p JOIN faces f ON p.id=f.person JOIN media m ON m.id=f.media WHERE f.rejected=0 AND m.deleted=0 ORDER BY f.manual DESC,f.confidence DESC",null)){while(c.moveToNext()){Group g=map.get(c.getString(0));if(g==null){g=new Group();g.id=c.getString(0);g.name=c.getString(1);g.me=c.getInt(2)==1;g.face=c.getString(3);map.put(g.id,g);}g.media.add(c.getString(4));if(g.vectors.size()<16)g.vectors.add(unpack(c.getBlob(5)));}}return new ArrayList<>(map.values());}
    synchronized void index(String media)throws Exception{
        initialize();Bitmap bitmap=db.bitmap(media,1600);Mat rgba=new Mat(),bgr=new Mat(),found=new Mat();
        try{
            Utils.bitmapToMat(bitmap,rgba);Imgproc.cvtColor(rgba,bgr,Imgproc.COLOR_RGBA2BGR);detector.setInputSize(new Size(bgr.cols(),bgr.rows()));detector.detect(bgr,found);
            ArrayList<Group> groups=groups();Set<String> assigned=new HashSet<>();
            for(int i=0;i<found.rows();i++){
                float[] box=new float[15];found.get(i,0,box);
                if(box[14]<.92||box[2]<30||box[3]<30||box[2]/box[3]<.5||box[2]/box[3]>1.6)continue;
                double eyes=Math.hypot(box[4]-box[6],box[5]-box[7]);if(eyes<box[2]*.13||eyes>box[2]*.8)continue;
                String face=Catalog.id(media+"/face/"+Math.round(box[0]/10)+"/"+Math.round(box[1]/10));
                // Rescanning never overwrites a user correction or rejection.
                if(db.count("SELECT COUNT(*) FROM faces WHERE id=? AND manual=1",face)>0)continue;
                Mat aligned=new Mat(),embedding=new Mat();float[] vector;
                try{recognizer.alignCrop(bgr,found.row(i),aligned);recognizer.feature(aligned,embedding);vector=new float[(int)embedding.total()];embedding.get(0,0,vector);normalize(vector);}finally{aligned.release();embedding.release();}
                Group best=null;double nearest=2,runner=2;
                for(Group g:groups){if(g.media.contains(media)||assigned.contains(g.id))continue;double d=g.nearest(vector);if(d<nearest){runner=nearest;nearest=d;best=g;}else runner=Math.min(runner,d);}
                String person;if(best!=null&&nearest<.32&&runner-nearest>.035){person=best.id;}else{person=Catalog.uuid();db.getWritableDatabase().execSQL("INSERT INTO people(id) VALUES(?)",new Object[]{person});Group g=new Group();g.id=person;g.name="";g.face=face;g.vectors.add(vector);g.media.add(media);groups.add(g);}
                assigned.add(person);
                db.getWritableDatabase().execSQL("INSERT OR REPLACE INTO faces(id,media,person,vector,confidence) VALUES(?,?,?,?,?)",new Object[]{face,media,person,pack(vector),box[14]});
                int x=Math.max(0,(int)(box[0]-box[2]*.12)),y=Math.max(0,(int)(box[1]-box[3]*.12));int w=Math.min(bitmap.getWidth()-x,(int)(box[2]*1.24)),h=Math.min(bitmap.getHeight()-y,(int)(box[3]*1.24));if(w<=0||h<=0)continue;
                Bitmap crop=Bitmap.createBitmap(bitmap,x,y,w,h),small=Bitmap.createScaledBitmap(crop,160,160,true);try(OutputStream out=new FileOutputStream(new File(db.faces,face+".jpg"))){small.compress(Bitmap.CompressFormat.JPEG,88,out);}finally{if(small!=crop)small.recycle();crop.recycle();}
            }
        }finally{bitmap.recycle();rgba.release();bgr.release();found.release();}
    }
    boolean cannotMerge(Group a,Group b){if(!a.name.isEmpty()&&!b.name.isEmpty()&&!a.name.equalsIgnoreCase(b.name))return true;if(!Collections.disjoint(a.media,b.media))return true;return db.count("SELECT COUNT(*) FROM negatives WHERE (a=? AND b=?) OR (a=? AND b=?)",a.id,b.id,b.id,a.id)>0;}
    double groupDistance(Group a,Group b){double best=2;for(float[] f:a.vectors)best=Math.min(best,b.nearest(f));return best;}
    JSONArray reviewPairs()throws Exception{
        ArrayList<Group> groups=groups();ArrayList<Object[]> candidates=new ArrayList<>();
        // Candidate search uses a per-group nearest neighbour, not every face-pair in the UI.
        for(int i=0;i<groups.size();i++){Group first=groups.get(i),best=null;double nearest=.48;for(int j=i+1;j<groups.size();j++){Group second=groups.get(j);if(cannotMerge(first,second))continue;if(db.count("SELECT COUNT(*) FROM reviews WHERE state='queued' AND ((a=? AND b=?) OR (a=? AND b=?))",first.id,second.id,second.id,first.id)>0)continue;double d=groupDistance(first,second);if(d<nearest){nearest=d;best=second;}}if(best!=null)candidates.add(new Object[]{first,best,nearest});}
        candidates.sort(Comparator.comparingDouble(v->(double)v[2]));JSONArray out=new JSONArray();for(int i=0;i<Math.min(24,candidates.size());i++){Object[] v=candidates.get(i);Group a=(Group)v[0],b=(Group)v[1];out.put(Catalog.object("a",a.id,"b",b.id,"firstFace",a.face,"secondFace",b.face,"firstName",a.name,"secondName",b.name,"distance",v[2]));}return out;
    }
    synchronized void feedback(String first,String second,boolean same)throws Exception{
        if(!same){db.getWritableDatabase().execSQL("INSERT OR IGNORE INTO negatives(a,b) VALUES(?,?)",new Object[]{first,second});return;}
        Group a=null,b=null;for(Group g:groups()){if(g.id.equals(first))a=g;if(g.id.equals(second))b=g;}if(a==null||b==null)return;
        // Explicit human confirmation can override a false co-occurrence detection, but not a prior 'different' answer.
        db.getWritableDatabase().execSQL("DELETE FROM negatives WHERE (a=? AND b=?) OR(a=? AND b=?)",new Object[]{first,second,second,first});
        merge(a,b);
        // Only confident nearby groups are absorbed; no single-link chain across unrelated identities.
        ArrayList<Group> all=groups();Group anchor=null;for(Group g:all)if(g.id.equals(a.id))anchor=g;
        if(anchor!=null)for(Group g:all){if(g.id.equals(anchor.id)||cannotMerge(anchor,g))continue;if(groupDistance(anchor,g)<.29)merge(anchor,g);}
    }
    void merge(Group a,Group b){db.getWritableDatabase().beginTransaction();try{db.getWritableDatabase().execSQL("UPDATE faces SET person=? WHERE person=?",new Object[]{a.id,b.id});db.getWritableDatabase().execSQL("UPDATE people SET name=?,me=? WHERE id=?",new Object[]{a.name.isEmpty()?b.name:a.name,a.me||b.me?1:0,a.id});db.getWritableDatabase().execSQL("INSERT OR IGNORE INTO negatives(a,b) SELECT ?,b FROM negatives WHERE a=?",new Object[]{a.id,b.id});db.getWritableDatabase().execSQL("INSERT OR IGNORE INTO negatives(a,b) SELECT a,? FROM negatives WHERE b=?",new Object[]{a.id,b.id});db.getWritableDatabase().execSQL("DELETE FROM people WHERE id=?",new Object[]{b.id});db.getWritableDatabase().setTransactionSuccessful();}finally{db.getWritableDatabase().endTransaction();}}
}
