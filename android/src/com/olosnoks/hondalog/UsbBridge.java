package com.olosnoks.hondalog;

import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The Java half of the navigator.usb shim (assets/hondalog-android.js): the
 * WebUSB calls the page's FTDI/CH340 drivers make, done with Android's USB
 * host API. Every call is async: the page passes a callback id and the result
 * comes back through Js.done. Devices are named by UsbDevice.getDeviceName().
 */
final class UsbBridge {
    private static final String TAG = "hondalog-usb";
    static final String ACTION_PERMISSION = "com.olosnoks.hondalog.USB_PERMISSION";

    /** Chips the page has drivers for: auto-ask permission when one is plugged in. */
    static boolean isKnownChip(UsbDevice d) {
        int v = d.getVendorId(), p = d.getProductId();
        return (v == 0x0403 && p == 0x6001) || (v == 0x1a86 && (p == 0x7523 || p == 0x5523));
    }

    private final Context ctx;
    private final UsbManager um;
    private final Js js;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Map<String, Conn> conns = new ConcurrentHashMap<String, Conn>();
    /** Pending requestDevice() / auto-connect callbacks by device name (-1 = auto-connect). */
    private final Map<String, List<Integer>> waitingPermission = new ConcurrentHashMap<String, List<Integer>>();
    private Context dialogContext;

    private static final class Conn {
        final UsbDevice dev;
        final UsbDeviceConnection c;
        final Map<Integer, UsbInterface> claimed = new ConcurrentHashMap<Integer, UsbInterface>();
        final ExecutorService ctl = Executors.newSingleThreadExecutor();
        final ExecutorService in = Executors.newSingleThreadExecutor();
        volatile boolean closed;
        /** Bumped by releaseInterface/close: a pending bulk read with an older number stops. */
        volatile int generation;

        Conn(UsbDevice d, UsbDeviceConnection c) { dev = d; this.c = c; }
    }

    UsbBridge(Context ctx, Js js) {
        this.ctx = ctx.getApplicationContext();
        this.um = (UsbManager) ctx.getSystemService(Context.USB_SERVICE);
        this.js = js;
        IntentFilter f = new IntentFilter();
        f.addAction(ACTION_PERMISSION);
        f.addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED);
        f.addAction(UsbManager.ACTION_USB_DEVICE_DETACHED);
        this.ctx.registerReceiver(receiver, f);
    }

    /** Where the device chooser dialog is shown (the activity). */
    void setDialogContext(Context c) { dialogContext = c; }

    void shutdown() {
        try { ctx.unregisterReceiver(receiver); } catch (Exception ignored) {}
        for (Conn c : conns.values()) closeConn(c);
        conns.clear();
    }

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            UsbDevice d = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
            String a = intent.getAction();
            if (d == null || a == null) return;
            if (ACTION_PERMISSION.equals(a)) {
                permissionResult(d, intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false) || um.hasPermission(d));
            } else if (UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(a)) {
                attached(d);
            } else if (UsbManager.ACTION_USB_DEVICE_DETACHED.equals(a)) {
                Conn c = conns.remove(d.getDeviceName());
                if (c != null) closeConn(c);
                js.usbEvent("disconnect", describe(d).toString());
            }
        }
    };

    /** A device was plugged in (broadcast, or the activity was opened by it). */
    void attached(UsbDevice d) {
        if (d == null) return;
        if (um.hasPermission(d)) {
            js.usbEvent("connect", describe(d).toString());
        } else if (isKnownChip(d)) {
            askPermission(d, -1);
        }
    }

    private void askPermission(UsbDevice d, int cb) {
        List<Integer> l = waitingPermission.get(d.getDeviceName());
        boolean first = l == null;
        if (first) waitingPermission.put(d.getDeviceName(), l = new ArrayList<Integer>());
        synchronized (l) { l.add(cb); }
        if (!first) return;
        Intent i = new Intent(ACTION_PERMISSION).setPackage(ctx.getPackageName());
        PendingIntent pi = PendingIntent.getBroadcast(ctx, 1, i, Compat.mutableFlags());
        um.requestPermission(d, pi);
    }

    private void permissionResult(UsbDevice d, boolean granted) {
        List<Integer> l = waitingPermission.remove(d.getDeviceName());
        if (granted) js.usbEvent("connect", describe(d).toString());
        if (l == null) return;
        synchronized (l) {
            for (int cb : l) {
                if (cb < 0) continue;
                if (granted) js.ok(cb, describe(d).toString());
                else js.fail(cb, "NotFoundError", "Access to the USB device was not allowed.");
            }
        }
    }

    // ---- navigator.usb ----

    void getDevices(int cb) {
        JSONArray a = new JSONArray();
        for (UsbDevice d : um.getDeviceList().values()) if (um.hasPermission(d)) a.put(describe(d));
        js.ok(cb, a.toString());
    }

    void requestDevice(final int cb, String filtersJson) {
        final List<UsbDevice> match = new ArrayList<UsbDevice>();
        try {
            JSONArray filters = new JSONArray(filtersJson == null ? "[]" : filtersJson);
            for (UsbDevice d : um.getDeviceList().values()) if (matches(d, filters)) match.add(d);
        } catch (Exception e) {
            js.fail(cb, "TypeError", "bad filters: " + e.getMessage());
            return;
        }
        if (match.isEmpty()) {
            js.fail(cb, "NotFoundError", "No matching USB device is plugged in.");
            return;
        }
        if (match.size() == 1) {
            grantThen(cb, match.get(0));
            return;
        }
        // Several: let the driver pick, like Chrome's chooser.
        ui.post(new Runnable() {
            @Override public void run() {
                Context c = dialogContext;
                if (c == null) { grantThen(cb, match.get(0)); return; }
                String[] names = new String[match.size()];
                for (int i = 0; i < names.length; i++) names[i] = label(match.get(i));
                final boolean[] picked = {false};
                new AlertDialog.Builder(c)
                        .setTitle("Connect to")
                        .setItems(names, new DialogInterface.OnClickListener() {
                            @Override public void onClick(DialogInterface dlg, int which) {
                                picked[0] = true;
                                grantThen(cb, match.get(which));
                            }
                        })
                        .setOnDismissListener(new DialogInterface.OnDismissListener() {
                            @Override public void onDismiss(DialogInterface dlg) {
                                if (!picked[0]) js.fail(cb, "NotFoundError", "No device selected.");
                            }
                        })
                        .show();
            }
        });
    }

    private void grantThen(int cb, UsbDevice d) {
        if (um.hasPermission(d)) js.ok(cb, describe(d).toString());
        else askPermission(d, cb);
    }

    private static boolean matches(UsbDevice d, JSONArray filters) throws Exception {
        if (filters.length() == 0) return true;
        for (int i = 0; i < filters.length(); i++) {
            JSONObject f = filters.getJSONObject(i);
            if (f.has("vendorId") && f.getInt("vendorId") != d.getVendorId()) continue;
            if (f.has("productId") && f.getInt("productId") != d.getProductId()) continue;
            if (f.has("classCode") && f.getInt("classCode") != d.getDeviceClass()) continue;
            return true;
        }
        return false;
    }

    private static String label(UsbDevice d) {
        String name = Build.VERSION.SDK_INT >= 21 ? d.getProductName() : null;
        return String.format("%s (%04x:%04x)", name == null ? "USB device" : name, d.getVendorId(), d.getProductId());
    }

    JSONObject describe(UsbDevice d) {
        JSONObject o = new JSONObject();
        try {
            o.put("id", d.getDeviceName());
            o.put("vendorId", d.getVendorId());
            o.put("productId", d.getProductId());
            o.put("deviceClass", d.getDeviceClass());
            o.put("deviceSubclass", d.getDeviceSubclass());
            o.put("deviceProtocol", d.getDeviceProtocol());
            o.put("productName", d.getProductName() == null ? JSONObject.NULL : d.getProductName());
            o.put("manufacturerName", d.getManufacturerName() == null ? JSONObject.NULL : d.getManufacturerName());
            String serial = null;
            try {
                if (um.hasPermission(d)) serial = d.getSerialNumber();   // throws without permission on 29+
            } catch (SecurityException ignored) {
            }
            o.put("serialNumber", serial == null ? JSONObject.NULL : serial);
            JSONArray cfgs = new JSONArray();
            for (int ci = 0; ci < d.getConfigurationCount(); ci++) {
                android.hardware.usb.UsbConfiguration cfg = d.getConfiguration(ci);
                JSONObject jc = new JSONObject();
                jc.put("configurationValue", cfg.getId());
                JSONArray ifs = new JSONArray();
                for (int ii = 0; ii < cfg.getInterfaceCount(); ii++) {
                    UsbInterface itf = cfg.getInterface(ii);
                    JSONObject alt = new JSONObject();
                    alt.put("interfaceNumber", itf.getId());
                    alt.put("alternateSetting", itf.getAlternateSetting());
                    alt.put("interfaceClass", itf.getInterfaceClass());
                    alt.put("interfaceSubclass", itf.getInterfaceSubclass());
                    alt.put("interfaceProtocol", itf.getInterfaceProtocol());
                    JSONArray eps = new JSONArray();
                    for (int ei = 0; ei < itf.getEndpointCount(); ei++) {
                        UsbEndpoint ep = itf.getEndpoint(ei);
                        JSONObject je = new JSONObject();
                        je.put("endpointNumber", ep.getEndpointNumber());
                        je.put("direction", ep.getDirection() == UsbConstants.USB_DIR_IN ? "in" : "out");
                        int t = ep.getType();
                        je.put("type", t == UsbConstants.USB_ENDPOINT_XFER_BULK ? "bulk"
                                : t == UsbConstants.USB_ENDPOINT_XFER_INT ? "interrupt"
                                : t == UsbConstants.USB_ENDPOINT_XFER_ISOC ? "isochronous" : "control");
                        je.put("packetSize", ep.getMaxPacketSize());
                        eps.put(je);
                    }
                    alt.put("endpoints", eps);
                    ifs.put(alt);
                }
                jc.put("interfaces", ifs);   // flat list of (interface, alternate) pairs; the shim groups them
                cfgs.put(jc);
            }
            o.put("configurations", cfgs);
        } catch (Exception e) {
            Log.w(TAG, "describe", e);
        }
        return o;
    }

    // ---- USBDevice ----

    private UsbDevice find(String id) {
        return um.getDeviceList().get(id);
    }

    private Conn conn(int cb, String id) {
        Conn c = conns.get(id);
        if (c == null || c.closed) {
            js.fail(cb, "InvalidStateError", "The device must be opened first.");
            return null;
        }
        return c;
    }

    void open(int cb, String id) {
        if (conns.containsKey(id)) { js.ok(cb, null); return; }
        UsbDevice d = find(id);
        if (d == null) { js.fail(cb, "NotFoundError", "The device was disconnected."); return; }
        if (!um.hasPermission(d)) { js.fail(cb, "SecurityError", "Access denied."); return; }
        UsbDeviceConnection c = um.openDevice(d);
        if (c == null) { js.fail(cb, "NetworkError", "Unable to open the device."); return; }
        conns.put(id, new Conn(d, c));
        js.ok(cb, null);
    }

    void close(int cb, String id) {
        Conn c = conns.remove(id);
        if (c != null) closeConn(c);
        js.ok(cb, null);
    }

    private void closeConn(Conn c) {
        c.closed = true;
        c.generation++;
        for (UsbInterface i : c.claimed.values()) {
            try { c.c.releaseInterface(i); } catch (Exception ignored) {}
        }
        c.claimed.clear();
        try { c.c.close(); } catch (Exception ignored) {}
        c.ctl.shutdown();
        c.in.shutdown();
    }

    void selectConfiguration(int cb, String id, int value) {
        Conn c = conn(cb, id);
        if (c == null) return;
        // Android has already configured the device; set it only if a different one is asked for.
        if (c.dev.getConfigurationCount() > 1) {
            for (int i = 0; i < c.dev.getConfigurationCount(); i++) {
                android.hardware.usb.UsbConfiguration cfg = c.dev.getConfiguration(i);
                if (cfg.getId() == value && i != 0) c.c.setConfiguration(cfg);
            }
        }
        js.ok(cb, null);
    }

    void claimInterface(int cb, String id, int number) {
        Conn c = conn(cb, id);
        if (c == null) return;
        UsbInterface itf = iface(c.dev, number);
        if (itf == null) { js.fail(cb, "NotFoundError", "No interface " + number + "."); return; }
        if (!c.c.claimInterface(itf, true)) { js.fail(cb, "NetworkError", "Unable to claim interface " + number + "."); return; }
        c.claimed.put(number, itf);
        js.ok(cb, null);
    }

    void releaseInterface(int cb, String id, int number) {
        Conn c = conns.get(id);
        if (c == null) { js.ok(cb, null); return; }
        c.generation++;   // ends a pending transferIn
        UsbInterface itf = c.claimed.remove(number);
        if (itf != null) {
            try { c.c.releaseInterface(itf); } catch (Exception ignored) {}
        }
        js.ok(cb, null);
    }

    private static UsbInterface iface(UsbDevice d, int number) {
        for (int i = 0; i < d.getInterfaceCount(); i++) {
            UsbInterface itf = d.getInterface(i);
            if (itf.getId() == number && itf.getAlternateSetting() == 0) return itf;
        }
        return null;
    }

    private static UsbEndpoint endpoint(Conn c, int number, int dir) {
        for (UsbInterface itf : c.claimed.values()) {
            for (int i = 0; i < itf.getEndpointCount(); i++) {
                UsbEndpoint ep = itf.getEndpoint(i);
                if (ep.getEndpointNumber() == number && ep.getDirection() == dir) return ep;
            }
        }
        return null;
    }

    /** controlTransferIn/Out. requestType is the full bmRequestType byte (the shim builds it). */
    void control(final int cb, String id, final int requestType, final int request, final int value,
                 final int index, final String dataB64, final int length) {
        final Conn c = conn(cb, id);
        if (c == null) return;
        c.ctl.execute(new Runnable() {
            @Override public void run() {
                try {
                    boolean in = (requestType & 0x80) != 0;
                    byte[] buf = in ? new byte[Math.max(0, length)]
                            : (dataB64 == null ? new byte[0] : Base64.decode(dataB64, Base64.NO_WRAP));
                    int n = c.c.controlTransfer(requestType, request, value, index, buf, buf.length, 1000);
                    JSONObject r = new JSONObject();
                    if (n < 0) {
                        r.put("status", "stall");
                    } else {
                        r.put("status", "ok");
                        if (in) r.put("data", Base64.encodeToString(buf, 0, n, Base64.NO_WRAP));
                        else r.put("bytesWritten", n);
                    }
                    js.ok(cb, r.toString());
                } catch (Exception e) {
                    js.fail(cb, "NetworkError", e.toString());
                }
            }
        });
    }

    /**
     * transferIn on a bulk/interrupt endpoint. Like WebUSB it settles only when
     * data comes: Android's bulkTransfer has a timeout, so this polls. The FTDI
     * sends a 2-byte modem-status packet every latency period even when idle;
     * those are not handed to the page one by one.
     */
    void transferIn(final int cb, String id, final int number, final int length) {
        final Conn c = conn(cb, id);
        if (c == null) return;
        final UsbEndpoint ep = endpoint(c, number, UsbConstants.USB_DIR_IN);
        if (ep == null) { js.fail(cb, "NotFoundError", "Endpoint " + number + " in is not part of a claimed interface."); return; }
        final int gen = c.generation;
        final boolean ftdi = c.dev.getVendorId() == 0x0403;
        final String name = c.dev.getDeviceName();
        c.in.execute(new Runnable() {
            @Override public void run() {
                byte[] buf = new byte[Math.max(1, length)];
                int misses = 0;
                while (true) {
                    if (c.closed || c.generation != gen) {
                        js.fail(cb, "AbortError", "The transfer was cancelled.");
                        return;
                    }
                    long t0 = SystemClock.uptimeMillis();
                    int n = c.c.bulkTransfer(ep, buf, buf.length, 100);
                    if (n > 0 && !(ftdi && n <= 2)) {
                        try {
                            JSONObject r = new JSONObject();
                            r.put("status", "ok");
                            r.put("data", Base64.encodeToString(buf, 0, n, Base64.NO_WRAP));
                            js.ok(cb, r.toString());
                        } catch (Exception e) {
                            js.fail(cb, "NetworkError", e.toString());
                        }
                        return;
                    }
                    if (n < 0) {
                        // A timeout and an unplugged device both give -1: check the second one now and then.
                        if (++misses % 20 == 0 && !um.getDeviceList().containsKey(name)) {
                            js.fail(cb, "NetworkError", "The device was disconnected.");
                            return;
                        }
                        if (SystemClock.uptimeMillis() - t0 < 5) SystemClock.sleep(5);   // failing fast: don't spin
                    }
                }
            }
        });
    }

    void transferOut(final int cb, String id, final int number, final String dataB64) {
        final Conn c = conn(cb, id);
        if (c == null) return;
        final UsbEndpoint ep = endpoint(c, number, UsbConstants.USB_DIR_OUT);
        if (ep == null) { js.fail(cb, "NotFoundError", "Endpoint " + number + " out is not part of a claimed interface."); return; }
        c.ctl.execute(new Runnable() {
            @Override public void run() {
                try {
                    byte[] data = Base64.decode(dataB64, Base64.NO_WRAP);
                    int done = 0;
                    while (done < data.length) {
                        int chunk = Math.min(data.length - done, 16384);
                        byte[] part = new byte[chunk];
                        System.arraycopy(data, done, part, 0, chunk);
                        int n = c.c.bulkTransfer(ep, part, chunk, 2000);
                        if (n < 0) break;
                        done += n;
                    }
                    JSONObject r = new JSONObject();
                    r.put("status", done == data.length ? "ok" : "stall");
                    r.put("bytesWritten", done);
                    js.ok(cb, r.toString());
                } catch (Exception e) {
                    js.fail(cb, "NetworkError", e.toString());
                }
            }
        });
    }

    /** CLEAR_FEATURE(ENDPOINT_HALT) on an endpoint. */
    void clearHalt(final int cb, String id, final boolean in, final int number) {
        final Conn c = conn(cb, id);
        if (c == null) return;
        c.ctl.execute(new Runnable() {
            @Override public void run() {
                int n = c.c.controlTransfer(0x02, 0x01, 0, number | (in ? 0x80 : 0), null, 0, 1000);
                if (n < 0) js.fail(cb, "NetworkError", "clearHalt failed");
                else js.ok(cb, null);
            }
        });
    }
}
