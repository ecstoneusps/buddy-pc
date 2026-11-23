package com.buddy.tv;

import android.Manifest;
import android.app.Activity;
import android.app.PictureInPictureParams;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.util.Rational;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.PermissionRequest;
import android.webkit.WebView;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Buddy full screen on the TV. Back (or Home) puts him in a corner: a window
 * floating over everything (if "display over other apps" is allowed), else
 * Android's picture-in-picture. Hold Back to close him. OK talks to him.
 */
public class MainActivity extends Activity implements BuddyWeb.MicAsker {
    private static final int MIC_REQUEST = 1;
    static MainActivity current;  // (for "Talk to Buddy" while he's full screen)
    private WebView web;
    private PermissionRequest pendingMic;
    private boolean closing;
    private boolean longBack;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        stopService(new Intent(this, OverlayService.class));  // back from the corner: full screen
        takeUrl(getIntent());
        start();
        current = this;
        talkSoonIfAsked(getIntent());
    }

    /** Opened by "Talk to Buddy": listen once the page has loaded. */
    private void talkSoonIfAsked(Intent intent) {
        if (intent != null && intent.getBooleanExtra("talk", false) && web != null) {
            web.postDelayed(this::toggleTalk, 2500);
        }
    }

    void toggleTalk() {
        if (web != null) web.evaluateJavascript("window.toggleTalk && toggleTalk()", null);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        stopService(new Intent(this, OverlayService.class));
        if (takeUrl(intent)) start();
        talkSoonIfAsked(intent);
    }

    /** "adb shell am start -n com.buddy.tv/.MainActivity --es url https://..." sets the address. */
    private boolean takeUrl(Intent intent) {
        String url = intent == null ? null : intent.getStringExtra("url");
        if (url == null || url.trim().isEmpty()) return false;
        BuddyWeb.saveUrl(this, url);
        return true;
    }

    private void start() {
        String url = BuddyWeb.savedUrl(this);
        if (url.isEmpty()) {
            showSetup();
            return;
        }
        if (web != null) web.destroy();
        web = BuddyWeb.make(this, url + "/?tv&app", this);
        setContentView(web);
        web.requestFocus();
    }

    /** First time: where Buddy is (his Tailscale address). */
    private void showSetup() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(Color.BLACK);
        box.setPadding(120, 60, 120, 60);
        TextView t = new TextView(this);
        t.setText("Buddy's address (the one your phone uses), like\nabsdesktop.lemur-copperhead.ts.net");
        t.setTextColor(Color.LTGRAY);
        t.setTextSize(22);
        t.setGravity(Gravity.CENTER);
        EditText e = new EditText(this);
        e.setSingleLine(true);
        e.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        e.setImeOptions(EditorInfo.IME_ACTION_DONE);
        e.setTextColor(Color.WHITE);
        e.setTextSize(24);
        Button b = new Button(this);
        b.setText("Save");
        b.setOnClickListener(v -> {
            BuddyWeb.saveUrl(this, e.getText().toString());
            start();
        });
        box.addView(t);
        box.addView(e, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        box.addView(b);
        setContentView(box);
        e.requestFocus();
    }

    // ---- The remote's Back: short = corner, held = close ----------------------

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_MENU && web != null) {  // the remote's menu button
            boolean on = BuddyWeb.toggleAutoHide(this);
            web.evaluateJavascript("status('Auto-hide in the corner: " + (on ? "on (after a minute of quiet)" : "off")
                    + "')", null);
            return true;
        }
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null) {
            event.startTracking();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public boolean onKeyLongPress(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            longBack = true;
            closing = true;
            finishAndRemoveTask();
            return true;
        }
        return super.onKeyLongPress(keyCode, event);
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null) {
            if (!longBack && event.isTracking() && !event.isCanceled()) goToCorner();
            longBack = false;
            return true;
        }
        return super.onKeyUp(keyCode, event);
    }

    @Override
    protected void onUserLeaveHint() {  // Home, or another input/app chosen
        super.onUserLeaveHint();
        if (!closing && web != null) goToCorner();
    }

    private void goToCorner() {
        if (Settings.canDrawOverlays(this)) {
            // Floats over everything, the Apple TV's input included.
            Intent i = new Intent(this, OverlayService.class);
            i.putExtra("url", BuddyWeb.savedUrl(this));
            startForegroundService(i);
            closing = true;
            finish();
        } else if (getPackageManager().hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)) {
            enterPictureInPictureMode(new PictureInPictureParams.Builder()
                    .setAspectRatio(new Rational(4, 3)).build());
        } else {
            moveTaskToBack(true);
        }
    }

    @Override
    public void onPictureInPictureModeChanged(boolean inPip, Configuration config) {
        super.onPictureInPictureModeChanged(inPip, config);
        if (web != null) web.evaluateJavascript("window.setPip && setPip(" + inPip + ")", null);
    }

    @Override
    protected void onStop() {
        super.onStop();
        // The picture-in-picture window was closed: he's done.
        if (isInPictureInPictureMode()) finish();
    }

    // ---- The microphone (OK on the remote talks to him) ------------------------

    @Override
    public void askForMic(PermissionRequest request) {
        pendingMic = request;
        requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, MIC_REQUEST);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code != MIC_REQUEST || pendingMic == null) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            pendingMic.grant(new String[] {PermissionRequest.RESOURCE_AUDIO_CAPTURE});
        } else {
            pendingMic.deny();
        }
        pendingMic = null;
    }

    @Override
    protected void onDestroy() {
        if (current == this) current = null;
        if (web != null) web.destroy();
        web = null;
        super.onDestroy();
    }
}
