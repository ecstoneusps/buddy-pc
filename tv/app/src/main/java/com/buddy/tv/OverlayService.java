package com.buddy.tv;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.DisplayMetrics;
import android.view.Gravity;
import android.view.WindowManager;
import android.webkit.WebView;

/** Buddy in a small window floating in the corner, over whatever is on the TV. */
public class OverlayService extends Service {
    private static final String CHANNEL = "buddy";
    static volatile boolean running;
    private WindowManager windows;
    private WebView web;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Buddy", NotificationManager.IMPORTANCE_LOW));
        Notification n = new Notification.Builder(this, CHANNEL)
                .setContentTitle("Buddy is in the corner")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, n);
        }
        if (intent != null && Talk.ACTION.equals(intent.getAction())) {
            if (web != null) web.evaluateJavascript("window.toggleTalk && toggleTalk()", null);
            return START_STICKY;
        }
        if (web == null && Settings.canDrawOverlays(this)) show(intent);
        if (web == null) stopSelf();
        running = web != null;
        return START_STICKY;
    }

    private void show(Intent intent) {
        String url = intent != null && intent.getStringExtra("url") != null
                ? intent.getStringExtra("url") : BuddyWeb.savedUrl(this);
        if (url.isEmpty()) return;
        windows = getSystemService(WindowManager.class);
        DisplayMetrics dm = getResources().getDisplayMetrics();
        int w = Math.round(dm.widthPixels * 0.2f);
        int h = w * 3 / 4;
        WindowManager.LayoutParams p = new WindowManager.LayoutParams(w, h,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        p.gravity = Gravity.BOTTOM | Gravity.END;
        p.x = Math.round(dm.widthPixels * 0.025f);
        p.y = Math.round(dm.heightPixels * 0.04f);
        web = BuddyWeb.make(this, url + "/?tv&corner", null);
        windows.addView(web, p);
    }

    @Override
    public void onDestroy() {
        running = false;
        if (web != null) {
            windows.removeView(web);
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
