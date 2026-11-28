package com.olosnoks.hondalog;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.webkit.WebView;

import java.lang.reflect.Constructor;

/**
 * Calls newer than API 23 (the android.jar this is compiled against), by
 * reflection, so the app builds from distro packages with no SDK download.
 */
final class Compat {
    private Compat() {}

    static final String CHANNEL = "hondalog";
    static final int FLAG_MUTABLE = 0x02000000;      // PendingIntent.FLAG_MUTABLE (API 31)
    static final int FLAG_IMMUTABLE = 0x04000000;    // PendingIntent.FLAG_IMMUTABLE (API 23)

    /** Notification channel (API 26+), low importance: no sound, no pop-up. */
    static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT < 26) return;
        try {
            Class<?> ch = Class.forName("android.app.NotificationChannel");
            Object channel = ch.getConstructor(String.class, CharSequence.class, int.class)
                    .newInstance(CHANNEL, "hondalog logging", 2 /* IMPORTANCE_LOW */);
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            NotificationManager.class.getMethod("createNotificationChannel", ch).invoke(nm, channel);
        } catch (Exception ignored) {
        }
    }

    @SuppressWarnings("deprecation")
    static Notification.Builder builder(Context ctx) {
        if (Build.VERSION.SDK_INT >= 26) {
            try {
                Constructor<Notification.Builder> c = Notification.Builder.class.getConstructor(Context.class, String.class);
                return c.newInstance(ctx, CHANNEL);
            } catch (Exception ignored) {
            }
        }
        return new Notification.Builder(ctx).setPriority(Notification.PRIORITY_LOW);
    }

    static void startForegroundService(Context ctx, Intent i) {
        if (Build.VERSION.SDK_INT >= 26) {
            try {
                Context.class.getMethod("startForegroundService", Intent.class).invoke(ctx, i);
                return;
            } catch (Exception ignored) {
            }
        }
        ctx.startService(i);
    }

    /** Keep the page's renderer at foreground priority while the radio shows another app (API 26+). */
    static void keepRendererImportant(WebView v) {
        if (Build.VERSION.SDK_INT < 26) return;
        try {
            WebView.class.getMethod("setRendererPriorityPolicy", int.class, boolean.class)
                    .invoke(v, 2 /* RENDERER_PRIORITY_IMPORTANT */, false);
        } catch (Exception ignored) {
        }
    }

    /** A PendingIntent the system fills in (USB permission results need a mutable one on 31+). */
    static int mutableFlags() {
        return PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? FLAG_MUTABLE : 0);
    }

    static int immutableFlags() {
        return PendingIntent.FLAG_UPDATE_CURRENT | FLAG_IMMUTABLE;
    }
}
