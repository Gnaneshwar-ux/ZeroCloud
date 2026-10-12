package com.local.zerocloud;
import android.database.Cursor;
import android.net.Uri;
import androidx.documentfile.provider.DocumentFile;
import org.json.*;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;

/** Non-destructive, streaming SHA-256 verified copies. Restore never overwrites an existing file. */
final class BackupJobs {
    static Object queue(Catalog db,JSONObject args,boolean restore)throws Exception{
        if(db.setting("backupDestination","").isEmpty())throw new IOException("Choose a backup destination folder first.");
        String id=Catalog.uuid();JSONObject copy=new JSONObject(args.toString());copy.put("destination",db.setting("backupDestination",""));
        db.getWritableDatabase().execSQL("INSERT INTO jobs(id,kind,args) VALUES(?,?,?)",new Object[]{id,restore?"restore":"backup",copy.toString()});db.schedule();return Catalog.object("id",id);
    }
    static JSONArray list(Catalog db)throws Exception{JSONArray a=new JSONArray();try(Cursor c=db.getReadableDatabase().rawQuery("SELECT * FROM backups ORDER BY created DESC LIMIT 300",null)){while(c.moveToNext())a.put(Catalog.object("id",Catalog.str(c,"id"),"name",Catalog.str(c,"name"),"size",Catalog.num(c,"size"),"sha256",Catalog.str(c,"sha256"),"created",Catalog.num(c,"created")));}return a;}
    static void run(Catalog db)throws Exception{
        try(Cursor jobs=db.getReadableDatabase().rawQuery("SELECT id,kind,args FROM jobs WHERE state='queued' ORDER BY rowid",null)){
            while(jobs.moveToNext()){
                if(db.paused())return;String id=jobs.getString(0),kind=jobs.getString(1);JSONObject args=new JSONObject(jobs.getString(2));db.getWritableDatabase().execSQL("UPDATE jobs SET state='running',error='' WHERE id=?",new Object[]{id});
                try{if(kind.equals("backup"))backup(db,id,args);else restore(db,id,args);if(db.paused()){db.getWritableDatabase().execSQL("UPDATE jobs SET state='queued' WHERE id=?",new Object[]{id});return;}db.getWritableDatabase().execSQL("UPDATE jobs SET state='done' WHERE id=?",new Object[]{id});}
                catch(Exception e){if(db.paused()){db.getWritableDatabase().execSQL("UPDATE jobs SET state='queued' WHERE id=?",new Object[]{id});return;}db.getWritableDatabase().execSQL("UPDATE jobs SET state='failed',error=? WHERE id=?",new Object[]{e.getMessage(),id});}
            }
        }
    }
    static DocumentFile destination(Catalog db,JSONObject args)throws Exception{DocumentFile root=DocumentFile.fromTreeUri(db.context,Uri.parse(args.getString("destination")));if(root==null||!root.canWrite())throw new IOException("Reconnect the writable destination folder.");return root;}
    static DocumentFile directory(DocumentFile parent,String name)throws Exception{DocumentFile child=parent.findFile(name);if(child==null)child=parent.createDirectory(name);if(child==null||!child.isDirectory())throw new IOException("Cannot create destination directory: "+name);return child;}
    static void backup(Catalog db,String job,JSONObject args)throws Exception{
        DocumentFile root=directory(destination(db,args),"ZeroCloud Backup");String folder=args.optString("folder");String where="deleted=0";String[] params=null;if(!folder.isEmpty()){where+=" AND folder=?";params=new String[]{folder};}
        db.getWritableDatabase().execSQL("UPDATE jobs SET total=? WHERE id=?",new Object[]{db.count("SELECT COUNT(*) FROM media WHERE "+where,params==null?new String[0]:params),job});int done=0;
        try(Cursor c=db.getReadableDatabase().rawQuery("SELECT id,folder,uri,relative,mime,version FROM media WHERE "+where+" ORDER BY folder,relative",params)){
            while(c.moveToNext()){
                if(db.paused())return;String mid=c.getString(0),version=c.getString(5),key=Catalog.id(mid+"/"+version+"/"+args.getString("destination"));
                if(db.count("SELECT COUNT(*) FROM backups WHERE id=?",key)==0){
                    String[] parts=c.getString(3).split("/");DocumentFile parent=directory(root,c.getString(1));for(int i=0;i<parts.length-1;i++)parent=directory(parent,parts[i]);String name=parts[parts.length-1];
                    if(parent.findFile(name)!=null){int dot=name.lastIndexOf('.');name=(dot<0?name:name.substring(0,dot))+"-"+version.replace(':','-')+(dot<0?"":name.substring(dot));}
                    db.current="Backing up "+name;DocumentFile file=copyVerified(db,Uri.parse(c.getString(2)),parent,name,c.getString(4));String hash=digest(db,file.getUri());
                    db.getWritableDatabase().execSQL("INSERT INTO backups(id,source,destination,name,sha256,size,created) VALUES(?,?,?,?,?,?,?)",new Object[]{key,mid,file.getUri().toString(),c.getString(3),hash,file.length(),System.currentTimeMillis()});
                }
                db.getWritableDatabase().execSQL("UPDATE jobs SET done=? WHERE id=?",new Object[]{++done,job});
            }
        }
    }
    static DocumentFile copyVerified(Catalog db,Uri source,DocumentFile parent,String name,String mime)throws Exception{
        if(parent.findFile(name)!=null)throw new IOException("Destination already contains "+name+". Existing files are not overwritten.");
        DocumentFile temp=parent.createFile(mime,name+".part-"+Catalog.uuid());if(temp==null)throw new IOException("Provider cannot create backup file.");boolean keep=false;
        try{
            MessageDigest sourceHash=MessageDigest.getInstance("SHA-256");
            try(InputStream in=db.context.getContentResolver().openInputStream(source);OutputStream out=db.context.getContentResolver().openOutputStream(temp.getUri(),"w")){
                if(in==null||out==null)throw new IOException("Could not open copy streams.");byte[] b=new byte[131072];int n;while((n=in.read(b))>=0){if(db.paused())throw new IOException("Paused");out.write(b,0,n);sourceHash.update(b,0,n);}
            }
            if(!Catalog.hex(sourceHash.digest()).equals(digest(db,temp.getUri())))throw new IOException("Backup checksum verification failed; original retained.");
            if(!temp.renameTo(name))throw new IOException("Provider cannot finalize verified copy.");keep=true;return temp;
        }finally{if(!keep)temp.delete();}
    }
    static String digest(Catalog db,Uri uri)throws Exception{MessageDigest hash=MessageDigest.getInstance("SHA-256");try(InputStream in=db.context.getContentResolver().openInputStream(uri)){if(in==null)throw new IOException("Cannot read file for verification.");byte[] b=new byte[131072];int n;while((n=in.read(b))>=0){if(db.paused())throw new IOException("Paused");hash.update(b,0,n);}}return Catalog.hex(hash.digest());}
    static void restore(Catalog db,String job,JSONObject args)throws Exception{
        DocumentFile root=directory(destination(db,args),"ZeroCloud Restored");JSONArray ids=args.getJSONArray("ids");db.getWritableDatabase().execSQL("UPDATE jobs SET total=? WHERE id=?",new Object[]{ids.length(),job});
        for(int i=0;i<ids.length();i++){
            if(db.paused())return;try(Cursor c=db.getReadableDatabase().rawQuery("SELECT destination,name,sha256 FROM backups WHERE id=?",new String[]{ids.getString(i)})){
                if(!c.moveToFirst())throw new IOException("Backup record is unavailable.");Uri source=Uri.parse(c.getString(0));if(!digest(db,source).equals(c.getString(2)))throw new IOException("Backup has changed; restore stopped to protect data.");String[] parts=c.getString(1).split("/");DocumentFile parent=root;for(int j=0;j<parts.length-1;j++)parent=directory(parent,parts[j]);db.current="Restoring "+parts[parts.length-1];copyVerified(db,source,parent,parts[parts.length-1],Catalog.mimeFor(parts[parts.length-1],null));
                db.getWritableDatabase().execSQL("UPDATE jobs SET done=? WHERE id=?",new Object[]{i+1,job});
            }
        }
    }
}
