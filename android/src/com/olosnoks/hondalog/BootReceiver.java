package com.olosnoks.hondalog;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Opens hondalog when the radio boots, if "Start with the radio" is on in App settings. */
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent intent) {
        if (!c.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE).getBoolean(MainActivity.PREF_AUTOSTART, false)) return;
        DashService.start(c);
        try {
            c.startActivity(new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (Exception ignored) {
            // Android 10+ refuses unless "Display over other apps" is allowed; the service still runs.
        }
    }
}
