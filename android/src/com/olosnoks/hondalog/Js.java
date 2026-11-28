package com.olosnoks.hondalog;

import android.os.Handler;
import android.os.Looper;
import android.webkit.WebView;

import org.json.JSONObject;

/** Calls into the page from any thread (evaluateJavascript must run on the UI thread). */
final class Js {
    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile WebView view;

    void attach(WebView v) { view = v; }

    void detach() { view = null; }

    void eval(final String script) {
        ui.post(new Runnable() {
            @Override public void run() {
                WebView v = view;
                if (v != null) v.evaluateJavascript(script, null);
            }
        });
    }

    /** Settle the shim's promise number id: ok with a JSON value (or null), or a DOMException. */
    void done(int id, boolean ok, String json) {
        eval("window.__hondalogUsbDone&&window.__hondalogUsbDone(" + id + "," + ok + ","
                + (json == null ? "null" : JSONObject.quote(json)) + ")");
    }

    void ok(int id, String json) { done(id, true, json); }

    void fail(int id, String domName, String message) {
        try {
            JSONObject e = new JSONObject();
            e.put("name", domName);
            e.put("message", message == null ? domName : message);
            done(id, false, e.toString());
        } catch (Exception ignored) {
            done(id, false, "{\"name\":\"NetworkError\",\"message\":\"error\"}");
        }
    }

    /** A navigator.usb "connect" / "disconnect" event with the device descriptor. */
    void usbEvent(String type, String deviceJson) {
        eval("window.__hondalogUsbEvent&&window.__hondalogUsbEvent(" + JSONObject.quote(type) + ","
                + JSONObject.quote(deviceJson) + ")");
    }
}
