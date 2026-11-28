package com.olosnoks.hondalog;

import android.webkit.JavascriptInterface;

/**
 * window.HondalogAndroid. File calls are synchronous and return a string;
 * a failure comes back as "\u0001" + message (never a Java exception, which
 * older WebViews turn into a crash). USB calls are async (Js.done).
 */
final class Bridge {
    static final String ERR = "\u0001";

    interface Host {
        void toast(String text);
        String versionName();
    }

    private final FileBridge files;
    private final UsbBridge usb;
    private final Host host;

    Bridge(FileBridge files, UsbBridge usb, Host host) {
        this.files = files;
        this.usb = usb;
        this.host = host;
    }

    private static String err(Exception e) {
        String m = e.getMessage();
        return ERR + (m == null ? e.toString() : m);
    }

    @JavascriptInterface public String appVersion() { return host.versionName(); }

    // ---- file store ----

    @JavascriptInterface public String fileRead(String name) {
        try { return files.read(name); } catch (Exception e) { return err(e); }
    }

    @JavascriptInterface public String fileList() { return files.list(); }

    @JavascriptInterface public String fileRemove(String name) {
        try { files.remove(name); return ""; } catch (Exception e) { return err(e); }
    }

    @JavascriptInterface public String fileWriteBegin(String name) {
        try { return files.writeBegin(name); } catch (Exception e) { return err(e); }
    }

    @JavascriptInterface public String fileWriteChunk(String token, String b64) {
        try { files.writeChunk(token, b64); return ""; } catch (Exception e) { return err(e); }
    }

    @JavascriptInterface public String fileWriteCommit(String token) {
        try { files.writeCommit(token); return ""; } catch (Exception e) { return err(e); }
    }

    @JavascriptInterface public String fileWriteAbort(String token) {
        files.writeAbort(token);
        return "";
    }

    // ---- exports (downloads) ----

    @JavascriptInterface public String exportBegin(String name, String mime) {
        try { return files.exportBegin(name, mime); } catch (Exception e) { return err(e); }
    }

    @JavascriptInterface public String exportChunk(String token, String b64) {
        try { files.writeChunk(token, b64); return ""; } catch (Exception e) { return err(e); }
    }

    @JavascriptInterface public String exportCommit(String token) {
        try {
            String where = files.exportCommit(token);
            host.toast("Saved " + where);
            return where;
        } catch (Exception e) {
            host.toast("Not saved: " + e.getMessage());
            return err(e);
        }
    }

    // ---- navigator.usb ----

    @JavascriptInterface public void usbGetDevices(int cb) { usb.getDevices(cb); }

    @JavascriptInterface public void usbRequestDevice(int cb, String filters) { usb.requestDevice(cb, filters); }

    @JavascriptInterface public void usbOpen(int cb, String id) { usb.open(cb, id); }

    @JavascriptInterface public void usbClose(int cb, String id) { usb.close(cb, id); }

    @JavascriptInterface public void usbSelectConfiguration(int cb, String id, int value) { usb.selectConfiguration(cb, id, value); }

    @JavascriptInterface public void usbClaimInterface(int cb, String id, int n) { usb.claimInterface(cb, id, n); }

    @JavascriptInterface public void usbReleaseInterface(int cb, String id, int n) { usb.releaseInterface(cb, id, n); }

    @JavascriptInterface public void usbControl(int cb, String id, int requestType, int request, int value, int index, String dataB64, int length) {
        usb.control(cb, id, requestType, request, value, index, dataB64, length);
    }

    @JavascriptInterface public void usbTransferIn(int cb, String id, int endpoint, int length) { usb.transferIn(cb, id, endpoint, length); }

    @JavascriptInterface public void usbTransferOut(int cb, String id, int endpoint, String dataB64) { usb.transferOut(cb, id, endpoint, dataB64); }

    @JavascriptInterface public void usbClearHalt(int cb, String id, boolean in, int endpoint) { usb.clearHalt(cb, id, in, endpoint); }
}
