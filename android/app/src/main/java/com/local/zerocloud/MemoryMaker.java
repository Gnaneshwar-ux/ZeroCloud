package com.local.zerocloud;
import android.database.Cursor;
import android.graphics.*;
import android.media.*;
import org.json.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.util.*;

/** On-device MP4 slideshow with animated fitting, crossfades and a synthesized AAC score. */
final class MemoryMaker {
    static JSONArray options(Catalog db)throws Exception{
        JSONArray result=new JSONArray();JSONArray people=db.people();for(int i=0;i<people.length();i++){JSONObject p=people.getJSONObject(i);if(p.getInt("count")>=4)result.put(Catalog.object("id","person:"+p.getString("id"),"category","people","title",p.getString("name").isEmpty()?"Person "+(i+1):p.getString("name"),"count",p.getInt("count")));}
        try(Cursor c=db.getReadableDatabase().rawQuery("SELECT f.id,f.name,COUNT(*) FROM folders f JOIN media m ON m.folder=f.id WHERE m.kind IN('image','video') AND m.deleted=0 GROUP BY f.id HAVING COUNT(*)>=4",null)){while(c.moveToNext())result.put(Catalog.object("id","folder:"+c.getString(0),"category","trips","title",c.getString(1),"count",c.getInt(2)));}
        return result;
    }
    static Object queue(Catalog db,JSONObject args)throws Exception{
        JSONArray options=options(db);if(options.length()==0)throw new IOException("Add at least four indexed photos or videos from a person or folder first.");
        String selection=args.optString("selection");if(selection.isEmpty())selection=options.getJSONObject(new java.security.SecureRandom().nextInt(options.length())).getString("id");JSONObject subject=null;for(int i=0;i<options.length();i++)if(options.getJSONObject(i).getString("id").equals(selection))subject=options.getJSONObject(i);if(subject==null)throw new IOException("This memory subject is no longer available.");
        String where,parameter;if(selection.startsWith("person:")){where="m.id IN(SELECT media FROM faces WHERE person=? AND rejected=0)";parameter=selection.substring(7);}else{where="m.folder=?";parameter=selection.substring(7);}
        ArrayList<String> available=new ArrayList<>();try(Cursor c=db.getReadableDatabase().rawQuery("SELECT m.id FROM media m WHERE "+where+" AND m.deleted=0 AND m.kind IN('image','video') LIMIT 1000",new String[]{parameter})){while(c.moveToNext())available.add(c.getString(0));}
        Collections.shuffle(available,new java.security.SecureRandom());JSONArray selected=new JSONArray();for(int i=0;i<Math.min(10,available.size());i++)selected.put(available.get(i));if(selected.length()<4)throw new IOException("This story needs at least four available items.");
        String id=Catalog.uuid(),style=args.optString("style","daydream"),music=args.optString("music","joyful");if(style.equals("auto"))style=new String[]{"daydream","postcards","joyful","cinema"}[new java.security.SecureRandom().nextInt(4)];
        db.getWritableDatabase().execSQL("INSERT INTO reels(id,title,subject,style,music,status,created,items) VALUES(?,?,?,?,?,'queued',?,?)",new Object[]{id,subject.getString("title"),selection,style,music,System.currentTimeMillis(),selected.toString()});db.schedule();return Catalog.object("id",id);
    }
    static void run(Catalog db)throws Exception{
        try(Cursor c=db.getReadableDatabase().rawQuery("SELECT id,items,style,music FROM reels WHERE status='queued' ORDER BY created",null)){
            while(c.moveToNext()){
                if(db.paused())return;String id=c.getString(0);db.state="Creating memory";db.current=id;db.getWritableDatabase().execSQL("UPDATE reels SET status='rendering',error='' WHERE id=?",new Object[]{id});
                File video=new File(db.memories,id+".video.mp4"),audio=new File(db.memories,id+".audio.m4a"),target=new File(db.memories,id+".mp4"),partial=new File(db.memories,id+".part.mp4");
                try{
                    JSONArray items=new JSONArray(c.getString(1));String style=c.getString(2);int width=style.equals("cinema")?1280:720,height=style.equals("cinema")?720:1280;
                    encodeVideo(db,items,style,video,width,height);encodeAudio(db,items.length()*3,c.getString(3),audio);mux(video,audio,partial);if(!partial.renameTo(target))throw new IOException("Could not finalize reel.");db.getWritableDatabase().execSQL("UPDATE reels SET status='ready' WHERE id=?",new Object[]{id});
                }catch(Exception e){db.getWritableDatabase().execSQL("UPDATE reels SET status=?,error=? WHERE id=?",new Object[]{db.paused()?"queued":"failed",e.getMessage(),id});if(db.paused())return;}
                finally{video.delete();audio.delete();partial.delete();}
            }
        }
    }
    static void checkpoint(Catalog db)throws Exception{if(db.paused())throw new IOException("Paused");while(db.viewing){if(db.paused())throw new IOException("Paused");Thread.sleep(200);}}
    static void encodeVideo(Catalog db,JSONArray items,String style,File file,int w,int h)throws Exception{
        final int fps=24,perImage=72,frames=items.length()*perImage;MediaFormat format=MediaFormat.createVideoFormat("video/avc",w,h);format.setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);format.setInteger(MediaFormat.KEY_BIT_RATE,3_000_000);format.setInteger(MediaFormat.KEY_FRAME_RATE,fps);format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL,1);
        MediaCodec codec=MediaCodec.createEncoderByType("video/avc");MediaMuxer muxer=new MediaMuxer(file.getAbsolutePath(),MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);Bitmap canvasBitmap=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888),photo=null,next=null;Canvas canvas=new Canvas(canvasBitmap);Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG|Paint.FILTER_BITMAP_FLAG);int[] pixels=new int[w*h];boolean started=false;int track=-1,sent=0,loaded=-1;MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
        try{
            codec.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);codec.start();boolean ended=false;
            while(!ended){
                checkpoint(db);
                if(sent<=frames){int index=codec.dequeueInputBuffer(10000);if(index>=0){if(sent==frames){codec.queueInputBuffer(index,0,0,(long)frames*1_000_000/fps,MediaCodec.BUFFER_FLAG_END_OF_STREAM);sent++;}else{
                    int item=sent/perImage;if(item!=loaded){if(photo!=null)photo.recycle();if(next!=null)next.recycle();photo=db.bitmap(items.getString(item),1600);next=item+1<items.length()?db.bitmap(items.getString(item+1),1600):null;loaded=item;}
                    float t=(sent%perImage)/(float)perImage;canvas.drawColor(Color.rgb(12,18,28));draw(canvas,paint,photo,w,h,t,style,item,255);if(next!=null&&t>.80f)draw(canvas,paint,next,w,h,0,style,item+1,(int)((t-.80f)*5*255));
                    canvasBitmap.getPixels(pixels,0,w,0,0,w,h);Image input=codec.getInputImage(index);if(input==null)throw new IOException("Video encoder does not provide a supported YUV input layout.");try{yuv(input,pixels,w,h);}finally{input.close();}codec.queueInputBuffer(index,0,w*h*3/2,(long)sent*1_000_000/fps,0);sent++;
                }}}
                int output=codec.dequeueOutputBuffer(info,10000);if(output==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED){track=muxer.addTrack(codec.getOutputFormat());muxer.start();started=true;}else if(output>=0){ByteBuffer buffer=codec.getOutputBuffer(output);if(info.size>0&&(info.flags&MediaCodec.BUFFER_FLAG_CODEC_CONFIG)==0){if(!started||buffer==null)throw new IOException("Encoder output unavailable.");buffer.position(info.offset);buffer.limit(info.offset+info.size);muxer.writeSampleData(track,buffer,info);}ended=(info.flags&MediaCodec.BUFFER_FLAG_END_OF_STREAM)!=0;codec.releaseOutputBuffer(output,false);}
            }
        }finally{if(photo!=null)photo.recycle();if(next!=null)next.recycle();canvasBitmap.recycle();try{codec.stop();}catch(Exception ignored){}codec.release();if(started)try{muxer.stop();}catch(Exception ignored){}muxer.release();}
    }
    static void draw(Canvas canvas,Paint paint,Bitmap b,int w,int h,float t,String style,int item,int alpha){
        float fit=Math.min((float)w/b.getWidth(),(float)h/b.getHeight())*.94f;float zoom=style.equals("cinema")?1+t*.04f:1+t*.05f;float bw=b.getWidth()*fit*zoom,bh=b.getHeight()*fit*zoom;float dx=(w-bw)/2,dy=(h-bh)/2;float move=(t-.5f)*Math.min(w,h)*.018f;dx+=item%2==0?move:-move;dy+=item%3==0?move:0;paint.setAlpha(Math.max(0,Math.min(255,alpha)));canvas.drawBitmap(b,null,new RectF(dx,dy,dx+bw,dy+bh),paint);paint.setAlpha(255);
    }
    static void yuv(Image image,int[] pixels,int w,int h){Image.Plane[] planes=image.getPlanes();ByteBuffer y=planes[0].getBuffer(),u=planes[1].getBuffer(),v=planes[2].getBuffer();int yr=planes[0].getRowStride(),yp=planes[0].getPixelStride(),ur=planes[1].getRowStride(),up=planes[1].getPixelStride(),vr=planes[2].getRowStride(),vp=planes[2].getPixelStride();for(int row=0;row<h;row++)for(int col=0;col<w;col++){int color=pixels[row*w+col],r=(color>>16)&255,g=(color>>8)&255,b=color&255;int yy=((66*r+129*g+25*b+128)>>8)+16;y.put(row*yr+col*yp,(byte)Math.max(0,Math.min(255,yy)));if(row%2==0&&col%2==0){int uu=((-38*r-74*g+112*b+128)>>8)+128,vv=((112*r-94*g-18*b+128)>>8)+128;u.put((row/2)*ur+(col/2)*up,(byte)Math.max(0,Math.min(255,uu)));v.put((row/2)*vr+(col/2)*vp,(byte)Math.max(0,Math.min(255,vv)));}}}
    static void encodeAudio(Catalog db,int seconds,String mood,File file)throws Exception{
        int rate=44100,total=seconds*rate;MediaFormat format=MediaFormat.createAudioFormat("audio/mp4a-latm",rate,1);format.setInteger(MediaFormat.KEY_AAC_PROFILE,MediaCodecInfo.CodecProfileLevel.AACObjectLC);format.setInteger(MediaFormat.KEY_BIT_RATE,128000);MediaCodec codec=MediaCodec.createEncoderByType("audio/mp4a-latm");MediaMuxer muxer=new MediaMuxer(file.getAbsolutePath(),MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);boolean started=false,ended=false;int sent=0,track=-1;MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();int seed=new java.security.SecureRandom().nextInt(4);
        try{codec.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);codec.start();while(!ended){checkpoint(db);if(sent<=total){int index=codec.dequeueInputBuffer(10000);if(index>=0){ByteBuffer input=codec.getInputBuffer(index);if(input==null)throw new IOException("Audio input unavailable.");input.clear();if(sent==total){codec.queueInputBuffer(index,0,0,(long)sent*1_000_000/rate,MediaCodec.BUFFER_FLAG_END_OF_STREAM);sent++;}else{int n=Math.min(input.remaining()/2,total-sent);for(int i=0;i<n;i++){double t=(double)(sent+i)/rate,bpm=mood.equals("cinematic")?76:mood.equals("relaxed")?90:116,beat=t*bpm/60;int note=new int[]{0,4,7,12,7,4,2,9}[(int)(beat+seed)%8];double frequency=220*Math.pow(2,note/12.0),envelope=Math.exp(-4*(beat-Math.floor(beat)));double tone=Math.sin(2*Math.PI*frequency*t)*envelope*.22+Math.sin(2*Math.PI*frequency*.5*t)*.10;double fade=Math.min(1,Math.min(t/1.2,(seconds-t)/1.2));short value=(short)(Math.max(-1,Math.min(1,tone*fade))*32767);input.put((byte)(value&255));input.put((byte)((value>>8)&255));}codec.queueInputBuffer(index,0,n*2,(long)sent*1_000_000/rate,0);sent+=n;}}}
            int output=codec.dequeueOutputBuffer(info,10000);if(output==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED){track=muxer.addTrack(codec.getOutputFormat());muxer.start();started=true;}else if(output>=0){ByteBuffer b=codec.getOutputBuffer(output);if(info.size>0&&(info.flags&MediaCodec.BUFFER_FLAG_CODEC_CONFIG)==0){if(b==null||!started)throw new IOException("Audio output unavailable.");b.position(info.offset);b.limit(info.offset+info.size);muxer.writeSampleData(track,b,info);}ended=(info.flags&MediaCodec.BUFFER_FLAG_END_OF_STREAM)!=0;codec.releaseOutputBuffer(output,false);}
        }}finally{try{codec.stop();}catch(Exception ignored){}codec.release();if(started)try{muxer.stop();}catch(Exception ignored){}muxer.release();}
    }
    static void mux(File video,File audio,File target)throws Exception{
        MediaExtractor v=new MediaExtractor(),a=new MediaExtractor();MediaMuxer muxer=new MediaMuxer(target.getAbsolutePath(),MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);boolean started=false;
        try{v.setDataSource(video.getAbsolutePath());a.setDataSource(audio.getAbsolutePath());int vt=muxer.addTrack(v.getTrackFormat(0)),at=muxer.addTrack(a.getTrackFormat(0));muxer.start();started=true;copyTrack(v,muxer,vt);copyTrack(a,muxer,at);}finally{v.release();a.release();if(started)muxer.stop();muxer.release();}
    }
    static void copyTrack(MediaExtractor extractor,MediaMuxer muxer,int track){extractor.selectTrack(0);ByteBuffer buffer=ByteBuffer.allocate(4*1024*1024);MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();while(true){buffer.clear();int size=extractor.readSampleData(buffer,0);if(size<0)break;info.set(0,size,extractor.getSampleTime(),extractor.getSampleFlags());muxer.writeSampleData(track,buffer,info);extractor.advance();}}
}
