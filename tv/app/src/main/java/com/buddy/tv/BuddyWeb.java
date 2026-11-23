package com.buddy.tv;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.webkit.JavascriptInterface;
import android.os.Handler;
import android.os.Looper;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Buddy's page (the same one the phone uses, in TV mode) in a WebView. */
final class BuddyWeb {
    static final String PREFS = "buddy";
    static final String KEY_URL = "url";

    /** Asks for the microphone (only the full-screen app can show that question). */
    interface MicAsker {
        void askForMic(PermissionRequest request);
    }

    private BuddyWeb() {}

    static String savedUrl(Context c) {
        return prefs(c).getString(KEY_URL, "");
    }

    static void saveUrl(Context c, String url) {
        prefs(c).edit().putString(KEY_URL, clean(url)).apply();
    }

    /** "absdesktop.lemur-copperhead.ts.net/" -> "https://absdesktop.lemur-copperhead.ts.net" */
    static String clean(String url) {
        String u = url == null ? "" : url.trim();
        if (u.isEmpty()) return "";
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "https://" + u;
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u;
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    @SuppressLint("SetJavaScriptEnabled")
    static WebView make(Context c, String pageUrl, MicAsker asker) {
        WebView web = new WebView(c);
        web.setBackgroundColor(Color.BLACK);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);  // he talks without a tap
        // For the page: which microphones the TV lets apps use (to say why talking
        // on the TV doesn't work, when it doesn't).
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public String mics() {
                StringBuilder out = new StringBuilder();
                AudioManager am = c.getSystemService(AudioManager.class);
                for (AudioDeviceInfo d : am.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
                    if (out.length() > 0) out.append(", ");
                    out.append(d.getProductName()).append(" (type ").append(d.getType()).append(")");
                }
                return out.toString();
            }

            private final Recorder recorder = new Recorder();

            @JavascriptInterface
            public String startRec() {
                if (c.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    return "not allowed (run install-tv-app.bat again)";
                }
                return recorder.start();
            }

            @JavascriptInterface
            public double recLevel() {
                return recorder.level();
            }

            @JavascriptInterface
            public String stopRec() {
                return recorder.stop();
            }

            @JavascriptInterface
            public String micAllowed() {
                return c.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED ? "yes" : "no";
            }
        }, "BuddyTV");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    // The PC is off or Tailscale isn't connected yet: try again soon.
                    view.loadData("<body style='background:#000;color:#ccc;font:5vh sans-serif;"
                            + "display:flex;align-items:center;justify-content:center;text-align:center'>"
                            + "Can't reach Buddy yet.<br>Is the PC on, and Tailscale connected on the TV?</body>",
                            "text/html", "utf-8");
                    new Handler(Looper.getMainLooper()).postDelayed(() -> view.loadUrl(pageUrl), 15000);
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                boolean wantsMic = false;
                for (String r : request.getResources()) {
                    if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) wantsMic = true;
                }
                if (!wantsMic) {
                    request.deny();
                } else if (c.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED) {
                    request.grant(new String[] {PermissionRequest.RESOURCE_AUDIO_CAPTURE});
                } else if (asker != null) {
                    asker.askForMic(request);
                } else {
                    request.deny();
                }
            }
        });
        web.loadUrl(pageUrl);
        return web;
    }
}
