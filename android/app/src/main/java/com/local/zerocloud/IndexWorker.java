package com.local.zerocloud;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import androidx.work.ForegroundInfo;
public final class IndexWorker extends Worker {
    public IndexWorker(Context c,WorkerParameters p){super(c,p);}
    @Override public Result doWork(){Catalog engine=Catalog.get(getApplicationContext());try{
        NotificationManager nm=(NotificationManager)getApplicationContext().getSystemService(Context.NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel("indexing","Background indexing",NotificationManager.IMPORTANCE_LOW));
        Notification notification=new Notification.Builder(getApplicationContext(),"indexing").setContentTitle("ZeroCloud").setContentText("Indexing locally · pause in Settings").setSmallIcon(android.R.drawable.ic_menu_gallery).setOngoing(true).build();
        setForegroundAsync(new ForegroundInfo(104,notification,android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)).get();
        engine.runWork();return Result.success();
    }catch(Exception e){engine.lastError=e.getMessage();engine.state="Needs attention";return Result.failure();}}
}
