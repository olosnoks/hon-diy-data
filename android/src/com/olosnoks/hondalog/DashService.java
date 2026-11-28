package com.olosnoks.hondalog;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Keeps the app's process (and the page in it) running at foreground priority
 * with the CPU awake while hondalog is open, so logging goes on behind other
 * apps and with the screen off. The notification's buttons open the app's
 * settings or quit.
 */
public final class DashService extends Service {
    static final String ACTION_QUIT = "com.olosnoks.hondalog.QUIT";
    static final String ACTION_SETTINGS = "com.olosnoks.hondalog.SETTINGS";
    private PowerManager.WakeLock wake;

    static void start(Context c) {
        Compat.startForegroundService(c, new Intent(c, DashService.class));
    }

    static void stop(Context c) {
        c.stopService(new Intent(c, DashService.class));
    }

    @Override public void onCreate() {
        super.onCreate();
        Compat.ensureChannel(this);
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "hondalog:logging");
        wake.setReferenceCounted(false);
        wake.acquire();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(1, notification());
        return START_STICKY;
    }

    @SuppressWarnings("deprecation")
    private Notification notification() {
        Intent open = new Intent(this, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        Intent settings = new Intent(this, MainActivity.class).setAction(ACTION_SETTINGS)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        Intent quit = new Intent(this, MainActivity.class).setAction(ACTION_QUIT)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return Compat.builder(this)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle("hondalog")
                .setContentText("Reading the ECU and logging, also behind other apps")
                .setOngoing(true)
                .setShowWhen(false)
                .setContentIntent(PendingIntent.getActivity(this, 0, open, Compat.immutableFlags()))
                .addAction(0, "App settings", PendingIntent.getActivity(this, 1, settings, Compat.immutableFlags()))
                .addAction(0, "Quit", PendingIntent.getActivity(this, 2, quit, Compat.immutableFlags()))
                .build();
    }

    @Override public void onDestroy() {
        if (wake != null && wake.isHeld()) wake.release();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
