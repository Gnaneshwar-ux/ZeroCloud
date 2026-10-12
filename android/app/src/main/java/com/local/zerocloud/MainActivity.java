package com.local.zerocloud;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.webkit.WebViewAssetLoader;
import org.json.JSONObject;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

/** Only the packaged app origin can reach this bridge. No remote page runs in this WebView. */
public final class MainActivity extends Activity {
    private WebView web;
    private Catalog engine;
    private final ExecutorService foreground=Executors.newFixedThreadPool(2);
    private final ExecutorService controls=Executors.newSingleThreadExecutor();
    private JSONObject picker;
    private static final String ORIGIN="https://appassets.androidplatform.net";
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        engine=Catalog.get(getApplicationContext());
        web=new WebView(this);
        web.setBackgroundColor(0xff101827);
        setContentView(web);
        web.setOnApplyWindowInsetsListener((v,insets)->{
            if(android.os.Build.VERSION.SDK_INT>=30){ android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.ime()); v.setPadding(bars.left,bars.top,bars.right,bars.bottom); }
            else v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets;
        });
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setMediaPlaybackRequiresUserGesture(true);
        web.getSettings().setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        web.addJavascriptInterface(new Bridge(),"ZeroCloudNative");
        WebViewAssetLoader loader=new WebViewAssetLoader.Builder().addPathHandler("/assets/",new WebViewAssetLoader.AssetsPathHandler(this)).build();
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){
                Uri u=r.getUrl();
                if(!r.isForMainFrame())return true;
                if("https".equals(u.getScheme())&&"appassets.androidplatform.net".equals(u.getHost()))return false;
                if("https".equals(u.getScheme()))startActivity(new Intent(Intent.ACTION_VIEW,u));
                return true;
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest r){
                Uri u=r.getUrl();
                if(!"https".equals(u.getScheme())||!"appassets.androidplatform.net".equals(u.getHost()))return error(403,"Blocked");
                if(u.getPath().startsWith("/native/"))return engine.resource(u,r.getRequestHeaders().get("Range"));
                WebResourceResponse response=loader.shouldInterceptRequest(u);
                if(response==null)return error(404,"Not found");
                java.util.Map<String,String> headers=new java.util.HashMap<>();
                headers.put("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'");
                headers.put("X-Content-Type-Options","nosniff");response.setResponseHeaders(headers);return response;
            }
        });
        web.loadUrl(ORIGIN+"/assets/index.html");
        engine.schedule();
    }
    static WebResourceResponse error(int code,String message){return new WebResourceResponse("text/plain","utf-8",code,message,new java.util.HashMap<>(),new java.io.ByteArrayInputStream(message.getBytes(java.nio.charset.StandardCharsets.UTF_8)));}
    final class Bridge {
        @JavascriptInterface public void request(String raw){
            if(raw.length()>2_000_000)return;
            try {
                JSONObject request=new JSONObject(raw);String op=request.getString("op");
                if(op.equals("chooseFolder")||op.equals("chooseCache")||op.equals("chooseBackup")||op.equals("exportWorkspace")||op.equals("importWorkspace")){
                    runOnUiThread(()->pick(request));return;
                }
                if(op.equals("delete")){
                    runOnUiThread(()->new AlertDialog.Builder(MainActivity.this).setTitle("Delete original files?").setMessage("The selected originals will be permanently deleted from their folder. Android folder providers may not have a recycle bin. Your backup is not deleted.").setNegativeButton("Cancel",(d,w)->reply(request,null,null)).setPositiveButton("Delete originals",(d,w)->controls.execute(()->dispatch(request))).show());return;
                }
                if(op.equals("share")){runOnUiThread(()->{try{engine.share(MainActivity.this,request.optJSONObject("args"));reply(request,true,null);}catch(Exception e){reply(request,null,e.getMessage());}});return;}
                (op.equals("load")||op.equals("status")?foreground:controls).execute(()->dispatch(request));
            }catch(Exception ignored){}
        }
    }
    private void dispatch(JSONObject request){try{reply(request,engine.request(request.getString("op"),request.optJSONObject("args")),null);}catch(Exception e){reply(request,null,e.getMessage()==null?e.toString():e.getMessage());}}
    private void reply(JSONObject request,Object value,String error){
        try{JSONObject msg=new JSONObject().put("id",request.getLong("id")).put("result",value==null?JSONObject.NULL:value);if(error!=null)msg.put("error",error);
            runOnUiThread(()->{if(web!=null)web.evaluateJavascript("window.nativeReply("+msg+")",null);});
        }catch(Exception ignored){}
    }
    private void pick(JSONObject request){
        if(picker!=null){reply(request,null,"Finish the open folder picker first.");return;}
        picker=request;String op=request.optString("op");Intent intent;
        if(op.equals("exportWorkspace")){intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/json").putExtra(Intent.EXTRA_TITLE,"ZeroCloud-workspace.json");}
        else if(op.equals("importWorkspace")){intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/json").addCategory(Intent.CATEGORY_OPENABLE);}
        else {intent=new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);intent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION|Intent.FLAG_GRANT_PREFIX_URI_PERMISSION);}
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        try{startActivityForResult(intent,100);}catch(Exception e){picker=null;reply(request,null,e.getMessage());}
    }
    @Override protected void onActivityResult(int code,int result,Intent data){
        super.onActivityResult(code,result,data);if(code!=100||picker==null)return;
        JSONObject request=picker;picker=null;
        if(result!=RESULT_OK||data==null||data.getData()==null){reply(request,null,null);return;}
        Uri uri=data.getData();String op=request.optString("op");
        if(!op.equals("exportWorkspace")&&!op.equals("importWorkspace"))getContentResolver().takePersistableUriPermission(uri,data.getFlags()&(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION));
        controls.execute(()->{try{reply(request,engine.picked(op,uri,request.optJSONObject("args")),null);}catch(Exception e){reply(request,null,e.getMessage());}});
    }
    @Override public void onBackPressed(){web.evaluateJavascript("window.androidBack()",v->{if(!"true".equals(v))super.onBackPressed();});}
    @Override protected void onDestroy(){if(web!=null){web.removeJavascriptInterface("ZeroCloudNative");web.destroy();web=null;}foreground.shutdown();controls.shutdown();super.onDestroy();}
}
