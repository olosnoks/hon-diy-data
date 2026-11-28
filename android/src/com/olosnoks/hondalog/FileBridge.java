package com.olosnoks.hondalog;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.util.Base64;

import org.json.JSONArray;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * The page's file store (src/platform/native.ts) in the app's private storage,
 * plus exports into the public Download/hondalog folder. Writes go to a temp
 * file that replaces the old one only once it is complete and synced, so a
 * power cut mid-write (the radio losing ignition) leaves the last good copy.
 */
final class FileBridge {
    private final Context ctx;
    private final File root;
    private final Map<String, Pending> pending = new ConcurrentHashMap<String, Pending>();

    private static final class Pending {
        final File temp, target;
        final FileOutputStream out;
        final String mime;     // exports only

        Pending(File temp, File target, String mime) throws IOException {
            this.temp = temp;
            this.target = target;
            this.mime = mime;
            this.out = new FileOutputStream(temp);
        }
    }

    FileBridge(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        this.root = new File(this.ctx.getFilesDir(), "hondalog");
        //noinspection ResultOfMethodCallIgnored
        root.mkdirs();
        // Leftovers of writes cut short.
        cleanTemps(root);
    }

    File root() { return root; }

    private static void cleanTemps(File dir) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            if (f.isDirectory()) cleanTemps(f);
            else if (f.getName().contains(".tmp-")) //noinspection ResultOfMethodCallIgnored
                f.delete();
        }
    }

    private File file(String name) throws IOException {
        if (name == null || name.isEmpty()) throw new IOException("bad file name");
        for (String part : name.split("/")) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) throw new IOException("bad file name: " + name);
        }
        File f = new File(root, name);
        if (!f.getCanonicalPath().startsWith(root.getCanonicalPath() + File.separator)) throw new IOException("bad file name: " + name);
        return f;
    }

    static byte[] readAll(File f) throws IOException {
        byte[] b = new byte[(int) f.length()];
        InputStream in = new FileInputStream(f);
        try {
            int o = 0;
            while (o < b.length) {
                int n = in.read(b, o, b.length - o);
                if (n < 0) break;
                o += n;
            }
        } finally {
            in.close();
        }
        return b;
    }

    String read(String name) throws IOException {
        File f = file(name);
        if (!f.isFile()) return null;
        return Base64.encodeToString(readAll(f), Base64.NO_WRAP);
    }

    String list() {
        JSONArray a = new JSONArray();
        walk(root, "", a);
        return a.toString();
    }

    private static void walk(File dir, String prefix, JSONArray out) {
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            if (f.isDirectory()) walk(f, prefix + f.getName() + "/", out);
            else if (!f.getName().contains(".tmp-")) out.put(prefix + f.getName());
        }
    }

    void remove(String name) throws IOException {
        //noinspection ResultOfMethodCallIgnored
        file(name).delete();
    }

    String writeBegin(String name) throws IOException {
        File target = file(name);
        //noinspection ResultOfMethodCallIgnored
        target.getParentFile().mkdirs();
        String token = UUID.randomUUID().toString();
        pending.put(token, new Pending(new File(target.getParentFile(), "." + target.getName() + ".tmp-" + token), target, null));
        return token;
    }

    void writeChunk(String token, String b64) throws IOException {
        Pending p = pending.get(token);
        if (p == null) throw new IOException("no such write");
        p.out.write(Base64.decode(b64, Base64.NO_WRAP));
    }

    void writeCommit(String token) throws IOException {
        Pending p = pending.remove(token);
        if (p == null) throw new IOException("no such write");
        try {
            p.out.flush();
            p.out.getFD().sync();
        } finally {
            p.out.close();
        }
        if (!p.temp.renameTo(p.target)) {
            //noinspection ResultOfMethodCallIgnored
            p.temp.delete();
            throw new IOException("could not replace " + p.target.getName());
        }
    }

    void writeAbort(String token) {
        Pending p = pending.remove(token);
        if (p == null) return;
        try { p.out.close(); } catch (IOException ignored) {}
        //noinspection ResultOfMethodCallIgnored
        p.temp.delete();
    }

    // ---- exports (the page's downloads) ----

    String exportBegin(String name, String mime) throws IOException {
        String safe = name == null ? "download" : name.replaceAll("[\\\\/:*?\"<>|]+", "_");
        if (safe.isEmpty()) safe = "download";
        File dir = new File(ctx.getCacheDir(), "exports");
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        String token = UUID.randomUUID().toString();
        pending.put(token, new Pending(new File(dir, token), new File(dir, safe), mime == null || mime.isEmpty() ? guessMime(safe) : mime));
        return token;
    }

    /** Finish an export: copy it into Download/hondalog. Returns where it went, for the toast. */
    String exportCommit(String token) throws IOException {
        Pending p = pending.remove(token);
        if (p == null) throw new IOException("no such export");
        p.out.close();
        try {
            String name = p.target.getName();
            if (Build.VERSION.SDK_INT >= 29) {
                ContentResolver cr = ctx.getContentResolver();
                ContentValues v = new ContentValues();
                v.put("_display_name", name);
                v.put("mime_type", p.mime);
                v.put("relative_path", Environment.DIRECTORY_DOWNLOADS + "/hondalog");
                Uri uri = cr.insert(Uri.parse("content://media/external/downloads"), v);
                if (uri == null) throw new IOException("Download folder is not available");
                OutputStream out = cr.openOutputStream(uri);
                if (out == null) throw new IOException("Download folder is not writable");
                try {
                    copy(p.temp, out);
                } finally {
                    out.close();
                }
                return Environment.DIRECTORY_DOWNLOADS + "/hondalog/" + name;
            }
            File dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "hondalog");
            if (!dir.isDirectory() && !dir.mkdirs()) {
                dir = new File(ctx.getExternalFilesDir(null), "exports");   // no storage permission: app folder
                //noinspection ResultOfMethodCallIgnored
                dir.mkdirs();
            }
            File dest = new File(dir, name);
            for (int i = 1; dest.exists(); i++) dest = new File(dir, stem(name) + " (" + i + ")" + ext(name));
            OutputStream out = new FileOutputStream(dest);
            try {
                copy(p.temp, out);
            } finally {
                out.close();
            }
            return dest.getAbsolutePath();
        } finally {
            //noinspection ResultOfMethodCallIgnored
            p.temp.delete();
        }
    }

    private static void copy(File from, OutputStream out) throws IOException {
        InputStream in = new FileInputStream(from);
        try {
            byte[] b = new byte[65536];
            int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
        } finally {
            in.close();
        }
    }

    private static String stem(String n) { int i = n.lastIndexOf('.'); return i > 0 ? n.substring(0, i) : n; }

    private static String ext(String n) { int i = n.lastIndexOf('.'); return i > 0 ? n.substring(i) : ""; }

    static String guessMime(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".db") || n.endsWith(".sqlite")) return "application/x-sqlite3";
        if (n.endsWith(".csv")) return "text/csv";
        if (n.endsWith(".gpx")) return "application/gpx+xml";
        if (n.endsWith(".kml")) return "application/vnd.google-earth.kml+xml";
        if (n.endsWith(".json") || n.endsWith(".geojson")) return "application/json";
        if (n.endsWith(".txt") || n.endsWith(".toml") || n.endsWith(".msl")) return "text/plain";
        return "application/octet-stream";
    }
}
