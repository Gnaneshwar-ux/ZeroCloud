package com.local.zerocloud;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import android.content.Context;
import android.graphics.*;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.database.Cursor;
import org.junit.*;
import org.junit.runner.RunWith;
import org.json.*;
import java.io.*;
import static org.junit.Assert.*;

/** Synthetic test media only; never loads the developer's media library. */
@RunWith(AndroidJUnit4.class) public class NativeSmokeTest {
 Context context;Catalog db;
 @Before public void setup(){context=InstrumentationRegistry.getInstrumentation().getTargetContext();db=Catalog.get(context);db.setSetting("paused","false");}
 @Test public void sqlitePersistsFavoriteAndSource()throws Exception{
   String id=Catalog.uuid();db.getWritableDatabase().execSQL("INSERT INTO folders(id,uri,name,scan) VALUES(?,?,?,0)",new Object[]{id,"content://test/"+id,"Synthetic source"});
   assertTrue(db.folders().toString().contains(id));db.request("pause",new JSONObject().put("paused",true));assertTrue(db.status().getBoolean("paused"));db.request("pause",new JSONObject().put("paused",false));
 }
 @Test public void faceDistancesAreNormalized(){assertEquals(0,FaceEngine.distance(new float[]{1,0},new float[]{8,0}),.0001);assertEquals(1,FaceEngine.distance(new float[]{1,0},new float[]{0,1}),.0001);}
 @Test public void decodeAndFaceDetectorRejectSyntheticScenery()throws Exception{
   String id=synthetic(0);Bitmap b=db.bitmap(id,512);assertEquals(512,b.getWidth());b.recycle();assertTrue(db.preview(id,512).isFile());FaceEngine face=new FaceEngine(db);face.index(id);assertEquals(0,db.count("SELECT COUNT(*) FROM faces WHERE media=?",id));
 }
 @Test public void memoryEncodesPlayableVideoAndAudio()throws Exception{
   JSONArray list=new JSONArray();for(int i=0;i<4;i++)list.put(synthetic(i));File video=new File(context.getCacheDir(),"test-reel-video.mp4"),audio=new File(context.getCacheDir(),"test-reel-audio.m4a"),target=new File(context.getCacheDir(),"test-reel.mp4");
   MemoryMaker.encodeVideo(db,list,"daydream",video,320,568);MemoryMaker.encodeAudio(db,12,"joyful",audio);MemoryMaker.mux(video,audio,target);
   try(MediaMetadataRetriever r=new MediaMetadataRetriever()){r.setDataSource(target.getAbsolutePath());assertTrue(Long.parseLong(r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION))>=11000);assertEquals("yes",r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_HAS_AUDIO));Bitmap frame=r.getFrameAtTime(1_000_000);assertNotNull(frame);frame.recycle();}
 }
 String synthetic(int index)throws Exception{
   String id=Catalog.uuid();File file=new File(context.getCacheDir(),id+".png");Bitmap bitmap=Bitmap.createBitmap(800,600,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(bitmap);canvas.drawColor(Color.rgb(30+index*30,80,160));Paint p=new Paint();p.setColor(Color.GREEN);canvas.drawRect(0,400,800,600,p);try(OutputStream out=new FileOutputStream(file)){bitmap.compress(Bitmap.CompressFormat.PNG,100,out);}bitmap.recycle();
   db.getWritableDatabase().execSQL("INSERT INTO media(id,folder,uri,relative,name,mime,kind,size,modified,version) VALUES(?,?,?,?,?,'image/png','image',?,0,'1')",new Object[]{id,"synthetic",Uri.fromFile(file).toString(),file.getName(),file.getName(),file.length()});return id;
 }
}
