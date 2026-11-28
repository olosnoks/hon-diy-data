package com.olosnoks.hondalog;

import android.content.Context;
import android.view.View;
import android.webkit.WebView;

/**
 * A WebView that tells the page it is still on screen when the radio shows
 * another app (maps, music). Otherwise Chromium marks the page hidden and
 * throttles its timers, and the ECU polling, the recorder and the stall
 * catcher would stall with it. The foreground service keeps the process alive.
 */
final class AppWebView extends WebView {
    AppWebView(Context c) { super(c); }

    @Override protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(View.VISIBLE);
    }
}
