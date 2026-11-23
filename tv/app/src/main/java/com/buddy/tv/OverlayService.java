package com.buddy.tv;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.PixelFormat;
import android.animation.ValueAnimator;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
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
    private WindowManager.LayoutParams params;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean hidden;
    private static final long HIDE_AFTER_MS = 60_000;

    /** Auto-hide: fades out after a minute of nothing, back the moment he's needed. */
    private final Runnable watch = new Runnable() {
        @Override
        public void run() {
            if (web != null) {
                boolean idle = System.currentTimeMillis() - BuddyWeb.lastAwake > HIDE_AFTER_MS;
                boolean hide = idle && BuddyWeb.autoHide(OverlayService.this);
                if (hide != hidden) fade(hide);
            }
            handler.postDelayed(this, 500);
        }
    };

    private void fade(boolean hide) {
        hidden = hide;
        ValueAnimator a = ValueAnimator.ofFloat(params.alpha, hide ? 0f : 1f);
        a.setDuration(hide ? 1200 : 250);
        a.addUpdateListener(v -> {
            if (web == null) return;
            params.alpha = (float) v.getAnimatedValue();
            windows.updateViewLayout(web, params);
        });
        a.start();
    }

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
        boolean talk = intent != null && (Talk.ACTION.equals(intent.getAction()) || intent.getBooleanExtra("talk", false));
        if (talk) BuddyWeb.lastAwake = System.currentTimeMillis();  // (show him straight away)
        if (web != null) {
            if (talk) web.evaluateJavascript("window.toggleTalk && toggleTalk()", null);
            return START_STICKY;
        }
        if (Settings.canDrawOverlays(this)) show(intent);
        if (web == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        running = true;
        handler.post(watch);
        if (talk) {  // started by the mic button: listen once his page has loaded
            handler.postDelayed(() -> {
                if (web != null) web.evaluateJavascript("window.toggleTalk && toggleTalk()", null);
            }, 3000);
        }
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
        params = p;
        hidden = false;
        BuddyWeb.lastAwake = System.currentTimeMillis();
        windows.addView(web, p);
    }

    @Override
    public void onDestroy() {
        running = false;
        handler.removeCallbacksAndMessages(null);
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
