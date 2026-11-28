package com.olosnoks.hondalog;

import android.content.res.AssetManager;
import android.net.Uri;
import android.webkit.WebResourceResponse;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.util.HashMap;
import java.util.Map;

/**
 * Serves the bundled web build (assets/www) at https://appassets.androidplatform.net/,
 * a real https origin, so the page is a secure context and ES modules and
 * WebAssembly load as they do from GitHub Pages. index.html gets the USB/file
 * shim as its first script.
 */
final class AssetServer {
    static final String HOST = "appassets.androidplatform.net";
    static final String ORIGIN = "https://" + HOST + "/";
    private static final String SHIM_PATH = "/__hondalog/android.js";
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private static final Map<String, String> MIME = new HashMap<String, String>();
    static {
        MIME.put("html", "text/html");
        MIME.put("js", "text/javascript");
        MIME.put("mjs", "text/javascript");
        MIME.put("css", "text/css");
        MIME.put("json", "application/json");
        MIME.put("webmanifest", "application/manifest+json");
        MIME.put("wasm", "application/wasm");
        MIME.put("png", "image/png");
        MIME.put("jpg", "image/jpeg");
        MIME.put("svg", "image/svg+xml");
        MIME.put("ico", "image/x-icon");
        MIME.put("woff2", "font/woff2");
        MIME.put("woff", "font/woff");
        MIME.put("ttf", "font/ttf");
        MIME.put("otf", "font/otf");
        MIME.put("toml", "text/plain");
        MIME.put("txt", "text/plain");
        MIME.put("md", "text/plain");
    }

    private final AssetManager assets;

    AssetServer(AssetManager assets) { this.assets = assets; }

    static boolean isApp(Uri u) {
        return u != null && "https".equals(u.getScheme()) && HOST.equals(u.getHost());
    }

    WebResourceResponse serve(Uri u) {
        String path = u.getPath();
        if (path == null || path.isEmpty() || path.equals("/")) path = "/index.html";
        try {
            if (path.equals(SHIM_PATH)) return response("text/javascript", open("hondalog-android.js"));
            if (path.equals("/index.html")) return index();
            if (path.contains("..")) return notFound();
            InputStream in = tryOpen("www" + path);
            if (in == null) {
                // A route of the single-page app (no file extension): the app shell.
                String last = path.substring(path.lastIndexOf('/') + 1);
                return last.contains(".") ? notFound() : index();
            }
            String ext = last(path);
            String mime = MIME.get(ext);
            return response(mime == null ? "application/octet-stream" : mime, in);
        } catch (IOException e) {
            return notFound();
        }
    }

    private WebResourceResponse index() throws IOException {
        String html = new String(readAll(open("www/index.html")), UTF8);
        String tag = "<script src=\"" + SHIM_PATH + "\"></script>";
        int head = html.indexOf("<head>");
        html = head >= 0 ? html.substring(0, head + 6) + "\n" + tag + html.substring(head + 6) : tag + html;
        return response("text/html", new ByteArrayInputStream(html.getBytes(UTF8)));
    }

    private static String last(String path) {
        int dot = path.lastIndexOf('.');
        return dot < 0 ? "" : path.substring(dot + 1).toLowerCase();
    }

    private InputStream open(String name) throws IOException { return assets.open(name, AssetManager.ACCESS_STREAMING); }

    private InputStream tryOpen(String name) {
        try { return open(name); } catch (IOException e) { return null; }
    }

    private static byte[] readAll(InputStream in) throws IOException {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] b = new byte[16384];
            int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
            return out.toByteArray();
        } finally {
            in.close();
        }
    }

    private static WebResourceResponse response(String mime, InputStream in) {
        Map<String, String> h = new HashMap<String, String>();
        h.put("Cache-Control", "no-cache");
        h.put("Access-Control-Allow-Origin", "*");
        boolean text = mime.startsWith("text/") || mime.contains("json") || mime.contains("xml");
        return new WebResourceResponse(mime, text ? "utf-8" : null, 200, "OK", h, in);
    }

    private static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found", new HashMap<String, String>(),
                new ByteArrayInputStream(new byte[0]));
    }
}
