package com.local.zerocloud;

import android.content.Context;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageDecoder;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.provider.DocumentsContract;
import android.webkit.WebResourceResponse;
import androidx.documentfile.provider.DocumentFile;
import androidx.exifinterface.media.ExifInterface;
import androidx.work.OneTimeWorkRequest;
import androidx.work.ExistingWorkPolicy;
import androidx.work.WorkManager;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Local catalog: originals are referenced by persisted document URI, never imported/copied. */
public final class Catalog extends SQLiteOpenHelper {
    private static Catalog instance;
    public static synchronized Catalog get(Context c){if(instance==null)instance=new Catalog(c.getApplicationContext());return instance;}
    final Context context;
    final File previews,faces,memories;
    final AtomicBoolean running=new AtomicBoolean();
    volatile String current="",state="Ready",lastError="";
    volatile boolean viewing;
    final Set<String> priority=java.util.concurrent.ConcurrentHashMap.newKeySet();
    FaceEngine faceEngine;
    Catalog(Context c){super(c,"catalog.sqlite",null,1);context=c;setWriteAheadLoggingEnabled(true);previews=new File(c.getCacheDir(),"previews");faces=new File(c.getFilesDir(),"faces");memories=new File(c.getFilesDir(),"memories");previews.mkdirs();faces.mkdirs();memories.mkdirs();}
    @Override public void onCreate(SQLiteDatabase d){
        d.execSQL("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)");
        d.execSQL("CREATE TABLE folders(id TEXT PRIMARY KEY,uri TEXT UNIQUE,name TEXT,cache TEXT DEFAULT '',scan INTEGER DEFAULT 1,online INTEGER DEFAULT 1)");
        d.execSQL("CREATE TABLE media(id TEXT PRIMARY KEY,folder TEXT,uri TEXT,relative TEXT,name TEXT,mime TEXT,kind TEXT,size INTEGER,modified INTEGER,version TEXT,captured INTEGER DEFAULT 0,latitude REAL,longitude REAL,favorite INTEGER DEFAULT 0,checked INTEGER DEFAULT 0,error TEXT DEFAULT '',seen TEXT,deleted INTEGER DEFAULT 0)");
        d.execSQL("CREATE INDEX media_folder ON media(folder)");d.execSQL("CREATE INDEX media_date ON media(captured DESC)");
        d.execSQL("CREATE TABLE people(id TEXT PRIMARY KEY,name TEXT DEFAULT '',me INTEGER DEFAULT 0)");
        d.execSQL("CREATE TABLE faces(id TEXT PRIMARY KEY,media TEXT,person TEXT,vector BLOB,confidence REAL,rejected INTEGER DEFAULT 0,manual INTEGER DEFAULT 0)");
        d.execSQL("CREATE INDEX faces_person ON faces(person)");d.execSQL("CREATE INDEX faces_media ON faces(media)");
        d.execSQL("CREATE TABLE negatives(a TEXT,b TEXT,PRIMARY KEY(a,b))");
        d.execSQL("CREATE TABLE reviews(id TEXT PRIMARY KEY,a TEXT,b TEXT,same INTEGER,state TEXT DEFAULT 'queued',error TEXT DEFAULT '')");
        d.execSQL("CREATE TABLE reels(id TEXT PRIMARY KEY,title TEXT,subject TEXT,style TEXT,music TEXT,status TEXT,created INTEGER,error TEXT DEFAULT '',items TEXT)");
        d.execSQL("CREATE TABLE backups(id TEXT PRIMARY KEY,source TEXT,destination TEXT,name TEXT,sha256 TEXT,size INTEGER,created INTEGER)");
        d.execSQL("CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT,args TEXT,state TEXT DEFAULT 'queued',done INTEGER DEFAULT 0,total INTEGER DEFAULT 0,error TEXT DEFAULT '')");
    }
    @Override public void onUpgrade(SQLiteDatabase d,int from,int to){throw new IllegalStateException("Unsupported catalog upgrade; existing data has not been changed.");}
    String setting(String key,String fallback){try(Cursor c=getReadableDatabase().rawQuery("SELECT value FROM settings WHERE key=?",new String[]{key})){return c.moveToFirst()?c.getString(0):fallback;}}
    void setSetting(String key,String value){ContentValues v=new ContentValues();v.put("key",key);v.put("value",value);getWritableDatabase().insertWithOnConflict("settings",null,v,SQLiteDatabase.CONFLICT_REPLACE);}
    boolean paused(){return setting("paused","false").equals("true");}
    public void schedule(){WorkManager.getInstance(context).enqueueUniqueWork("zerocloud-index",ExistingWorkPolicy.APPEND_OR_REPLACE,new OneTimeWorkRequest.Builder(IndexWorker.class).build());}
    static JSONObject object(Object...args)throws Exception{JSONObject o=new JSONObject();for(int i=0;i<args.length;i+=2)o.put((String)args[i],args[i+1]==null?JSONObject.NULL:args[i+1]);return o;}
    static String hex(byte[] bytes){StringBuilder out=new StringBuilder(bytes.length*2);for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
    static String id(String value)throws Exception{return hex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))).substring(0,32);}
    static String uuid(){return UUID.randomUUID().toString();}
    long count(String sql,String...args){try(Cursor c=getReadableDatabase().rawQuery(sql,args)){return c.moveToFirst()?c.getLong(0):0;}}
    synchronized Object request(String op,JSONObject a)throws Exception{
        if(a==null)a=new JSONObject();
        switch(op){
            case "load":return load(a);
            case "status":return status();
            case "pause":setSetting("paused",String.valueOf(a.optBoolean("paused")));if(!paused())schedule();return status();
            case "viewing":viewing=a.optBoolean("value");return true;
            case "priority":{JSONArray ids=a.optJSONArray("ids");if(ids!=null)for(int i=0;i<ids.length();i++)priority.add(ids.getString(i));return true;}
            case "favorite":{getWritableDatabase().execSQL("UPDATE media SET favorite=? WHERE id=?",new Object[]{a.optBoolean("value")?1:0,a.getString("id")});return true;}
            case "rescan":getWritableDatabase().execSQL("UPDATE folders SET scan=1");schedule();return true;
            case "rescanLocations":getWritableDatabase().execSQL("UPDATE media SET checked=0,error='' WHERE deleted=0");schedule();return true;
            case "rescanFaces":getWritableDatabase().execSQL("UPDATE media SET checked=0,error='' WHERE kind='image' AND deleted=0");schedule();return true;
            case "people":return people();
            case "renamePerson":getWritableDatabase().execSQL("UPDATE people SET name=? WHERE id=?",new Object[]{a.optString("name").trim(),a.getString("id")});return true;
            case "setMe":{getWritableDatabase().beginTransaction();try{getWritableDatabase().execSQL("UPDATE people SET me=0");getWritableDatabase().execSQL("UPDATE people SET me=1 WHERE id=?",new Object[]{a.getString("id")});getWritableDatabase().setTransactionSuccessful();}finally{getWritableDatabase().endTransaction();}return true;}
            case "reviews":return reviewPairs();
            case "review":{String first=a.getString("a"),second=a.getString("b");if(first.equals(second))throw new Exception("Choose two different people.");String rid=uuid();getWritableDatabase().execSQL("INSERT INTO reviews(id,a,b,same) VALUES(?,?,?,?)",new Object[]{rid,first,second,a.getBoolean("same")?1:0});schedule();return object("queued",rid);}
            case "appearances":return appearances(a.getString("person"));
            case "editFace":{String fid=a.getString("id"),person=a.optString("person");if(a.optBoolean("reject")){getWritableDatabase().execSQL("UPDATE faces SET rejected=1,manual=1 WHERE id=?",new Object[]{fid});}else{if(person.isEmpty()){person=uuid();getWritableDatabase().execSQL("INSERT INTO people(id,name) VALUES(?,?)",new Object[]{person,a.optString("name")});}if(count("SELECT COUNT(*) FROM people WHERE id=?",person)==0)throw new Exception("Target person is no longer available.");getWritableDatabase().execSQL("UPDATE faces SET person=?,manual=1 WHERE id=?",new Object[]{person,fid});}return true;}
            case "locations":return locations();
            case "delete":return delete(a);
            case "storage":return storage();
            case "cacheBudget":{int mb=a.optInt("mb",256);if(mb<64||mb>1024)throw new Exception("Cache budget must be 64–1024 MB.");setSetting("cacheMB",String.valueOf(mb));trimCache();return storage();}
            case "portableSync":exportPortable();return true;
            case "detachFolder":getWritableDatabase().execSQL("UPDATE folders SET online=0 WHERE id=?",new Object[]{a.getString("id")});return true;
            case "memoryOptions":return memoryOptions();
            case "memories":return reels();
            case "createMemory":return createMemory(a);
            case "backups":return backups();
            case "backupStart":return backupStart(a);
            case "restore":return restore(a);
            default:throw new Exception("Unsupported Android operation: "+op);
        }
    }
    Object picked(String op,Uri uri,JSONObject args)throws Exception{
        switch(op){
            case "chooseFolder":{
                DocumentFile tree=DocumentFile.fromTreeUri(context,uri);if(tree==null||!tree.canRead())throw new Exception("This folder cannot be read. Choose a folder supported by Android’s file provider.");
                String folder=uuid();try(Cursor c=getReadableDatabase().rawQuery("SELECT id FROM folders WHERE uri=?",new String[]{uri.toString()})){if(c.moveToFirst()){folder=c.getString(0);getWritableDatabase().execSQL("UPDATE folders SET scan=1,online=1 WHERE id=?",new Object[]{folder});}else getWritableDatabase().execSQL("INSERT INTO folders(id,uri,name) VALUES(?,?,?)",new Object[]{folder,uri.toString(),tree.getName()});}
                schedule();return object("id",folder,"name",tree.getName());
            }
            case "chooseCache":{
                DocumentFile tree=DocumentFile.fromTreeUri(context,uri);if(tree==null||!tree.canWrite())throw new Exception("Select a writable cache folder.");
                String source=args==null?"":args.optString("folder");if(source.isEmpty())setSetting("portableCache",uri.toString());else getWritableDatabase().execSQL("UPDATE folders SET cache=? WHERE id=?",new Object[]{uri.toString(),source});return true;
            }
            case "chooseBackup":setSetting("backupDestination",uri.toString());return true;
            case "exportWorkspace":try(OutputStream out=context.getContentResolver().openOutputStream(uri,"wt")){if(out==null)throw new Exception("Cannot write workspace.");out.write(workspace().toString().getBytes(StandardCharsets.UTF_8));}return true;
            case "importWorkspace":{try(InputStream in=context.getContentResolver().openInputStream(uri)){JSONObject doc=new JSONObject(readText(in,8_000_000));if(!doc.optString("format").equals("zerocloud-android-workspace")||doc.optInt("version")!=1)throw new Exception("Choose a supported Android workspace export. Windows workspaces require a separate import adapter.");JSONArray list=doc.getJSONArray("folders");if(list.length()>200)throw new Exception("Too many folders in workspace.");for(int i=0;i<list.length();i++){JSONObject f=list.getJSONObject(i);Uri root=Uri.parse(f.getString("uri"));if(!"content".equals(root.getScheme()))throw new Exception("Invalid folder URI.");DocumentFile tree=DocumentFile.fromTreeUri(context,root);ContentValues v=new ContentValues();v.put("id",f.getString("id"));v.put("uri",root.toString());v.put("name",f.optString("name","Folder"));v.put("cache",f.optString("cache"));v.put("online",tree!=null&&tree.canRead()?1:0);v.put("scan",1);getWritableDatabase().insertWithOnConflict("folders",null,v,SQLiteDatabase.CONFLICT_IGNORE);}schedule();return true;}}
            default:throw new Exception("Unknown file picker operation.");
        }
    }
    static String readText(InputStream in,int limit)throws Exception{if(in==null)throw new IOException("File unavailable.");ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buf=new byte[8192];int n;while((n=in.read(buf))>=0){if(out.size()+n>limit)throw new IOException("File exceeds safe import limit.");out.write(buf,0,n);}return new String(out.toByteArray(),StandardCharsets.UTF_8);}
    JSONObject workspace()throws Exception{return object("format","zerocloud-android-workspace","version",1,"folders",folders(),"cache",setting("portableCache",""));}
    JSONArray folders()throws Exception{JSONArray a=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT id,uri,name,cache,online,scan FROM folders",null)){while(c.moveToNext())a.put(object("id",c.getString(0),"uri",c.getString(1),"name",c.getString(2),"cache",c.getString(3),"online",c.getInt(4)==1,"scanning",c.getInt(5)==1,"count",count("SELECT COUNT(*) FROM media WHERE folder=? AND deleted=0",c.getString(0))));}return a;}
    JSONObject load(JSONObject a)throws Exception{
        String where="m.deleted=0";ArrayList<String> params=new ArrayList<>();
        String q=a.optString("query").trim();if(!q.isEmpty()){where+=" AND m.name LIKE ? ESCAPE '\\'";params.add("%"+q.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%");}
        if(a.optBoolean("favorite"))where+=" AND m.favorite=1";
        for(String key:new String[]{"folder","kind"})if(!a.optString(key).isEmpty()){where+=" AND m."+key+"=?";params.add(a.getString(key));}
        if(!a.optString("person").isEmpty()){where+=" AND m.id IN(SELECT media FROM faces WHERE person=? AND rejected=0)";params.add(a.getString("person"));}
        if(a.optBoolean("located"))where+=" AND m.latitude IS NOT NULL";
        if(a.optLong("from")>0){where+=" AND m.captured>=?";params.add(String.valueOf(a.getLong("from")));}
        if(a.optLong("to")>0){where+=" AND m.captured<=?";params.add(String.valueOf(a.getLong("to")));}
        long total=count("SELECT COUNT(*) FROM media m WHERE "+where,params.toArray(new String[0]));
        JSONArray items=new JSONArray();int limit=Math.max(1,Math.min(200,a.optInt("limit",120))),offset=Math.max(0,a.optInt("offset"));
        try(Cursor c=getReadableDatabase().rawQuery("SELECT m.* FROM media m WHERE "+where+" ORDER BY m.captured DESC,m.name COLLATE NOCASE,m.id LIMIT "+limit+" OFFSET "+offset,params.toArray(new String[0]))){while(c.moveToNext())items.put(media(c));}
        return object("items",items,"total",total,"folders",folders());
    }
    JSONObject media(Cursor c)throws Exception{return object("id",str(c,"id"),"name",str(c,"name"),"kind",str(c,"kind"),"mime",str(c,"mime"),"size",num(c,"size"),"date",num(c,"captured"),"favorite",num(c,"favorite")==1,"error",str(c,"error"),"folder",str(c,"folder"),"relative",str(c,"relative"));}
    static String str(Cursor c,String col){return c.getString(c.getColumnIndexOrThrow(col));}
    static long num(Cursor c,String col){return c.getLong(c.getColumnIndexOrThrow(col));}
    JSONObject status()throws Exception{return object("state",paused()?"Paused":state,"current",current,"paused",paused(),"error",lastError,"done",count("SELECT COUNT(*) FROM media WHERE checked=1 AND deleted=0"),"total",count("SELECT COUNT(*) FROM media WHERE kind IN('image','video') AND deleted=0"),"failed",count("SELECT COUNT(*) FROM media WHERE error!='' AND deleted=0"),"people",count("SELECT COUNT(DISTINCT person) FROM faces WHERE rejected=0"),"gps",count("SELECT COUNT(*) FROM media WHERE latitude IS NOT NULL AND deleted=0"),"reviews",count("SELECT COUNT(*) FROM reviews WHERE state='queued'"),"running",running.get(),"jobs",jobs());}
    JSONArray jobs()throws Exception{JSONArray a=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 20",null)){while(c.moveToNext())a.put(object("id",str(c,"id"),"kind",str(c,"kind"),"state",str(c,"state"),"done",num(c,"done"),"total",num(c,"total"),"error",str(c,"error")));}return a;}
    void runWork()throws Exception{
        if(!running.compareAndSet(false,true))return;
        try {
            // Interrupted jobs restart safely; verified copy records make copying idempotent.
            getWritableDatabase().execSQL("UPDATE jobs SET state='queued' WHERE state='running'");
            getWritableDatabase().execSQL("UPDATE reels SET status='queued' WHERE status='rendering'");
            drainReviews();
            if(paused()){state="Paused";return;}
            try(Cursor c=getReadableDatabase().rawQuery("SELECT id,uri FROM folders WHERE scan=1",null)){while(c.moveToNext()){if(paused())return;scan(c.getString(0),Uri.parse(c.getString(1)));}}
            while(!paused()){
                drainReviews();String selected=null;
                for(String p:priority)if(count("SELECT COUNT(*) FROM media WHERE id=? AND checked=0 AND deleted=0",p)>0){selected=p;break;}
                if(selected==null)try(Cursor c=getReadableDatabase().rawQuery("SELECT id FROM media WHERE checked=0 AND deleted=0 AND kind IN('image','video') ORDER BY captured DESC LIMIT 1",null)){if(c.moveToFirst())selected=c.getString(0);}
                if(selected==null)break;
                if(viewing){state="Waiting while you view media";Thread.sleep(250);continue;}
                process(selected);priority.remove(selected);
            }
            if(!paused())runJobs();
            state=paused()?"Paused":"Up to date";current="";trimCache();
        }finally{running.set(false);}
    }
    void scan(String folder,Uri tree)throws Exception{
        state="Discovering media";String generation=uuid();boolean complete=false;
        try{
            ArrayDeque<String[]> pending=new ArrayDeque<>();pending.add(new String[]{DocumentsContract.getTreeDocumentId(tree),""});Set<String> visited=new HashSet<>();
            while(!pending.isEmpty()){
                if(paused())return;String[] directory=pending.removeFirst();if(!visited.add(directory[0]))continue;
                Uri children=DocumentsContract.buildChildDocumentsUriUsingTree(tree,directory[0]);
                String[] cols={DocumentsContract.Document.COLUMN_DOCUMENT_ID,DocumentsContract.Document.COLUMN_DISPLAY_NAME,DocumentsContract.Document.COLUMN_MIME_TYPE,DocumentsContract.Document.COLUMN_SIZE,DocumentsContract.Document.COLUMN_LAST_MODIFIED};
                try(Cursor c=context.getContentResolver().query(children,cols,null,null,null)){
                    if(c==null)throw new IOException("The folder provider did not respond.");
                    while(c.moveToNext()){
                        if(paused())return;String document=c.getString(0),name=c.getString(1),mime=c.getString(2),relative=directory[1]+name;current=relative;
                        if(DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)){if(name.startsWith(".zerocloud"))continue;pending.add(new String[]{document,relative+"/"});continue;}
                        String kind=kind(name,mime);if(kind.isEmpty())continue;
                        String mid=id(folder+"/"+relative),version=c.getLong(3)+":"+c.getLong(4),uri=DocumentsContract.buildDocumentUriUsingTree(tree,document).toString();
                        ContentValues v=new ContentValues();v.put("id",mid);v.put("folder",folder);v.put("uri",uri);v.put("relative",relative);v.put("name",name);v.put("mime",mimeFor(name,mime));v.put("kind",kind);v.put("size",c.getLong(3));v.put("modified",c.getLong(4));v.put("version",version);v.put("seen",generation);v.put("deleted",0);
                        if(getWritableDatabase().insertWithOnConflict("media",null,v,SQLiteDatabase.CONFLICT_IGNORE)==-1){String oldVersion="";try(Cursor old=getReadableDatabase().rawQuery("SELECT version FROM media WHERE id=?",new String[]{mid})){if(old.moveToFirst())oldVersion=old.getString(0);}if(!version.equals(oldVersion)){v.put("checked",0);v.put("error","");}v.remove("id");getWritableDatabase().update("media",v,"id=?",new String[]{mid});}
                    }
                }
            }
            complete=true;
        }catch(Exception e){lastError="Folder unavailable: "+e.getMessage();getWritableDatabase().execSQL("UPDATE folders SET online=0 WHERE id=?",new Object[]{folder});}
        if(complete){getWritableDatabase().execSQL("UPDATE folders SET scan=0,online=1 WHERE id=?",new Object[]{folder});getWritableDatabase().execSQL("UPDATE media SET deleted=1 WHERE folder=? AND seen!=?",new Object[]{folder,generation});}
    }
    static String ext(String name){int dot=name.lastIndexOf('.');return dot<0?"":name.substring(dot+1).toLowerCase(Locale.ROOT);}
    static String kind(String name,String mime){String e=ext(name);if(mime!=null&&mime.startsWith("image/")||Arrays.asList("jpg","jpeg","png","heic","heif","avif","webp","gif","bmp","tif","tiff","dng","cr2","cr3","nef","arw","orf","raf","rw2","jxl","jp2","psd","svg").contains(e))return "image";if(mime!=null&&mime.startsWith("video/")||Arrays.asList("mp4","mov","mkv","avi","webm","m4v","3gp","mts","m2ts").contains(e))return "video";if(mime!=null&&mime.startsWith("audio/"))return "audio";if(Arrays.asList("pdf","txt","doc","docx").contains(e))return "document";return "";}
    static String mimeFor(String name,String value){if(value!=null&&!value.equals("application/octet-stream"))return value;String mime=android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext(name));return mime==null?"application/octet-stream":mime;}
    Uri original(String id)throws Exception{try(Cursor c=getReadableDatabase().rawQuery("SELECT uri FROM media WHERE id=? AND deleted=0",new String[]{id})){if(!c.moveToFirst())throw new IOException("This item is not in the active catalog.");return Uri.parse(c.getString(0));}}
    Bitmap bitmap(String id,int maximum)throws Exception{
        Uri uri=original(id);String kind="";try(Cursor c=getReadableDatabase().rawQuery("SELECT kind FROM media WHERE id=?",new String[]{id})){if(c.moveToFirst())kind=c.getString(0);}
        if(kind.equals("video")){try(MediaMetadataRetriever retriever=new MediaMetadataRetriever()){retriever.setDataSource(context,uri);Bitmap b=retriever.getScaledFrameAtTime(1_000_000,MediaMetadataRetriever.OPTION_CLOSEST_SYNC,maximum,maximum);if(b==null)throw new IOException("No video frame can be decoded on this device.");return b;}}
        try{return ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.getContentResolver(),uri),(decoder,info,src)->{decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE);int w=info.getSize().getWidth(),h=info.getSize().getHeight();double scale=Math.min(1.0,(double)maximum/Math.max(w,h));decoder.setTargetSize(Math.max(1,(int)(w*scale)),Math.max(1,(int)(h*scale)));});}
        catch(Exception first){try(InputStream in=context.getContentResolver().openInputStream(uri)){BitmapFactory.Options opts=new BitmapFactory.Options();opts.inJustDecodeBounds=true;BitmapFactory.decodeStream(in,null,opts);int sample=1;while(Math.max(opts.outWidth,opts.outHeight)/sample>maximum)sample*=2;opts.inJustDecodeBounds=false;opts.inSampleSize=sample;try(InputStream second=context.getContentResolver().openInputStream(uri)){Bitmap b=BitmapFactory.decodeStream(second,null,opts);if(b==null)throw new IOException("This format requires a decoder unavailable on this Android device. Original file retained.",first);return b;}}}
    }
    File preview(String id,int maximum)throws Exception{
        String version="";try(Cursor c=getReadableDatabase().rawQuery("SELECT version FROM media WHERE id=?",new String[]{id})){if(!c.moveToFirst())throw new IOException("Unknown media.");version=c.getString(0);}
        File file=new File(previews,id+"-"+id(version)+"-"+maximum+".jpg");if(file.isFile()){file.setLastModified(System.currentTimeMillis());return file;}
        // Distinct temporary names protect concurrent thumbnail and foreground requests.
        File temp=new File(previews,file.getName()+"."+uuid()+".part");Bitmap bitmap=bitmap(id,maximum);
        try(FileOutputStream out=new FileOutputStream(temp)){if(!bitmap.compress(Bitmap.CompressFormat.JPEG,maximum>1000?95:83,out))throw new IOException("Image encoding failed.");}finally{bitmap.recycle();}
        if(!temp.renameTo(file)){temp.delete();if(!file.isFile())throw new IOException("Could not save preview.");}return file;
    }
    void process(String id)throws Exception{
        state="Indexing metadata and people";current=id;String error="";
        try {
            Uri uri=original(id);long captured=0;Double lat=null,lon=null;
            try(InputStream in=context.getContentResolver().openInputStream(uri)){ExifInterface exif=new ExifInterface(in);Long date=exif.getDateTimeOriginal();if(date!=null)captured=date;double[] gps=exif.getLatLong();if(gps!=null){lat=gps[0];lon=gps[1];}}
            catch(Exception ignored){try(MediaMetadataRetriever r=new MediaMetadataRetriever()){r.setDataSource(context,uri);String date=r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DATE);if(date!=null){SimpleDateFormat sdf=new SimpleDateFormat("yyyyMMdd'T'HHmmss",Locale.ROOT);sdf.setTimeZone(TimeZone.getTimeZone("UTC"));Date parsed=sdf.parse(date);if(parsed!=null)captured=parsed.getTime();}}catch(Exception ignoredToo){}}
            ContentValues v=new ContentValues();if(captured>0)v.put("captured",captured);if(lat!=null){v.put("latitude",lat);v.put("longitude",lon);}getWritableDatabase().update("media",v,"id=?",new String[]{id});
            preview(id,512);
            if(count("SELECT COUNT(*) FROM media WHERE id=? AND kind='image'",id)>0){if(faceEngine==null)faceEngine=new FaceEngine(this);faceEngine.index(id);}
        }catch(Exception e){error=e.getMessage()==null?e.toString():e.getMessage();}
        getWritableDatabase().execSQL("UPDATE media SET checked=1,error=? WHERE id=?",new Object[]{error,id});
    }
    WebResourceResponse resource(Uri uri,String range){
        try{
            java.util.List<String> path=uri.getPathSegments();if(path.size()!=3)return MainActivity.error(404,"Not found");String type=path.get(1),id=path.get(2);if(!id.matches("[a-zA-Z0-9-]{1,64}"))return MainActivity.error(400,"Invalid catalog ID");
            InputStream input;String mime;long size;File file=null;
            if(type.equals("thumb")||type.equals("view")){file=preview(id,type.equals("thumb")?512:4096);mime="image/jpeg";}
            else if(type.equals("face")){file=new File(faces,id+".jpg");mime="image/jpeg";}
            else if(type.equals("memory")){file=new File(memories,id+".mp4");mime="video/mp4";}
            else if(type.equals("original")){Uri original=original(id);input=context.getContentResolver().openInputStream(original);if(input==null)throw new IOException("Reconnect the original folder.");try(Cursor c=getReadableDatabase().rawQuery("SELECT mime,size FROM media WHERE id=?",new String[]{id})){c.moveToFirst();mime=c.getString(0);size=c.getLong(1);}return stream(mime,size,input,range);}
            else return MainActivity.error(404,"Not found");
            if(!file.isFile())throw new IOException("Cached media is unavailable.");input=new FileInputStream(file);size=file.length();return stream(mime,size,input,range);
        }catch(Exception e){return MainActivity.error(404,"Media unavailable");}
    }
    static WebResourceResponse stream(String mime,long size,InputStream in,String range)throws Exception{
        Map<String,String> headers=new HashMap<>();headers.put("Accept-Ranges","bytes");headers.put("Cache-Control","private, max-age=3600");headers.put("X-Content-Type-Options","nosniff");
        if(range!=null&&size>0){try{long[] bounds=Ranges.parse(range,size);long start=bounds[0],end=bounds[1];long skipped=0;while(skipped<start){long n=in.skip(start-skipped);if(n<=0){if(in.read()<0)throw new EOFException();n=1;}skipped+=n;}long length=end-start+1;headers.put("Content-Range","bytes "+start+"-"+end+"/"+size);headers.put("Content-Length",String.valueOf(length));return new WebResourceResponse(mime,null,206,"Partial Content",headers,new LimitedStream(in,length));}catch(IllegalArgumentException e){in.close();headers.put("Content-Range","bytes */"+size);return new WebResourceResponse("text/plain","utf-8",416,"Range Not Satisfiable",headers,new ByteArrayInputStream(new byte[0]));}}
        if(size>0)headers.put("Content-Length",String.valueOf(size));return new WebResourceResponse(mime,null,200,"OK",headers,in);
    }
    static final class LimitedStream extends FilterInputStream{long left;LimitedStream(InputStream in,long size){super(in);left=size;}@Override public int read()throws IOException{if(left<=0)return -1;int r=super.read();if(r>=0)left--;return r;}@Override public int read(byte[] b,int off,int len)throws IOException{if(left<=0)return -1;int n=super.read(b,off,(int)Math.min(len,left));if(n>0)left-=n;return n;}}
    JSONArray people()throws Exception{JSONArray a=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT p.id,p.name,p.me,COUNT(DISTINCT f.media),MIN(f.id) FROM people p JOIN faces f ON f.person=p.id JOIN media m ON m.id=f.media WHERE f.rejected=0 AND m.deleted=0 GROUP BY p.id ORDER BY p.me DESC,COUNT(DISTINCT f.media) DESC",null)){while(c.moveToNext())a.put(object("id",c.getString(0),"name",c.getString(1),"me",c.getInt(2)==1,"count",c.getInt(3),"face",c.getString(4)));}return a;}
    JSONArray appearances(String person)throws Exception{JSONArray a=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT f.id,f.media,m.name FROM faces f JOIN media m ON m.id=f.media WHERE f.person=? AND f.rejected=0 AND m.deleted=0 LIMIT 200",new String[]{person})){while(c.moveToNext())a.put(object("id",c.getString(0),"media",c.getString(1),"name",c.getString(2)));}return a;}
    JSONArray locations()throws Exception{JSONArray a=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT id,latitude,longitude FROM media WHERE latitude IS NOT NULL AND deleted=0 LIMIT 10000",null)){while(c.moveToNext())a.put(object("id",c.getString(0),"lat",c.getDouble(1),"lon",c.getDouble(2)));}return a;}
    JSONArray reviewPairs()throws Exception{if(faceEngine==null)faceEngine=new FaceEngine(this);return faceEngine.reviewPairs();}
    void drainReviews()throws Exception{try(Cursor c=getReadableDatabase().rawQuery("SELECT id,a,b,same FROM reviews WHERE state='queued' ORDER BY rowid",null)){while(c.moveToNext()){String rid=c.getString(0);try{if(faceEngine==null)faceEngine=new FaceEngine(this);faceEngine.feedback(c.getString(1),c.getString(2),c.getInt(3)==1);getWritableDatabase().execSQL("UPDATE reviews SET state='done' WHERE id=?",new Object[]{rid});}catch(Exception e){getWritableDatabase().execSQL("UPDATE reviews SET state='failed',error=? WHERE id=?",new Object[]{e.getMessage(),rid});}}}}
    Object delete(JSONObject a)throws Exception{JSONArray ids=a.getJSONArray("ids"),errors=new JSONArray();int removed=0;for(int i=0;i<ids.length();i++){String id=ids.getString(i);try{if(!DocumentsContract.deleteDocument(context.getContentResolver(),original(id)))throw new IOException("Provider refused deletion.");getWritableDatabase().execSQL("UPDATE media SET deleted=1 WHERE id=?",new Object[]{id});removed++;}catch(Exception e){errors.put(object("id",id,"error",e.getMessage()));}}return object("removed",removed,"errors",errors);}
    void share(android.app.Activity activity,JSONObject a)throws Exception{JSONArray ids=a.getJSONArray("ids");ArrayList<Uri> uris=new ArrayList<>();for(int i=0;i<ids.length();i++)uris.add(original(ids.getString(i)));if(uris.isEmpty())throw new Exception("Select an item first.");Intent intent=new Intent(Intent.ACTION_SEND_MULTIPLE).setType("*/*").putParcelableArrayListExtra(Intent.EXTRA_STREAM,uris).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);android.content.ClipData clip=android.content.ClipData.newRawUri("ZeroCloud media",uris.get(0));for(int i=1;i<uris.size();i++)clip.addItem(new android.content.ClipData.Item(uris.get(i)));intent.setClipData(clip);activity.startActivity(Intent.createChooser(intent,"Share selected media"));}
    JSONObject storage()throws Exception{return object("cacheBytes",directoryBytes(previews),"faceBytes",directoryBytes(faces),"reelBytes",directoryBytes(memories),"budgetMB",Integer.parseInt(setting("cacheMB","256")),"portable",setting("portableCache",""),"backup",setting("backupDestination",""),"originalCopies",0);}
    static long directoryBytes(File dir){long n=0;File[] files=dir.listFiles();if(files!=null)for(File f:files)n+=f.length();return n;}
    void trimCache(){long limit=Long.parseLong(setting("cacheMB","256"))*1048576,total=directoryBytes(previews);File[] files=previews.listFiles();if(files==null)return;Arrays.sort(files,Comparator.comparingLong(File::lastModified));for(File f:files){if(total<=limit)break;long size=f.length();if(f.delete())total-=size;}}
    void exportPortable()throws Exception{String selected=setting("portableCache","");if(selected.isEmpty())throw new Exception("Choose a portable cache folder first.");DocumentFile root=DocumentFile.fromTreeUri(context,Uri.parse(selected));if(root==null||!root.canWrite())throw new IOException("Reconnect the cache folder.");DocumentFile app=root.findFile(".zerocloud-android");if(app==null)app=root.createDirectory(".zerocloud-android");if(app==null)throw new IOException("Cannot create cache folder.");DocumentFile file=app.findFile("workspace.json");if(file==null)file=app.createFile("application/json","workspace.json");if(file==null)throw new IOException("Cannot create workspace export.");try(OutputStream out=context.getContentResolver().openOutputStream(file.getUri(),"wt")){if(out==null)throw new IOException("Cannot write export.");out.write(workspace().toString().getBytes(StandardCharsets.UTF_8));}}
    // Durable media jobs are implemented separately from UI control requests.
    JSONArray memoryOptions()throws Exception{return MemoryMaker.options(this);}
    JSONArray reels()throws Exception{JSONArray a=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT * FROM reels ORDER BY created DESC",null)){while(c.moveToNext())a.put(object("id",str(c,"id"),"title",str(c,"title"),"status",str(c,"status"),"error",str(c,"error"),"style",str(c,"style"),"music",str(c,"music")));}return a;}
    Object createMemory(JSONObject a)throws Exception{return MemoryMaker.queue(this,a);}
    JSONArray backups()throws Exception{return BackupJobs.list(this);}
    Object backupStart(JSONObject a)throws Exception{return BackupJobs.queue(this,a,false);}
    Object restore(JSONObject a)throws Exception{return BackupJobs.queue(this,a,true);}
    void runJobs()throws Exception{BackupJobs.run(this);MemoryMaker.run(this);}
}
