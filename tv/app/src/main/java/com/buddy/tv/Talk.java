package com.buddy.tv;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

/**
 * "Talk to Buddy": start (or stop) listening, wherever he is: in the corner,
 * full screen, or not open yet (then he opens full screen and listens). For a
 * remote button (tvQuickActions: launch "Talk to Buddy") or an intent
 * (adb shell am broadcast -a com.buddy.tv.TALK).
 */
final class Talk {
    static final String ACTION = "com.buddy.tv.TALK";

    private Talk() {}

    static void toggle(Context c) {
        if (OverlayService.running) {
            Intent i = new Intent(c, OverlayService.class).setAction(ACTION);
            c.startForegroundService(i);
        } else if (MainActivity.current != null) {
            MainActivity.current.toggleTalk();
        } else if (android.provider.Settings.canDrawOverlays(c) && !BuddyWeb.savedUrl(c).isEmpty()) {
            // Not open (the TV was off, say): straight into the corner, listening.
            c.startForegroundService(new Intent(c, OverlayService.class).putExtra("talk", true));
        } else {
            Intent i = new Intent(c, MainActivity.class).putExtra("talk", true)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            c.startActivity(i);
        }
    }

    /** The launcher entry "Talk to Buddy": does it and is gone at once. */
    public static class Shortcut extends Activity {
        @Override
        protected void onCreate(Bundle state) {
            super.onCreate(state);
            toggle(getApplicationContext());
            finish();
            overridePendingTransition(0, 0);
        }
    }

    /** The same, as a broadcast (com.buddy.tv.TALK). */
    public static class Receiver extends BroadcastReceiver {
        @Override
        public void onReceive(Context c, Intent intent) {
            toggle(c.getApplicationContext());
        }
    }
}
