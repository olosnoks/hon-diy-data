package com.olosnoks.hondalog;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** The one screen: the hondalog web app in a WebView, kept running behind other apps. */
public final class MainActivity extends Activity implements Bridge.Host {
    private static final String TAG = "hondalog";
    static final String PREFS = "hondalog";
    static final String PREF_AUTOSTART = "autostart";
    /** Oldest Chrome/WebView the web build is known to run on (ES2022, OPFS-free paths). */
    static final int MIN_WEBVIEW = 100;
    private static final int REQ_FILE = 7;

    private AppWebView web;
    private final Js js = new Js();
    private UsbBridge usb;
    private FileBridge files;
    private ValueCallback<Uri[]> fileCallback;
    private final Handler ui = new Handler(Looper.getMainLooper());

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        if (handleQuit(getIntent())) return;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        immersive();
        DashService.start(this);

        files = new FileBridge(this);
        usb = new UsbBridge(this, js);
        usb.setDialogContext(this);

        WebView.setWebContentsDebuggingEnabled(true);   // chrome://inspect over adb, for the owner
        web = new AppWebView(this);
        web.setBackgroundColor(Color.parseColor("#282A36"));
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setHapticFeedbackEnabled(true);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setTextZoom(100);   // the radio's font-size setting would break the fixed layout
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        Compat.keepRendererImportant(web);
        web.addJavascriptInterface(new Bridge(files, usb, this), "HondalogAndroid");

        final AssetServer server = new AssetServer(getAssets());
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
                return AssetServer.isApp(r.getUrl()) ? server.serve(r.getUrl()) : null;
            }

            @SuppressWarnings("deprecation")
            @Override public boolean shouldOverrideUrlLoading(WebView v, String url) {
                Uri u = Uri.parse(url);
                if (AssetServer.isApp(u)) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }

            @Override public void onPageFinished(WebView v, String url) {
                ui.postDelayed(new Runnable() {
                    @Override public void run() { checkStarted(); }
                }, 12000);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(ConsoleMessage m) {
                Log.println(m.messageLevel() == ConsoleMessage.MessageLevel.ERROR ? Log.ERROR : Log.INFO, TAG,
                        m.message() + " (" + m.sourceId() + ":" + m.lineNumber() + ")");
                return true;
            }

            @Override public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                Intent i = new Intent(Intent.ACTION_GET_CONTENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                if (p.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    startActivityForResult(Intent.createChooser(i, "Open file"), REQ_FILE);
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    toast("No file manager on this device");
                    return false;
                }
                return true;
            }
        });
        js.attach(web);
        setContentView(web);
        web.loadUrl(AssetServer.ORIGIN);

        warnOldWebView();
        handleIntent(getIntent());
    }

    private void immersive() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    @Override public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        if (focus) immersive();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (handleQuit(intent)) return;
        handleIntent(intent);
    }

    private boolean handleQuit(Intent i) {
        if (i == null || !DashService.ACTION_QUIT.equals(i.getAction())) return false;
        flush();
        DashService.stop(this);
        finishAndRemoveTask();
        return true;
    }

    private void handleIntent(Intent i) {
        if (i == null) return;
        if (UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(i.getAction())) {
            UsbDevice d = i.getParcelableExtra(UsbManager.EXTRA_DEVICE);
            if (usb != null) usb.attached(d);
        } else if (DashService.ACTION_SETTINGS.equals(i.getAction())) {
            showSettings();
        }
    }

    /** Write hondalog.db now (the page's own pagehide/visibility flush never fires here). */
    private void flush() {
        js.eval("try{var h=window.hondalog;h&&h.store&&h.store.persist().catch(function(){})}catch(e){}");
    }

    @Override protected void onStop() {
        flush();
        super.onStop();
    }

    @SuppressWarnings("deprecation")
    @Override public void onBackPressed() {
        // Back leaves the app running (like Home); Quit is in the notification.
        moveTaskToBack(true);
    }

    @Override protected void onDestroy() {
        if (web != null) {
            js.detach();
            web.destroy();
        }
        if (usb != null) usb.shutdown();
        if (isFinishing()) DashService.stop(this);
        super.onDestroy();
    }

    @Override protected void onActivityResult(int req, int result, Intent data) {
        if (req == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(req, result, data);
    }

    // ---- Bridge.Host ----

    @Override public void toast(final String text) {
        ui.post(new Runnable() {
            @Override public void run() { Toast.makeText(MainActivity.this, text, Toast.LENGTH_LONG).show(); }
        });
    }

    @Override public String versionName() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    // ---- WebView version ----

    static int webViewMajor(Context c) {
        try {
            Matcher m = Pattern.compile("Chrome/(\\d+)").matcher(WebSettings.getDefaultUserAgent(c));
            return m.find() ? Integer.parseInt(m.group(1)) : -1;
        } catch (Exception e) {
            return -1;
        }
    }

    private void warnOldWebView() {
        int v = webViewMajor(this);
        if (v < 0 || v >= MIN_WEBVIEW) return;
        new AlertDialog.Builder(this)
                .setTitle("Android System WebView is too old")
                .setMessage("This radio's WebView is version " + v + "; hondalog needs " + MIN_WEBVIEW
                        + " or newer. Update \"Android System WebView\" (Play Store, or install its APK), then reopen hondalog.")
                .setPositiveButton("OK", null)
                .show();
    }

    /** Nothing drawn 12 s after load: the page failed to start. Say why instead of a blank screen. */
    private void checkStarted() {
        if (web == null) return;
        web.evaluateJavascript("(function(){var r=document.getElementById('root');return r?r.childElementCount:0})()",
                new ValueCallback<String>() {
                    @Override public void onReceiveValue(String n) {
                        if (!"0".equals(n)) return;
                        new AlertDialog.Builder(MainActivity.this)
                                .setTitle("hondalog did not start")
                                .setMessage("The app screen stayed empty. WebView version " + webViewMajor(MainActivity.this)
                                        + " (needs " + MIN_WEBVIEW + "+). Connect the radio to a PC with adb and open chrome://inspect to see the error.")
                                .setPositiveButton("Reload", new android.content.DialogInterface.OnClickListener() {
                                    @Override public void onClick(android.content.DialogInterface d, int w) { web.reload(); }
                                })
                                .setNegativeButton("Close", null)
                                .show();
                    }
                });
    }

    // ---- App settings (from the notification) ----

    private void showSettings() {
        final SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad, pad, 0);

        final CheckBox auto = new CheckBox(this);
        auto.setText("Start hondalog with the radio");
        auto.setChecked(p.getBoolean(PREF_AUTOSTART, false));
        box.addView(auto);

        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        boolean batteryOk = pm.isIgnoringBatteryOptimizations(getPackageName());
        boolean overlayOk = Settings.canDrawOverlays(this);
        String info = "Version " + versionName() + ", WebView " + webViewMajor(this) + ", Android " + Build.VERSION.RELEASE
                + "\nBattery optimisation: " + (batteryOk ? "off (good)" : "ON - logging can be stopped in the background")
                + "\nDisplay over other apps: " + (overlayOk ? "allowed" : "not allowed - needed to open at boot on Android 10+")
                + "\nDrives: " + new java.io.File(files.root(), "hondalog.db").length() / 1024 + " kB in app storage."
                + " Export with Setup > Export hondalog.db (goes to Download/hondalog).";
        TextView t = new TextView(this);
        t.setText(info);
        t.setPadding(0, pad / 2, 0, 0);
        box.addView(t);

        AlertDialog.Builder b = new AlertDialog.Builder(this)
                .setTitle("hondalog app settings")
                .setView(box)
                .setPositiveButton("Done", new android.content.DialogInterface.OnClickListener() {
                    @Override public void onClick(android.content.DialogInterface d, int w) {
                        p.edit().putBoolean(PREF_AUTOSTART, auto.isChecked()).apply();
                        if (auto.isChecked() && !Settings.canDrawOverlays(MainActivity.this)) openOverlaySettings();
                    }
                });
        if (!batteryOk) {
            b.setNeutralButton("Battery: allow background", new android.content.DialogInterface.OnClickListener() {
                @SuppressLint("BatteryLife")
                @Override public void onClick(android.content.DialogInterface d, int w) {
                    p.edit().putBoolean(PREF_AUTOSTART, auto.isChecked()).apply();
                    try {
                        startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
                    } catch (ActivityNotFoundException e) {
                        toast("This radio has no battery optimisation setting");
                    }
                }
            });
        } else if (!overlayOk) {
            b.setNeutralButton("Allow open at boot", new android.content.DialogInterface.OnClickListener() {
                @Override public void onClick(android.content.DialogInterface d, int w) {
                    p.edit().putBoolean(PREF_AUTOSTART, auto.isChecked()).apply();
                    openOverlaySettings();
                }
            });
        }
        b.show();
    }

    private void openOverlaySettings() {
        try {
            startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName())));
        } catch (ActivityNotFoundException e) {
            toast("This radio has no \"Display over other apps\" setting");
        }
    }

}
