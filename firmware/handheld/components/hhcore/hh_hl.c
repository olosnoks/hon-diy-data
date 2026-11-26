/* The HL text session: the laptop app's way to manage the handheld over the USB-C cable (SERIAL.md, "The HL
 * session"; the Python side is hondalog/handheld/hl_client.py, the reference device cydsim_hl.py).
 *
 * Lines of text share the wire with the binary stream. A session opens with "HL HELLO" and pauses the binary
 * frames (the engine and the drive file go on); ESP_LOG then goes out as plain lines. Every command re-arms a
 * 30 s idle timer. Writes are limited to MAP/ (8.3 names), HANDHELD.MAP and HANDHELD.CFG; deletes to those and
 * DRIVES/. Nothing here has a K-line transport: the read-only guard is intact by construction.
 *
 * Files are moved with 2048-byte DATA lines (base64), a window of three, one ACK each; the whole file's CRC32 is
 * checked by the card layer (port/hh_serfs.c) before it replaces anything, on the file as it was read back.
 *
 * Portable: no OS, no UART. The card is hh_serial_fs_t, the ring is hh_serial.c's.
 */
#include <stdarg.h>
#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "hh_serial.h"

#if defined(HH_GPS_P1) && HH_GPS_P1 && !defined(HH_SERIAL_TX_ONLY)
#define HH_SERIAL_TX_ONLY 1
#endif

#ifndef HH_SERIAL_TX_ONLY

#define MODEL "cyd-4827s043n"

/* JSON has no NaN or infinity */
static double fin(double v) { return isfinite(v) ? v : 0.0; }

static double now(const hh_serial_t *s) { return s->clk.now(s->clk.ctx); }

/* ---- output ------------------------------------------------------------------------ */

static void say(hh_serial_t *s, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(s->hl_out, sizeof(s->hl_out) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if ((size_t)n > sizeof(s->hl_out) - 2) n = (int)sizeof(s->hl_out) - 2;
    s->hl_out[n++] = '\n';
    hh_serial_push_text(s, s->hl_out, (size_t)n);
}

/* ---- base64 ------------------------------------------------------------------------ */

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static size_t b64_encode(const uint8_t *in, size_t len, char *out)
{
    size_t w = 0;
    for (size_t i = 0; i < len; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16;
        if (i + 1 < len) v |= (uint32_t)in[i + 1] << 8;
        if (i + 2 < len) v |= in[i + 2];
        out[w++] = B64[(v >> 18) & 63];
        out[w++] = B64[(v >> 12) & 63];
        out[w++] = i + 1 < len ? B64[(v >> 6) & 63] : '=';
        out[w++] = i + 2 < len ? B64[v & 63] : '=';
    }
    return w;
}

static int b64_val(char c)
{
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    return c == '+' ? 62 : c == '/' ? 63 : -1;
}

/* Strict: length a multiple of 4, '=' only as the last one or two, nothing else. Bytes written, or -1. */
static int b64_decode(const char *in, size_t len, uint8_t *out, size_t cap)
{
    if (len % 4) return -1;
    size_t w = 0;
    for (size_t i = 0; i < len; i += 4) {
        int a = b64_val(in[i]), b = b64_val(in[i + 1]);
        int c = in[i + 2] == '=' ? -2 : b64_val(in[i + 2]);
        int d = in[i + 3] == '=' ? -2 : b64_val(in[i + 3]);
        if (a < 0 || b < 0 || c == -1 || d == -1) return -1;
        if (c == -2 && d != -2) return -1;
        if ((c == -2 || d == -2) && i + 4 != len) return -1;
        size_t n = c == -2 ? 1 : d == -2 ? 2 : 3;
        if (w + n > cap) return -1;
        uint32_t v = ((uint32_t)a << 18) | ((uint32_t)b << 12) | ((uint32_t)(c < 0 ? 0 : c) << 6) | (uint32_t)(d < 0 ? 0 : d);
        out[w++] = (uint8_t)(v >> 16);
        if (n > 1) out[w++] = (uint8_t)(v >> 8);
        if (n > 2) out[w++] = (uint8_t)v;
        if (n == 1 && (b & 15)) return -1;                  /* stray bits in the padding */
        if (n == 2 && (c & 3)) return -1;
    }
    return (int)w;
}

/* ---- paths ------------------------------------------------------------------------- */

/* "/MAP/map.dat" -> "MAP/MAP.DAT"; "/" -> "". 0 if it is not a path on the card: relative, "..", "//", a backslash,
 * characters other than A-Z a-z 0-9 _ . -, a segment over 12 characters or only dots, more than 64 characters. */
static int split_path(const char *in, char *out, size_t cap, int *depth)
{
    size_t len = strlen(in);
    if (in[0] != '/' || len > 64 || strchr(in, '\\') || cap < 66) return 0;
    size_t w = 0;
    int segs = 0;
    const char *p = in + 1;
    while (*p) {
        const char *e = strchr(p, '/');
        size_t n = e ? (size_t)(e - p) : strlen(p);
        if (n == 0) {
            if (!e) break;                                  /* cannot happen: *p != 0 */
            return 0;                                       /* "//" */
        }
        if (n > 12) return 0;
        int dots = 0;
        for (size_t i = 0; i < n; i++) {
            char c = p[i];
            if (c == '.') dots++;
            else if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) return 0;
        }
        if ((size_t)dots == n) return 0;                    /* ".", "..", "..." */
        if (segs) out[w++] = '/';
        for (size_t i = 0; i < n; i++) out[w++] = (char)((p[i] >= 'a' && p[i] <= 'z') ? p[i] - 32 : p[i]);
        segs++;
        if (!e) break;
        p = e + 1;                                          /* a trailing slash ends the loop with *p == 0 */
    }
    out[w] = 0;
    *depth = segs;
    return 1;
}

static int is_root_file(const char *path, const char *name) { return strcmp(path, name) == 0; }

static int can_write(const char *path, int depth)
{
    if (depth == 1) return is_root_file(path, "HANDHELD.CFG") || is_root_file(path, "HANDHELD.MAP");
    return depth == 2 && strncmp(path, "MAP/", 4) == 0 && hh_serial_mapup_name_ok(path, strlen(path));
}

static int can_remove(const char *path, int depth)
{
    return can_write(path, depth) || (depth == 2 && strncmp(path, "DRIVES/", 7) == 0);
}

static uint32_t max_size(const char *path, int depth)
{
    if (depth == 1) return is_root_file(path, "HANDHELD.CFG") ? 8u * 1024u : 256u * 1024u;
    return HH_SER_MAPUP_SIZE_MAX;
}

/* ---- the session ------------------------------------------------------------------- */

static const char *reason(int status)
{
    switch (status) {
    case HH_SER_E_IO: return "io";
    case HH_SER_E_CRC: return "crc";
    case HH_SER_E_ARG: return "args";
    case HH_SER_E_BUSY: return "busy";
    case HH_SER_E_REFUSED: return "refused";
    default: return "state";
    }
}

static void release_map(hh_serial_t *s)
{
    if (s->hl.held) { s->hl.held = 0; if (s->map_hold) s->map_hold(0); }
}

/* The PUT in progress is given up (kept = a MAP/ file's temporary file stays for a resume). */
static void put_stop(hh_serial_t *s, int keep)
{
    hh_hl_t *h = &s->hl;
    if (h->put_kind == 3 && h->held) {
        if (keep) { if (s->fs.mapup_pause) s->fs.mapup_pause(s->fs.ctx); }
        else if (s->fs.mapup_abort) s->fs.mapup_abort(s->fs.ctx);
    }
    release_map(s);
    if (!keep) h->put_kind = 0;
}

static void get_stop(hh_serial_t *s) { s->hl.get_on = 0; s->hl.ls_on = 0; }

static void end_session(hh_serial_t *s)
{
    hh_hl_t *h = &s->hl;
    get_stop(s);
    put_stop(s, 1);                     /* the partial file stays: the next session can carry on */
    h->open = 0; h->skip_data = 0;
    static const char zero[1] = {0};
    hh_serial_push_text(s, zero, 1);    /* closes anything half-sent */
    s->next_hello = now(s);             /* the stream starts again with a hello */
    s->next_stats = now(s) + 0.2;
}

static void send_hello(hh_serial_t *s)
{
    hh_core_info_t info;
    memset(&info, 0, sizeof(info));
    if (s->core) hh_core_info(s->core, &info);
    say(s, "HL HELLO fw=%s model=" MODEL " proto=1 id=%.12s map=%.12s variant=%s chunk=%d win=%d", HH_FW_VERSION,
        info.device, info.map_hash, s->variant == HH_SER_VARIANT_GPS_P1 ? "gps-p1" : "default", HH_HL_CHUNK, HH_HL_WINDOW);
}

static void send_stat(hh_serial_t *s)
{
    static const char *const LINK[] = {"no reply from the ECU", "connecting", "ok", "OBD2 only", "K-line cable not found",
                                       "via laptop"};
    hh_snapshot_t snap;
    hh_core_info_t info;
    memset(&snap, 0, sizeof(snap));
    memset(&info, 0, sizeof(info));
    if (s->core) { hh_core_snapshot(s->core, &snap); hh_core_info(s->core, &info); }
    const hh_link_stats_t *ls = s->core ? hh_core_link_stats(s->core) : NULL;
    char ecu[24] = "";
    for (int i = 0; i < info.ecu_id_len && i < 10; i++) snprintf(ecu + 2 * i, 3, "%02x", info.ecu_id[i]);
    const char *src = snap.time_source == HH_TS_NMEA || snap.time_source == HH_TS_PPS ? "gps"
                    : snap.time_source == HH_TS_HOST ? "host" : "none";
    unsigned state = snap.link.state > 5 ? 0 : snap.link.state;
    say(s,
        "HL STAT {\"t_ms\":%lld,\"fw\":\"%s\",\"boot\":%u,"
        "\"link\":{\"state\":%u,\"name\":\"%s\",\"hz\":%.2f,\"reads\":%u,\"errors\":%u,\"timeouts\":%u,\"checksum\":%u,"
        "\"retries\":%u,\"reconnects\":%u},"
        "\"ecu\":{\"id\":\"%s\",\"len\":%u},"
        "\"gps\":{\"fix\":%u,\"sats\":%u,\"lat\":%.7f,\"lon\":%.7f,\"speed_kmh\":%.1f,\"course\":%.1f,\"hdop\":%.2f,"
        "\"nmea_good\":%u,\"nmea_bad\":%u},"
        "\"sd\":{\"ok\":%u,\"free_mb\":%u},"
        "\"drive\":{\"recording\":%u,\"no\":%u,\"bytes\":%u,\"markers\":%u,\"stalls\":%u,\"armed\":%u},"
        "\"time\":{\"unix_ms\":%lld,\"source\":\"%s\"},"
        "\"serial\":{\"baud\":%u,\"tx_frames\":%u,\"tx_dropped\":%u,\"rx_frames\":%u,\"rx_bad\":%u,\"ring_peak\":%u}}",
        (long long)(now(s) * 1000.0), HH_FW_VERSION, (unsigned)info.boot_no,
        state, LINK[state], fin(snap.link.hz_fast), (unsigned)snap.link.reads, (unsigned)snap.link.errors,
        ls ? (unsigned)ls->timeouts : 0u, ls ? (unsigned)ls->checksum_failures : 0u, ls ? (unsigned)ls->retries : 0u,
        ls ? (unsigned)ls->reconnects : 0u,
        info.ecu_id_len ? ecu : "", (unsigned)info.ecu_id_len,
        (unsigned)snap.gps.fix, (unsigned)snap.gps.sats, fin(snap.gps.lat), fin(snap.gps.lon), fin(snap.gps.speed_kmh),
        fin(snap.gps.heading), fin(snap.gps.hdop), (unsigned)snap.nmea.good, (unsigned)snap.nmea.bad,
        (unsigned)snap.log.sd_ok, snap.log.free_mb > 0 ? (unsigned)snap.log.free_mb : 0u,
        (unsigned)snap.log.recording, snap.log.recording ? (unsigned)snap.log.drive_no : 0u, (unsigned)snap.log.bytes,
        (unsigned)snap.markers, (unsigned)snap.stall.count, (unsigned)snap.stall.armed,
        (long long)(snap.unix_us / 1000), src,
        (unsigned)s->baud, (unsigned)s->tx_frames, (unsigned)s->tx_dropped, (unsigned)s->rx_frames, (unsigned)s->rx_bad,
        (unsigned)s->ring_peak);
}

/* ---- LS -------------------------------------------------------------------------- */

typedef struct { hh_serial_t *s; const char *base; } ls_arg_t;

static int ls_emit(void *arg, int is_dir, const char *name, uint32_t size, uint32_t mtime)
{
    ls_arg_t *a = arg;
    hh_serial_t *s = a->s;
    if (hh_serial_ring_free(s) < 1500) return 1;            /* the laptop is behind: go on when it has caught up */
    char up[16];
    size_t n = 0;
    for (; name[n] && n < sizeof(up) - 1; n++) up[n] = (char)((name[n] >= 'a' && name[n] <= 'z') ? name[n] - 32 : name[n]);
    up[n] = 0;
    if (is_dir) say(s, "HL D %s/%s", a->base, up);
    else say(s, "HL F %u %u %s/%s", (unsigned)size, (unsigned)mtime, a->base, up);
    s->hl.ls_skip++;
    return 0;
}

static void ls_pump(hh_serial_t *s)
{
    hh_hl_t *h = &s->hl;
    char base[HH_HL_PATH_MAX + 2];
    snprintf(base, sizeof(base), "%s%s", h->ls_path[0] ? "/" : "", h->ls_path);
    ls_arg_t a = {s, base};
    int rc = s->fs.hl_ls(s->fs.ctx, h->ls_path, h->ls_skip, ls_emit, &a);
    if (rc == 1) return;                                    /* stopped for room */
    h->ls_on = 0;
    if (rc < 0 && h->ls_skip == 0) say(s, "HL ERR nofile");
    else say(s, "HL END");
}

/* ---- GET ------------------------------------------------------------------------- */

static void get_begin(hh_serial_t *s, const char *path)
{
    hh_hl_t *h = &s->hl;
    uint32_t size = 0;
    int rc = s->fs.hl_read(s->fs.ctx, path, 0, s->hl_io, 0, &size);
    if (rc == HH_SER_E_BUSY) { say(s, "HL ERR busy"); return; }
    if (rc < 0) { say(s, rc == HH_SER_E_ARG ? "HL ERR nofile" : "HL ERR io"); return; }
    snprintf(h->get_path, sizeof(h->get_path), "%s", path);
    h->get_off = 0; h->get_size = size; h->get_crc = 0; h->get_on = 1;
    say(s, "HL BEGIN %u", (unsigned)size);
}

static void get_pump(hh_serial_t *s)
{
    hh_hl_t *h = &s->hl;
    for (int guard = 0; h->get_on && guard < 8 && hh_serial_ring_free(s) >= HH_HL_LINE_MAX + 2048; guard++) {
        if (h->get_off >= h->get_size) {
            h->get_on = 0;
            say(s, "HL END %08x", (unsigned)h->get_crc);
            return;
        }
        uint32_t size = h->get_size;
        int n = s->fs.hl_read(s->fs.ctx, h->get_path, h->get_off, s->hl_io, HH_HL_CHUNK, &size);
        if (n < 0 || (n == 0 && h->get_off < h->get_size)) {
            h->get_on = 0;
            say(s, n == HH_SER_E_BUSY ? "HL ERR busy" : "HL ERR io");
            return;
        }
        h->get_crc = hh_crc32(h->get_crc, s->hl_io, (size_t)n);
        h->get_off += (uint32_t)n;
        memcpy(s->hl_out, "HL DATA ", 8);
        size_t w = 8 + b64_encode(s->hl_io, (size_t)n, s->hl_out + 8);
        s->hl_out[w++] = '\n';
        hh_serial_push_text(s, s->hl_out, w);
    }
}

/* ---- PUT ------------------------------------------------------------------------- */

static void part_clear(hh_serial_t *s) { s->hl.put_kind = 0; s->hl.put_held = 0; s->hl.put_run = 0; }

static void put_begin(hh_serial_t *s, const char *path, int depth, const char *args)
{
    hh_hl_t *h = &s->hl;
    char *end;
    unsigned long n = strtoul(args, &end, 10);
    if (end == args || (*end != ' ' && *end != 0)) { say(s, "HL ERR args"); return; }
    while (*end == ' ') end++;
    const char *crc_s = end;
    unsigned long crc = strtoul(crc_s, &end, 16);
    if (end == crc_s || end - crc_s > 8 || (*end != ' ' && *end != 0)) { say(s, "HL ERR args"); return; }
    while (*end == ' ') end++;
    unsigned long offset = 0;
    if (*end) {
        const char *off_s = end;
        offset = strtoul(off_s, &end, 10);
        if (end == off_s || *end != 0) { say(s, "HL ERR args"); return; }
    }
    if (n > max_size(path, depth)) { say(s, "HL ERR size"); return; }
    if (n == 0) { say(s, "HL ERR size"); return; }          /* an empty file is not a map, a config or a map piece */
    if (s->mapup_active) { say(s, "HL ERR busy"); return; } /* the binary upload owns MAP/ until it is done */
    uint8_t kind = depth == 1 ? (is_root_file(path, "HANDHELD.MAP") ? 1 : 2) : 3;
    int resume = kind == 3 && h->put_kind == 3 && strcmp(h->put_path, path) == 0 && h->put_size == n
                 && h->put_crc == crc && offset > 0 && offset <= h->put_held;
    int rc = HH_SER_OK;
    if (!resume) { put_stop(s, 0); part_clear(s); }
    if (kind == 3) {
        if (!h->held) {
            if (s->map_hold && s->map_hold(1) != 0) { say(s, "HL ERR busy"); return; }
            h->held = 1;
        }
        if (resume) {                                       /* the temporary file is still there: carry on from offset */
            rc = s->fs.mapup_open(s->fs.ctx, path, (uint32_t)n, (uint32_t)offset);
            if (rc != HH_SER_OK) resume = 0;
        }
        if (!resume) rc = s->fs.mapup_open(s->fs.ctx, path, (uint32_t)n, 0);
    } else {
        s->put_kind = 0;                                    /* the binary PUT's state: this one replaces it */
        rc = s->fs.put_begin(s->fs.ctx, kind == 1 ? HH_SER_F_MAP : HH_SER_F_CFG, (uint32_t)n);
    }
    if (rc != HH_SER_OK) { put_stop(s, 0); part_clear(s); say(s, "HL ERR %s", reason(rc)); return; }
    h->put_kind = kind;
    snprintf(h->put_path, sizeof(h->put_path), "%s", path);
    h->put_size = (uint32_t)n; h->put_crc = (uint32_t)crc;
    h->put_held = resume ? (uint32_t)offset : 0;
    h->skip_data = 0; h->data_lines = 0;
    say(s, "HL READY %u", (unsigned)h->put_held);
}

static void data_fail(hh_serial_t *s, const char *why)
{
    s->hl.skip_data = 1;
    say(s, "HL ERR %s", why);
}

static void put_finish(hh_serial_t *s)
{
    hh_hl_t *h = &s->hl;
    uint32_t value = 0;
    int rc;
    if (h->put_kind == 3) {
        rc = s->fs.mapup_commit(s->fs.ctx, h->put_path, h->put_size, h->put_crc);
        release_map(s);
        part_clear(s);
        if (rc != HH_SER_OK) { say(s, "HL ERR %s", reason(rc)); return; }
        say(s, "HL OK");
        return;
    }
    rc = s->fs.put_end(s->fs.ctx, h->put_kind == 1 ? HH_SER_F_MAP : HH_SER_F_CFG, h->put_size, h->put_crc, &value);
    part_clear(s);
    if (rc != HH_SER_OK) { say(s, "HL ERR %s", reason(rc)); return; }
    if (strcmp(h->put_path, "HANDHELD.CFG") == 0) say(s, "HL OK skipped=%u", (unsigned)value);
    else say(s, "HL OK");
}

static void cmd_data(hh_serial_t *s, char *rest)
{
    hh_hl_t *h = &s->hl;
    if (h->open) h->deadline = now(s) + HH_HL_TIMEOUT_S;
    if (!h->open || h->put_kind == 0 || h->skip_data) {
        if (!h->skip_data) {
            say(s, h->open ? "HL ERR state" : "HL ERR nosession");
            h->skip_data = 1;
        }
        return;
    }
    long at = -1;
    while (*rest == ' ') rest++;
    if (*rest == '@') {
        char *end;
        at = strtol(rest + 1, &end, 10);
        if (end == rest + 1 || (*end != ' ' && *end != 0)) at = -2;
        rest = end;
        while (*rest == ' ') rest++;
    }
    size_t len = strlen(rest);
    while (len && rest[len - 1] == ' ') len--;
    int n = b64_decode(rest, len, s->hl_io, sizeof(s->hl_io));
    h->data_lines++;
    if (n < 0 || at == -2) { data_fail(s, "b64"); return; }
    if (at >= 0 && (uint32_t)at != h->put_held) { data_fail(s, "offset"); return; }
    if ((uint64_t)h->put_held + (uint32_t)n > h->put_size) { data_fail(s, "size"); return; }
    if (n == 0) { data_fail(s, "args"); return; }
    int rc;
    if (h->put_kind == 3) rc = s->fs.mapup_write(s->fs.ctx, h->put_held, s->hl_io, (uint16_t)n);
    else rc = s->fs.put_data(s->fs.ctx, h->put_held, s->hl_io, (uint16_t)n);
    if (rc != HH_SER_OK) {
        put_stop(s, 0);
        part_clear(s);
        data_fail(s, reason(rc));
        return;
    }
    h->put_held += (uint32_t)n;
    say(s, "HL ACK %u", (unsigned)h->put_held);
    if (h->put_held == h->put_size) put_finish(s);
}

/* ---- commands ---------------------------------------------------------------------- */

static int parse_path(hh_serial_t *s, const char *arg, char *out, int *depth)
{
    (void)s;
    while (*arg == ' ') arg++;
    return split_path(arg, out, HH_HL_PATH_MAX, depth);
}

static void cmd_rm(hh_serial_t *s, const char *arg)
{
    char path[HH_HL_PATH_MAX];
    int depth, rc;
    if (!parse_path(s, arg, path, &depth)) { say(s, "HL ERR path"); return; }
    if (!can_remove(path, depth)) { say(s, "HL ERR denied"); return; }
    int mapdir = strncmp(path, "MAP/", 4) == 0;
    if (mapdir) {
        if (s->mapup_active || (s->map_hold && s->map_hold(1) != 0)) { say(s, "HL ERR busy"); return; }
        s->hl.held = 1;
    }
    rc = s->fs.hl_rm(s->fs.ctx, path);
    if (mapdir) release_map(s);
    if (rc == HH_SER_OK) say(s, "HL OK");
    else say(s, rc == HH_SER_E_ARG ? "HL ERR nofile" : rc == HH_SER_E_BUSY ? "HL ERR busy" : "HL ERR io");
}

static void cmd_status(hh_serial_t *s, const char *arg)
{
    char path[HH_HL_PATH_MAX];
    int depth;
    hh_hl_t *h = &s->hl;
    if (!parse_path(s, arg, path, &depth)) { say(s, "HL ERR path"); return; }
    if (h->put_kind && strcmp(h->put_path, path) == 0)
        say(s, "HL PART %u %u %08x", (unsigned)h->put_held, (unsigned)h->put_size, (unsigned)h->put_crc);
    else say(s, "HL PART 0 0 00000000");
}

static void cmd_time(hh_serial_t *s, const char *arg)
{
    char *end;
    long long ms = strtoll(arg, &end, 10);
    if (end == arg || *end || ms <= 0) { say(s, "HL ERR args"); return; }
    if (!s->core) { say(s, "HL ERR state"); return; }
    say(s, hh_core_set_time(s->core, ms * 1000) ? "HL OK ignored" : "HL OK");
}

void hh_hl_line(hh_serial_t *s, char *line, size_t len)
{
    hh_hl_t *h = &s->hl;
    char *p = line + 2;
    while (*p == ' ') p++;
    char *cmd = p;
    while (*p && *p != ' ') { if (*p >= 'a' && *p <= 'z') *p -= 32; p++; }
    char *rest = p;
    if (*rest) *rest++ = 0;
    while (*rest == ' ') rest++;
    size_t rl = strlen(rest);
    while (rl && (rest[rl - 1] == ' ' || rest[rl - 1] == '\t')) rest[--rl] = 0;
    (void)len;

    if (strcmp(cmd, "DATA") == 0) { cmd_data(s, rest); return; }
    if (h->open && strcmp(cmd, "STAT") != 0) h->deadline = now(s) + HH_HL_TIMEOUT_S;
    h->skip_data = 0;

    if (strcmp(cmd, "HELLO") == 0) {
        get_stop(s);
        if (!h->open) {
            h->open = 1;
            static const char zero[1] = {0};
            hh_serial_push_text(s, zero, 1);
        }
        h->deadline = now(s) + HH_HL_TIMEOUT_S;
        send_hello(s);
        return;
    }
    if (!h->open) { say(s, "HL ERR nosession"); return; }
    int card = s->fs.hl_ls && s->fs.hl_read && s->fs.hl_rm;
    if (!card && (!strcmp(cmd, "LS") || !strcmp(cmd, "GET") || !strcmp(cmd, "PUT") || !strcmp(cmd, "STATUS")
                  || !strcmp(cmd, "CFG") || !strcmp(cmd, "RM"))) { say(s, "HL ERR nosd"); return; }
    get_stop(s);

    if (!strcmp(cmd, "BYE")) { say(s, "HL OK bye"); end_session(s); return; }
    if (!strcmp(cmd, "MARK")) { if (s->core) hh_core_mark(s->core); say(s, "HL OK"); return; }
    if (!strcmp(cmd, "TIME")) { cmd_time(s, rest); return; }
    if (!strcmp(cmd, "REBOOT")) {
        say(s, "HL OK reboot");
        h->reboot_at = now(s) + 0.5;
        end_session(s);
        return;
    }
    if (!strcmp(cmd, "STAT")) { send_stat(s); return; }
    if (!strcmp(cmd, "LS") || !strcmp(cmd, "GET") || !strcmp(cmd, "RM") || !strcmp(cmd, "STATUS") || !strcmp(cmd, "PUT")
        || !strcmp(cmd, "CFG")) {
        char path[HH_HL_PATH_MAX], tail[16];
        int depth;
        const char *arg = rest;
        if (!strcmp(cmd, "CFG")) {
            char *sp = strchr(rest, ' ');
            size_t sl = sp ? (size_t)(sp - rest) : strlen(rest);
            if (sl >= sizeof(tail)) { say(s, "HL ERR args"); return; }
            memcpy(tail, rest, sl); tail[sl] = 0;
            for (char *q = tail; *q; q++) if (*q >= 'a' && *q <= 'z') *q -= 32;
            arg = sp ? sp + 1 : "";
            while (*arg == ' ') arg++;
            if (!strcmp(tail, "GET")) { get_begin(s, "HANDHELD.CFG"); return; }
            if (!strcmp(tail, "PUT")) { put_begin(s, "HANDHELD.CFG", 1, arg); return; }
            say(s, "HL ERR args");
            return;
        }
        if (!strcmp(cmd, "RM")) { cmd_rm(s, rest); return; }
        if (!strcmp(cmd, "STATUS")) { cmd_status(s, rest); return; }
        if (!strcmp(cmd, "LS")) {
            if (!*rest) rest = (char *)"/";
            if (!parse_path(s, rest, path, &depth)) { say(s, "HL ERR path"); return; }
            snprintf(h->ls_path, sizeof(h->ls_path), "%s", path);
            h->ls_skip = 0; h->ls_on = 1;
            ls_pump(s);
            return;
        }
        if (!strcmp(cmd, "GET")) {
            if (!parse_path(s, rest, path, &depth)) { say(s, "HL ERR path"); return; }
            get_begin(s, path);
            get_pump(s);
            return;
        }
        /* PUT <path> <size> <crc32 hex> [offset] */
        char *sp = strchr(rest, ' ');
        if (!sp) { say(s, "HL ERR path"); return; }
        *sp = 0;
        if (!split_path(rest, path, sizeof(path), &depth)) { say(s, "HL ERR path"); return; }
        if (!can_write(path, depth)) { say(s, "HL ERR denied"); return; }
        put_begin(s, path, depth, sp + 1);
        return;
    }
    say(s, "HL ERR badcmd");
}

void hh_hl_tick(hh_serial_t *s)
{
    hh_hl_t *h = &s->hl;
    if (h->reboot_at > 0 && now(s) >= h->reboot_at) {
        h->reboot_at = 0;
        if (s->reboot) s->reboot(s->lock_ctx);
    }
    if (!h->open) return;
    if (now(s) > h->deadline) {
        say(s, "HL TIMEOUT");
        end_session(s);
        return;
    }
    if (h->ls_on) ls_pump(s);
    if (h->get_on) get_pump(s);
}

#endif /* !HH_SERIAL_TX_ONLY */
