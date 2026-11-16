/* hh_serial: the link to the laptop. See include/hh_serial.h and SERIAL.md. */
#include <string.h>

#include "hh_serial.h"

#if defined(HH_GPS_P1) && HH_GPS_P1 && !defined(HH_SERIAL_TX_ONLY)
#define HH_SERIAL_TX_ONLY 1     /* the GPS has UART0's RX pin: nothing from the laptop can be heard */
#endif

static double now(const hh_serial_t *s) { return s->clk.now(s->clk.ctx); }
static uint64_t us(double t) { return t <= 0 ? 0 : (uint64_t)(t * 1e6 + 0.5); }
static void lock(hh_serial_t *s) { if (s->lock) s->lock(s->lock_ctx); }
static void unlock(hh_serial_t *s) { if (s->unlock) s->unlock(s->lock_ctx); }

/* ---- framing ------------------------------------------------------------------ */

uint16_t hh_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (int b = 0; b < 8; b++) crc = (uint16_t)((crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1);
    }
    return crc;
}

size_t hh_cobs_encode(const uint8_t *in, size_t len, uint8_t *out)
{
    size_t code_at = 0, w = 1;
    uint8_t code = 1;
    for (size_t i = 0; i < len; i++) {
        if (in[i] == 0) {
            out[code_at] = code; code_at = w++; code = 1;
        } else {
            out[w++] = in[i];
            if (++code == 0xFF) { out[code_at] = code; code_at = w++; code = 1; }
        }
    }
    out[code_at] = code;
    return w;
}

int hh_cobs_decode(const uint8_t *in, size_t len, uint8_t *out)
{
    size_t i = 0, w = 0;
    while (i < len) {
        uint8_t code = in[i];
        if (code == 0 || i + code > len) return -1;
        memcpy(out + w, in + i + 1, (size_t)code - 1);
        w += (size_t)code - 1;
        i += code;
        if (code != 0xFF && i < len) out[w++] = 0;
    }
    return (int)w;
}

size_t hh_ser_frame(uint8_t type, uint8_t seq, const uint8_t *payload, size_t len, uint8_t *out)
{
    uint8_t raw[HH_SER_RAW_MAX];
    if (len > HH_SER_MAX_PAYLOAD) return 0;
    raw[0] = HH_SER_PROTO; raw[1] = type; raw[2] = seq;
    if (len) memcpy(raw + 3, payload, len);
    uint16_t crc = hh_crc16(raw, 3 + len);
    raw[3 + len] = (uint8_t)crc; raw[4 + len] = (uint8_t)(crc >> 8);
    size_t n = hh_cobs_encode(raw, 5 + len, out);
    out[n++] = 0;
    return n;
}

/* ---- the ring ------------------------------------------------------------------ */

void hh_serial_init(hh_serial_t *s, const hh_clock_t *clk, uint8_t *mem, uint32_t size, uint8_t variant,
                    uint16_t caps, uint32_t baud)
{
    memset(s, 0, sizeof(*s));
    s->clk = *clk;
    hh_ring_init(&s->ring, mem, mem ? size : 0);
    s->variant = variant;
#ifdef HH_SERIAL_TX_ONLY
    caps &= (uint16_t)~(HH_SER_CAP_RX | HH_SER_CAP_FILES | HH_SER_CAP_PUT | HH_SER_CAP_MAPUP);
#endif
    if (!(caps & HH_SER_CAP_RX)) caps &= (uint16_t)~(HH_SER_CAP_FILES | HH_SER_CAP_PUT | HH_SER_CAP_MAPUP);
    s->caps = caps;
    s->baud = baud;
    s->last_rx = -1;
}

void hh_serial_attach(hh_serial_t *s, hh_core_t *core) { s->core = core; }

int hh_serial_push(hh_serial_t *s, uint8_t type, const void *head, size_t head_len, const void *data, size_t data_len)
{
    size_t len = head_len + data_len;
    if (!s->ring.buf || len > HH_SER_MAX_PAYLOAD || len + 4 > s->ring.cap) return -1;
    lock(s);
    uint8_t *item = s->item;            /* any task may log or tap: the frame is built here, not on its stack */
    item[0] = type;
    item[1] = s->seq++;                 /* numbered here: the laptop sees a gap where frames were dropped */
    if (head_len) memcpy(item + 2, head, head_len);
    if (data_len) memcpy(item + 2 + head_len, data, data_len);
    while (s->ring.cap - s->ring.used < len + 4) {
        hh_ring_pop(&s->ring);          /* the laptop is not keeping up (or is not there): the oldest go */
        s->tx_dropped++;
    }
    hh_ring_push(&s->ring, item, (uint16_t)(len + 2));
    if (s->ring.used > s->ring_peak) s->ring_peak = s->ring.used;
    unlock(s);
    if (s->wake) s->wake(s->lock_ctx);
    return 0;
}

void hh_serial_tap(void *ctx, uint8_t kind, const void *rec, size_t len, const void *data, size_t data_len)
{
    static const uint8_t TYPE[] = {0, HH_SF_READ, HH_SF_TICK, HH_SF_LINK, HH_SF_GPS, HH_SF_EVENT, HH_SF_ISO};
    if (kind == 0 || kind >= sizeof(TYPE)) return;
    hh_serial_push(ctx, TYPE[kind], rec, len, data, data_len);
}

void hh_serial_log(hh_serial_t *s, const char *text, size_t len)
{
    while (len) {
        size_t n = len > 240 ? 240 : len;
        hh_serial_push(s, HH_SF_LOG, text, n, NULL, 0);
        text += n; len -= n;
    }
}

int hh_serial_host_attached(const hh_serial_t *s)
{
    return s->last_rx >= 0 && now(s) - s->last_rx < HH_SER_HOST_S;
}

/* ---- hello, stats ---------------------------------------------------------------- */

static void push_hello(hh_serial_t *s)
{
    hh_ser_hello_t h;
    hh_core_info_t info;
    memset(&h, 0, sizeof(h));
    memset(&info, 0, sizeof(info));
    if (s->core) hh_core_info(s->core, &info);
    memcpy(h.magic, "HHSL", 4);
    h.rev = HH_SER_REV;
    h.variant = s->variant;
    h.caps = s->caps;
    if (!info.sd_ok) h.caps &= (uint16_t)~(HH_SER_CAP_FILES | HH_SER_CAP_PUT | HH_SER_CAP_MAPUP);
    strncpy(h.fw, HH_FW_VERSION, sizeof(h.fw) - 1);
    memcpy(h.device, info.device, sizeof(h.device));
    memcpy(h.map_hash, info.map_hash, sizeof(h.map_hash));
    memcpy(h.map_sha, info.map_sha, sizeof(h.map_sha));
    memcpy(h.ecu_id, info.ecu_id, sizeof(h.ecu_id));
    h.ecu_id_len = info.ecu_id_len;
    h.link_state = info.link_state; h.backend = info.backend; h.link_mode = info.link_mode;
    h.n_ch = info.n_ch;
    h.boot_no = info.boot_no;
    h.baud = s->baud;
    h.t_us = us(now(s));
    if (s->core) {
        hh_snapshot_t snap;
        hh_core_snapshot(s->core, &snap);
        h.unix_us = snap.unix_us;
        if (snap.unix_us) h.t_us = (uint64_t)snap.time_t_us;    /* the pair is for one instant */
    }
    h.flags = (uint8_t)((info.builtin_map ? 1 : 0) | (h.unix_us ? 2 : 0) | (info.time_valid ? 4 : 0) | (info.sd_ok ? 8 : 0)
                        | (info.time_source == HH_TS_PPS ? 16 : 0));
    h.hello_ms = (uint16_t)(HH_SER_HELLO_S * 1000);
    h.max_payload = HH_SER_MAX_PAYLOAD;
    hh_serial_push(s, HH_SF_HELLO, &h, sizeof(h), NULL, 0);
}

static void push_stats(hh_serial_t *s)
{
    hh_ser_stats_t st;
    hh_snapshot_t snap;
    if (!s->core) return;
    memset(&st, 0, sizeof(st));
    hh_core_snapshot(s->core, &snap);
    const hh_link_stats_t *ls = hh_core_link_stats(s->core);
    /* (t_us, unix_us) is one instant: the laptop pairs them to carry the pulse's accuracy over to the stream's device times */
    st.t_us = snap.unix_us ? (uint64_t)snap.time_t_us : us(now(s));
    st.unix_us = snap.unix_us;
    st.time_source = snap.time_source;
    st.time_err_us = snap.time_err_us;
    st.reads = snap.link.reads; st.errors = snap.link.errors;
    st.timeouts = ls->timeouts; st.checksum_failures = ls->checksum_failures;
    st.retries = ls->retries; st.reconnects = ls->reconnects;
    float hz = snap.link.hz_fast > 650 ? 650 : snap.link.hz_fast < 0 ? 0 : snap.link.hz_fast;
    st.hz_c = (uint16_t)(hz * 100.0f + 0.5f);
    st.link_state = snap.link.state;
    st.flags = (uint8_t)((snap.log.sd_ok ? HH_SER_ST_SD_OK : 0) | (snap.log.recording ? HH_SER_ST_RECORDING : 0)
                         | (snap.gps.fix ? HH_SER_ST_GPS_FIX : 0) | (snap.unix_us ? HH_SER_ST_TIME : 0)
                         | (snap.stall.armed ? HH_SER_ST_ARMED : 0) | (snap.stall.triggered ? HH_SER_ST_CAPTURING : 0)
                         | (hh_serial_host_attached(s) ? HH_SER_ST_HOST : 0) | (snap.map_ok ? 0 : HH_SER_ST_BUILTIN));
    st.drive_no = snap.log.recording ? snap.log.drive_no : 0;
    st.log_bytes = snap.log.bytes;
    st.free_mb = snap.log.free_mb > 0 ? (uint32_t)snap.log.free_mb : 0;
    st.markers = snap.markers; st.stall_count = snap.stall.count;
    st.nmea_good = snap.nmea.good; st.nmea_bad = snap.nmea.bad;
    lock(s);
    st.tx_frames = s->tx_frames; st.tx_dropped = s->tx_dropped; st.tx_bytes = s->tx_bytes;
    st.rx_frames = s->rx_frames; st.rx_bad = s->rx_bad; st.ring_peak = s->ring_peak;
    unlock(s);
    hh_serial_push(s, HH_SF_STATS, &st, sizeof(st), NULL, 0);
}

size_t hh_serial_poll(hh_serial_t *s, uint8_t *out, size_t cap)
{
    size_t w = 0;
    double t = now(s);
    if (t >= s->next_hello) { s->next_hello = t + HH_SER_HELLO_S; push_hello(s); }
    if (t >= s->next_stats) { s->next_stats = t + HH_SER_STATS_S; push_stats(s); }
    while (cap - w >= HH_SER_FRAME_MAX) {
        uint8_t item[2 + HH_SER_MAX_PAYLOAD];
        uint32_t pos;
        lock(s);
        pos = s->ring.tail;
        uint16_t n = hh_ring_peek(&s->ring, &pos, item, sizeof(item));
        if (n) hh_ring_pop(&s->ring);
        else if (s->ring.used) hh_ring_clear(&s->ring);     /* cannot happen; never spin on it */
        unlock(s);
        if (n < 2) break;
        size_t len = hh_ser_frame(item[0], item[1], item + 2, (size_t)n - 2, out + w);
        w += len;
        s->tx_frames++;
        s->tx_bytes += (uint32_t)len;
    }
    return w;
}

/* ---- commands (two-way builds) ---------------------------------------------------- */

#ifndef HH_SERIAL_TX_ONLY

static void ack(hh_serial_t *s, uint8_t cmd, uint8_t seq, int status, uint32_t value)
{
    hh_ser_ack_t a = {cmd, seq, (int16_t)status, value};
    hh_serial_push(s, HH_SF_ACK, &a, sizeof(a), NULL, 0);
}

static void cmd_list(hh_serial_t *s, uint8_t seq, const uint8_t *p, size_t len)
{
    hh_ser_list_t req;
    hh_ser_file_info_t info[HH_SER_LIST_PAGE], end;
    int more = 0, n;
    if (len < sizeof(req)) { ack(s, HH_SC_LIST_FILES, seq, HH_SER_E_ARG, 0); return; }
    if (!s->fs.list || !(s->caps & HH_SER_CAP_FILES)) { ack(s, HH_SC_LIST_FILES, seq, HH_SER_E_UNSUPPORTED, 0); return; }
    memcpy(&req, p, sizeof(req));
    n = s->fs.list(s->fs.ctx, req.kind, req.after_no, info, HH_SER_LIST_PAGE, &more);
    if (n < 0) { ack(s, HH_SC_LIST_FILES, seq, n, 0); return; }
    for (int i = 0; i < n; i++) hh_serial_push(s, HH_SF_FILE_INFO, &info[i], sizeof(info[i]), NULL, 0);
    memset(&end, 0, sizeof(end));
    end.kind = req.kind; end.flags = more ? HH_SER_FI_MORE : 0; end.file_no = 0xFFFFFFFFu; end.size = (uint32_t)n;
    hh_serial_push(s, HH_SF_FILE_INFO, &end, sizeof(end), NULL, 0);
}

static void cmd_read(hh_serial_t *s, uint8_t seq, const uint8_t *p, size_t len)
{
    hh_ser_read_file_t req;
    hh_ser_file_data_t d;
    uint8_t buf[HH_SER_CHUNK];
    uint32_t size = 0;
    if (len < sizeof(req)) { ack(s, HH_SC_READ_FILE, seq, HH_SER_E_ARG, 0); return; }
    if (!s->fs.read || !(s->caps & HH_SER_CAP_FILES)) { ack(s, HH_SC_READ_FILE, seq, HH_SER_E_UNSUPPORTED, 0); return; }
    memcpy(&req, p, sizeof(req));
    if (req.len > HH_SER_CHUNK) req.len = HH_SER_CHUNK;
    int n = s->fs.read(s->fs.ctx, req.kind, req.file_no, req.offset, buf, req.len, &size);
    memset(&d, 0, sizeof(d));
    d.kind = req.kind; d.file_no = req.file_no;
    if (n < 0) {                        /* no such file, or it is still being written */
        d.offset = 0xFFFFFFFFu;
        d.flags = n == HH_SER_E_BUSY ? HH_SER_FD_BUSY : 0;
        hh_serial_push(s, HH_SF_FILE_DATA, &d, sizeof(d), NULL, 0);
        return;
    }
    d.offset = req.offset; d.len = (uint16_t)n;
    d.crc32 = hh_crc32(0, buf, (size_t)n);
    if (req.offset + (uint32_t)n >= size) d.flags |= HH_SER_FD_EOF;
    hh_serial_push(s, HH_SF_FILE_DATA, &d, sizeof(d), buf, (size_t)n);
}

static void cmd_put(hh_serial_t *s, uint8_t type, uint8_t seq, const uint8_t *p, size_t len)
{
    if (!s->fs.put_begin || !s->fs.put_data || !s->fs.put_end || !(s->caps & HH_SER_CAP_PUT)) {
        ack(s, type, seq, HH_SER_E_UNSUPPORTED, 0);
        return;
    }
    if (type == HH_SC_PUT_BEGIN) {
        hh_ser_put_begin_t b;
        if (len < sizeof(b)) { ack(s, type, seq, HH_SER_E_ARG, 0); return; }
        memcpy(&b, p, sizeof(b));
        if ((b.kind != HH_SER_F_MAP && b.kind != HH_SER_F_CFG) || b.size == 0
            || b.size > (b.kind == HH_SER_F_MAP ? 256u * 1024u : 8u * 1024u)) { ack(s, type, seq, HH_SER_E_ARG, 0); return; }
        if (s->put_kind == b.kind && s->put_size == b.size && s->put_crc == b.crc32) {
            ack(s, type, seq, HH_SER_OK, s->put_got);       /* the same file again: carry on where it stopped */
            return;
        }
        s->put_kind = 0;
        int rc = s->fs.put_begin(s->fs.ctx, b.kind, b.size);
        if (rc != HH_SER_OK) { ack(s, type, seq, rc, 0); return; }
        s->put_kind = b.kind; s->put_size = b.size; s->put_crc = b.crc32; s->put_got = 0;
        ack(s, type, seq, HH_SER_OK, 0);
    } else if (type == HH_SC_PUT_DATA) {
        uint32_t offset;
        if (len < 4) { ack(s, type, seq, HH_SER_E_ARG, 0); return; }
        if (!s->put_kind) { ack(s, type, seq, HH_SER_E_STATE, 0); return; }
        memcpy(&offset, p, 4);
        size_t n = len - 4;
        if (offset + n <= s->put_got) { ack(s, type, seq, HH_SER_OK, s->put_got); return; }     /* sent twice */
        if (offset != s->put_got) { ack(s, type, seq, HH_SER_E_OFFSET, s->put_got); return; }
        if (offset + n > s->put_size) { ack(s, type, seq, HH_SER_E_ARG, s->put_got); return; }
        int rc = s->fs.put_data(s->fs.ctx, offset, p + 4, (uint16_t)n);
        if (rc != HH_SER_OK) { s->put_kind = 0; ack(s, type, seq, rc, 0); return; }
        s->put_got += (uint32_t)n;
        ack(s, type, seq, HH_SER_OK, s->put_got);
    } else {
        uint32_t value = 0;
        if (!s->put_kind) { ack(s, type, seq, HH_SER_E_STATE, 0); return; }
        if (s->put_got != s->put_size) { ack(s, type, seq, HH_SER_E_STATE, s->put_got); return; }
        int rc = s->fs.put_end(s->fs.ctx, s->put_kind, s->put_size, s->put_crc, &value);
        s->put_kind = 0;
        ack(s, type, seq, rc, value);
    }
}

/* ---- MAP/ uploads ---------------------------------------------------------------- */

/* "MAP/" and an 8.3 name: 1-8 of A-Z 0-9 _ - ~ $, optionally a dot and 0-3 of the same. No other
 * folder, no "..", no lower case, no spaces. The temporary file's own name is not one to upload. */
static int mapup_name_ok(const char *name, size_t len)
{
    if (len < 5 || len > HH_SER_MAPUP_NAME_MAX || memcmp(name, "MAP/", 4) != 0) return 0;
    const char *f = name + 4;
    size_t n = len - 4, base = 0, ext = 0;
    int dot = 0;
    for (size_t i = 0; i < n; i++) {
        char c = f[i];
        if (c == '.') {
            if (dot || base == 0) return 0;
            dot = 1;
        } else if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '~' || c == '$') {
            if (dot) ext++; else base++;
        } else {
            return 0;
        }
    }
    if (base == 0 || base > 8 || ext > 3) return 0;
    return !(len == 4 + sizeof(HH_SER_MAPUP_TMP) - 1 && memcmp(f, HH_SER_MAPUP_TMP, sizeof(HH_SER_MAPUP_TMP) - 1) == 0);
}

/* The upload is over (or abandoned): the temporary file goes, and the map reader has its files. */
static void mapup_release(hh_serial_t *s)
{
    if (s->mapup_active) { if (s->fs.mapup_abort) s->fs.mapup_abort(s->fs.ctx); s->mapup_active = 0; }
    if (s->mapup_held) { s->mapup_held = 0; if (s->map_hold) s->map_hold(0); }
}

/* Quiet for a while: the map reader may have its files back (the old ones are whole until a commit);
 * the temporary file stays where it is. */
static void mapup_pause(hh_serial_t *s)
{
    if (!s->mapup_held) return;
    if (s->fs.mapup_pause) s->fs.mapup_pause(s->fs.ctx);
    s->mapup_held = 0;
    if (s->map_hold) s->map_hold(0);
}

/* An upload that exists but is paused takes the folder again: 0, or a status for the ACK. */
static int mapup_take(hh_serial_t *s)
{
    if (s->mapup_held) return HH_SER_OK;
    if (s->map_hold && s->map_hold(1) != 0) return HH_SER_E_BUSY;     /* the reader is still drawing: try again */
    s->mapup_held = 1;
    if (s->mapup_active) {
        int rc = s->fs.mapup_open(s->fs.ctx, s->mapup_name, s->mapup_size, s->mapup_got);
        if (rc != HH_SER_OK) { mapup_release(s); return rc; }       /* the temporary file is gone: start the file again */
    }
    return HH_SER_OK;
}

static void cmd_mapup(hh_serial_t *s, uint8_t type, uint8_t seq, const uint8_t *p, size_t len)
{
    if (!s->fs.mapup_open || !s->fs.mapup_write || !s->fs.mapup_commit || !(s->caps & HH_SER_CAP_MAPUP)) {
        ack(s, type, seq, HH_SER_E_UNSUPPORTED, 0);
        return;
    }
    s->mapup_t = now(s);
    if (type == HH_SC_MAPUP_OPEN) {
        hh_ser_mapup_open_t o;
        int rc;
        if (len < sizeof(o)) { ack(s, type, seq, HH_SER_E_ARG, 0); return; }
        memcpy(&o, p, sizeof(o));
        const char *name = (const char *)p + sizeof(o);
        if (len != sizeof(o) + o.name_len || !mapup_name_ok(name, o.name_len)
            || o.size == 0 || o.size > HH_SER_MAPUP_SIZE_MAX) { ack(s, type, seq, HH_SER_E_REFUSED, 0); return; }
        if (s->mapup_active && s->mapup_size == o.size && s->mapup_crc == o.crc32
            && strlen(s->mapup_name) == o.name_len && memcmp(s->mapup_name, name, o.name_len) == 0) {
            rc = mapup_take(s);                             /* the same file again: carry on where it stopped */
            ack(s, type, seq, rc, rc == HH_SER_OK ? s->mapup_got : 0);
            return;
        }
        if (s->mapup_active) { if (s->fs.mapup_abort) s->fs.mapup_abort(s->fs.ctx); s->mapup_active = 0; }
        if (!s->mapup_held && (rc = mapup_take(s)) != HH_SER_OK) { ack(s, type, seq, rc, 0); return; }
        rc = s->fs.mapup_open(s->fs.ctx, name, o.size, 0);
        if (rc != HH_SER_OK) { ack(s, type, seq, rc, 0); return; }
        memcpy(s->mapup_name, name, o.name_len);
        s->mapup_name[o.name_len] = 0;
        s->mapup_size = o.size; s->mapup_crc = o.crc32; s->mapup_got = 0; s->mapup_active = 1;
        ack(s, type, seq, HH_SER_OK, 0);
    } else if (type == HH_SC_MAPUP_WRITE) {
        hh_ser_mapup_write_t w;
        if (len < sizeof(w) + 1) { ack(s, type, seq, HH_SER_E_ARG, 0); return; }
        if (!s->mapup_active) { ack(s, type, seq, HH_SER_E_STATE, 0); return; }
        memcpy(&w, p, sizeof(w));
        const uint8_t *data = p + sizeof(w);
        uint32_t n = (uint32_t)(len - sizeof(w));
        if (hh_crc32(0, data, n) != w.crc32) { ack(s, type, seq, HH_SER_E_CRC, s->mapup_got); return; }
        if ((uint64_t)w.offset + n <= s->mapup_got) { ack(s, type, seq, HH_SER_OK, s->mapup_got); return; }   /* sent twice */
        if (w.offset != s->mapup_got) { ack(s, type, seq, HH_SER_E_OFFSET, s->mapup_got); return; }
        if ((uint64_t)w.offset + n > s->mapup_size) { ack(s, type, seq, HH_SER_E_ARG, s->mapup_got); return; }
        int rc = mapup_take(s);
        if (rc != HH_SER_OK) { ack(s, type, seq, rc == HH_SER_E_BUSY ? rc : HH_SER_E_STATE, 0); return; }
        rc = s->fs.mapup_write(s->fs.ctx, w.offset, data, (uint16_t)n);
        if (rc != HH_SER_OK) {
            /* the port closed the temp file but kept it: stay active and let the next chunk reopen it at
               mapup_got (mapup_take), so the sender just retries this chunk; ack says how much is held */
            s->mapup_held = 0;
            if (s->map_hold) s->map_hold(0);
            ack(s, type, seq, rc, s->mapup_got);
            return;
        }
        s->mapup_got += n;
        ack(s, type, seq, HH_SER_OK, s->mapup_got);
    } else if (type == HH_SC_MAPUP_STATUS) {
        hh_ser_mapup_info_t i;
        memset(&i, 0, sizeof(i));
        if (s->mapup_active) {
            i.state = s->mapup_held ? HH_SER_MU_OPEN : HH_SER_MU_PAUSED;
            i.got = s->mapup_got; i.size = s->mapup_size; i.crc32 = s->mapup_crc;
            memcpy(i.name, s->mapup_name, sizeof(i.name));
        }
        hh_serial_push(s, HH_SF_MAPUP, &i, sizeof(i), NULL, 0);
        ack(s, type, seq, s->mapup_active ? HH_SER_OK : HH_SER_E_STATE, s->mapup_active ? s->mapup_got : 0);
    } else {
        hh_ser_mapup_commit_t c;
        memset(&c, 0, sizeof(c));
        memcpy(&c, p, len < sizeof(c) ? len : sizeof(c));
        if (c.flags & HH_SER_MAPUP_ABORT) { mapup_release(s); ack(s, type, seq, HH_SER_OK, 0); return; }
        if (!s->mapup_active) { ack(s, type, seq, HH_SER_E_STATE, 0); return; }
        if (s->mapup_got != s->mapup_size) { ack(s, type, seq, HH_SER_E_STATE, s->mapup_got); return; }
        int rc = mapup_take(s);             /* the rename must not meet an open file: the reader is held off first */
        if (rc != HH_SER_OK) { ack(s, type, seq, rc == HH_SER_E_BUSY ? rc : HH_SER_E_STATE, 0); return; }
        rc = s->fs.mapup_commit(s->fs.ctx, s->mapup_name, s->mapup_size, s->mapup_crc);
        s->mapup_active = 0;                /* done, or removed: either way there is nothing to resume */
        if (c.flags & HH_SER_MAPUP_FINAL) mapup_release(s);
        s->mapup_t = now(s);
        ack(s, type, seq, rc, rc == HH_SER_OK ? s->mapup_size : 0);
    }
}

void hh_serial_tick(hh_serial_t *s)
{
    if (s->mapup_held && now(s) - s->mapup_t > HH_SER_MAPUP_IDLE_S) mapup_pause(s);
}

static void dispatch(hh_serial_t *s, uint8_t type, uint8_t seq, const uint8_t *p, size_t len)
{
    hh_core_t *c = s->core;
    switch (type) {
    case HH_SC_GET_HELLO:
        push_hello(s);
        break;
    case HH_SC_TIME_REQ: {
        hh_ser_time_rsp_t r;
        if (len < 8) break;
        memcpy(&r.host_tx_us, p, 8);
        r.dev_rx_us = us(now(s));
        r.dev_tx_us = us(now(s));
        hh_serial_push(s, HH_SF_TIME_RSP, &r, sizeof(r), NULL, 0);
        break;
    }
    case HH_SC_TIME_SET: {
        int64_t unix_us;
        if (len < 8 || !c) { ack(s, type, seq, len < 8 ? HH_SER_E_ARG : HH_SER_E_STATE, 0); break; }
        memcpy(&unix_us, p, 8);
        ack(s, type, seq, unix_us > 0 ? hh_core_set_time(c, unix_us) : HH_SER_E_ARG, 0);
        break;
    }
    case HH_SC_MARK:
        if (c) hh_core_mark(c);
        ack(s, type, seq, c ? HH_SER_OK : HH_SER_E_STATE, 0);
        break;
    case HH_SC_ACK_ALARM:
        if (c) hh_core_ack(c);
        ack(s, type, seq, c ? HH_SER_OK : HH_SER_E_STATE, 0);
        break;
    case HH_SC_DRIVE:
        if (len < 1 || !c) { ack(s, type, seq, len < 1 ? HH_SER_E_ARG : HH_SER_E_STATE, 0); break; }
        hh_core_drive(c, p[0] != 0);
        ack(s, type, seq, HH_SER_OK, p[0] != 0);
        break;
    case HH_SC_DTC:
        /* the engine's own Mode 03 read, behind its guard (hh_iso_allowed): the same one the button asks for */
        if (c) hh_core_request_dtc(c);
        ack(s, type, seq, c ? HH_SER_OK : HH_SER_E_STATE, 0);
        break;
    case HH_SC_LIST_FILES: cmd_list(s, seq, p, len); break;
    case HH_SC_READ_FILE: cmd_read(s, seq, p, len); break;
    case HH_SC_PUT_BEGIN: case HH_SC_PUT_DATA: case HH_SC_PUT_END: cmd_put(s, type, seq, p, len); break;
    case HH_SC_MAPUP_OPEN: case HH_SC_MAPUP_WRITE: case HH_SC_MAPUP_STATUS: case HH_SC_MAPUP_COMMIT:
        cmd_mapup(s, type, seq, p, len);
        break;
    default:
        ack(s, type, seq, HH_SER_E_UNSUPPORTED, 0);
        break;
    }
}

void hh_serial_rx(hh_serial_t *s, const uint8_t *data, size_t len)
{
    if (!(s->caps & HH_SER_CAP_RX)) return;
    for (size_t i = 0; i < len; i++) {
        uint8_t b = data[i];
        if (b != 0) {
            if (s->rx_skip) continue;
            if (s->rx_n >= sizeof(s->rx)) { s->rx_skip = 1; s->rx_n = 0; s->rx_bad++; continue; }
            s->rx[s->rx_n++] = b;
            continue;
        }
        uint16_t n = s->rx_n;
        s->rx_n = 0;
        if (s->rx_skip) { s->rx_skip = 0; continue; }
        if (!n) continue;
        uint8_t raw[HH_SER_FRAME_MAX];
        int m = hh_cobs_decode(s->rx, n, raw);
        if (m < 5 || m > HH_SER_RAW_MAX || raw[0] != HH_SER_PROTO
            || hh_crc16(raw, (size_t)m - 2) != (uint16_t)(raw[m - 2] | (raw[m - 1] << 8))) {
            s->rx_bad++;
            continue;
        }
        s->rx_frames++;
        s->last_rx = now(s);
        dispatch(s, raw[1], raw[2], raw + 3, (size_t)m - 5);
    }
}

#endif /* !HH_SERIAL_TX_ONLY */
