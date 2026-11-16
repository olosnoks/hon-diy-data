/* Host tests of the laptop link (components/hhcore/hh_serial.c, port/hh_serfs.c):
 * framing, the ring and its drop policy, the stream the engine produces, and
 * every command, against the simulated ECU. `make` runs it after hhtest.
 * tests/test_hh_serial.py reads the same stream with the laptop's decoder. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "hhhost.h"
#include "hhsim.h"

static int failures, checks;
static const char *current = "";

#define CHECK(cond) do { checks++; if (!(cond)) { failures++; \
    printf("  FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, current, #cond); } } while (0)
#define RUN(name) do { current = #name; int before = failures; name(); \
    printf("%-34s %s\n", #name, failures == before ? "ok" : "FAILED"); } while (0)
#define TEST(name) static void name(void)

static char TMP[200];
static int tmp_n;
static const char *fresh_dir(void)
{
    static char path[256];
    snprintf(path, sizeof(path), "%s/sd%d", TMP, tmp_n++);
    mkdir(path, 0777);
    return path;
}

/* ---- the laptop's side, in miniature ----------------------------------------- */

typedef struct { uint8_t type, seq; uint16_t len; uint8_t p[HH_SER_MAX_PAYLOAD]; } frame_t;

static struct {
    uint8_t buf[HH_SER_FRAME_MAX + 8];
    size_t n;
    uint32_t count[256], bad, total, seq_gaps;
    int have_seq; uint8_t last_seq;
    frame_t last[256 - 0x40];           /* the newest frame of each type from 0x40 up */
    frame_t keep[4096]; int n_keep;     /* every frame of the type in keep_type, in order */
    uint8_t keep_type;
} rx;

static void rx_reset(void) { memset(&rx, 0, sizeof(rx)); }

static void rx_frame(const uint8_t *raw, int m)
{
    uint8_t type = raw[1], seq = raw[2];
    rx.total++;
    rx.count[type]++;
    if (rx.have_seq && (uint8_t)(rx.last_seq + 1) != seq) rx.seq_gaps++;
    rx.have_seq = 1; rx.last_seq = seq;
    frame_t f;
    f.type = type; f.seq = seq; f.len = (uint16_t)(m - 5);
    memcpy(f.p, raw + 3, f.len);
    if (type >= 0x40) rx.last[type - 0x40] = f;
    if (type == rx.keep_type && rx.n_keep < 4096) rx.keep[rx.n_keep++] = f;
}

static void rx_bytes(const uint8_t *d, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        if (d[i]) { if (rx.n < sizeof(rx.buf)) rx.buf[rx.n++] = d[i]; else rx.bad++; continue; }
        uint8_t raw[HH_SER_FRAME_MAX];
        int m = hh_cobs_decode(rx.buf, rx.n, raw);
        rx.n = 0;
        if (m < 5 || raw[0] != HH_SER_PROTO || hh_crc16(raw, (size_t)m - 2) != (uint16_t)(raw[m - 2] | (raw[m - 1] << 8))) {
            rx.bad++;
            continue;
        }
        rx_frame(raw, m);
    }
}

static const frame_t *last(uint8_t type) { return &rx.last[type - 0x40]; }

/* everything the UART would send now */
static size_t drain(hhhost_t *h)
{
    uint8_t out[2048];
    size_t n, total = 0;
    while ((n = hhhost_serial_poll(h, out, sizeof(out))) != 0) { rx_bytes(out, n); total += n; }
    return total;
}

static void run(hhhost_t *h, double until)
{
    while (SIM.now < until) { hhhost_run(h, 1); drain(h); }
}

static uint8_t cmd_seq;
static void send(hhhost_t *h, uint8_t type, const void *payload, size_t len)
{
    uint8_t out[HH_SER_FRAME_MAX];
    size_t n = hh_ser_frame(type, ++cmd_seq, payload, len, out);
    hhhost_serial_rx(h, out, n);
    drain(h);
}

/* the ACK of the command just sent: its status, or -100 if none came */
static int acked(uint8_t type, uint32_t *value)
{
    hh_ser_ack_t a;
    const frame_t *f = last(HH_SF_ACK);
    if (f->len != sizeof(a)) return -100;
    memcpy(&a, f->p, sizeof(a));
    if (a.cmd != type || a.seq != cmd_seq) return -100;
    if (value) *value = a.value;
    return a.status;
}

/* ---- framing -------------------------------------------------------------------- */

TEST(test_framing)
{
    uint8_t in[700], enc[720], dec[720], out[HH_SER_FRAME_MAX];
    CHECK(hh_crc16((const uint8_t *)"123456789", 9) == 0x29B1);
    CHECK(sizeof(hh_ser_hello_t) == 96 && sizeof(hh_ser_stats_t) == 96 && sizeof(hh_ser_ack_t) == 8);
    CHECK(sizeof(hh_tap_read_t) == 12 && sizeof(hh_tap_tick_t) == 16 && sizeof(hh_tap_link_t) == 12 && sizeof(hh_tap_iso_t) == 12);
    CHECK(sizeof(hh_ser_time_rsp_t) == 24 && sizeof(hh_ser_file_info_t) == 12 && sizeof(hh_ser_file_data_t) == 16);
    CHECK(sizeof(hh_ser_list_t) == 8 && sizeof(hh_ser_read_file_t) == 12 && sizeof(hh_ser_put_begin_t) == 12);

    /* COBS: the classic vectors */
    static const uint8_t v1[] = {0x00}, e1[] = {0x01, 0x01};
    static const uint8_t v2[] = {0x11, 0x22, 0x00, 0x33}, e2[] = {0x03, 0x11, 0x22, 0x02, 0x33};
    CHECK(hh_cobs_encode(v1, 1, enc) == 2 && memcmp(enc, e1, 2) == 0);
    CHECK(hh_cobs_encode(v2, 4, enc) == 5 && memcmp(enc, e2, 5) == 0);
    CHECK(hh_cobs_encode(v1, 0, enc) == 1 && enc[0] == 0x01);
    /* every length around the 254-byte block edge, with and without zeros; no zero ever comes out */
    uint32_t r = 99;
    for (int kind = 0; kind < 3; kind++)
        for (size_t len = 0; len <= 600; len += (len < 20 || (len > 250 && len < 260) || (len > 505 && len < 512)) ? 1 : 37) {
            for (size_t i = 0; i < len; i++) {
                r = r * 1664525u + 1013904223u;
                in[i] = kind == 0 ? 0 : kind == 1 ? (uint8_t)(1 + (r >> 9) % 255) : (uint8_t)(r >> 11);
            }
            size_t n = hh_cobs_encode(in, len, enc);
            int zero = 0;
            for (size_t i = 0; i < n; i++) zero |= enc[i] == 0;
            CHECK(!zero && n <= len + len / 254 + 1);
            int m = hh_cobs_decode(enc, n, dec);
            CHECK(m == (int)len && memcmp(in, dec, len) == 0);
        }
    /* malformed input is refused, not read past its end */
    static const uint8_t bad1[] = {0x05, 0x11}, bad2[] = {0x02, 0x11, 0x00, 0x01};
    CHECK(hh_cobs_decode(bad1, 2, dec) == -1 && hh_cobs_decode(bad2, 4, dec) == -1);

    /* a whole frame: one 0x00, at the end; any damaged byte fails the CRC or the COBS */
    for (size_t len = 0; len <= HH_SER_MAX_PAYLOAD; len += 64) {
        for (size_t i = 0; i < len; i++) in[i] = (uint8_t)(i * 31);
        size_t n = hh_ser_frame(HH_SF_READ, 7, in, len, out);
        CHECK(n > 0 && n <= HH_SER_FRAME_MAX && out[n - 1] == 0 && memchr(out, 0, n - 1) == NULL);
        rx_reset();
        rx_bytes(out, n);
        CHECK(rx.total == 1 && rx.bad == 0 && last(HH_SF_READ)->len == len && last(HH_SF_READ)->seq == 7
              && memcmp(last(HH_SF_READ)->p, in, len) == 0);
        out[n / 2] ^= 0x10;
        rx_reset();
        rx_bytes(out, n);
        CHECK(rx.total == 0 && rx.bad >= 1);
    }
    CHECK(hh_ser_frame(HH_SF_READ, 0, in, HH_SER_MAX_PAYLOAD + 1, out) == 0);
}

/* ---- ring and drop policy --------------------------------------------------------- */

static double fake_now(void *ctx) { (void)ctx; return 1000.0; }     /* nothing periodic falls due */
static int wakes;
static void count_wake(void *ctx) { (void)ctx; wakes++; }
static void fake_sleep(void *ctx, double s) { (void)ctx; (void)s; }

TEST(test_ring_drop_oldest)
{
    static uint8_t mem[1024];
    uint8_t out[4096], body[40];
    hh_serial_t s;
    hh_clock_t clk = {NULL, fake_now, fake_sleep};
    hh_serial_init(&s, &clk, mem, sizeof(mem), HH_SER_VARIANT_GPS_P1, 0, 921600);
    s.next_hello = s.next_stats = 1e12;
    s.wake = count_wake;
    wakes = 0;
    CHECK(s.caps == 0);

    /* 100 frames of 40 bytes into a ring that holds 23: the newest 23 survive, in order */
    for (int i = 0; i < 100; i++) {
        memset(body, i, sizeof(body));
        CHECK(hh_serial_push(&s, HH_SF_READ, body, sizeof(body), NULL, 0) == 0);
    }
    CHECK(wakes == 100);                /* the TX task is told about every frame */
    int held = (int)(sizeof(mem) / (sizeof(body) + 4));
    CHECK(s.tx_dropped == 100u - (uint32_t)held && s.ring.used <= sizeof(mem) && s.ring_peak <= sizeof(mem));
    rx_reset();
    rx.keep_type = HH_SF_READ;
    size_t n;
    while ((n = hh_serial_poll(&s, out, sizeof(out))) != 0) {
        CHECK(n <= sizeof(out) && out[n - 1] == 0);        /* whole frames only */
        rx_bytes(out, n);
    }
    CHECK(rx.n_keep == held && rx.bad == 0 && s.tx_frames == (uint32_t)held);
    for (int i = 0; i < rx.n_keep; i++) {
        int want = 100 - held + i;
        CHECK(rx.keep[i].seq == (uint8_t)want && rx.keep[i].len == sizeof(body) && rx.keep[i].p[0] == (uint8_t)want);
    }
    CHECK(rx.seq_gaps == 0);                                /* a gap only before the first survivor */

    /* a drained ring takes new frames with no loss; the counter stays */
    uint32_t dropped = s.tx_dropped;
    for (int i = 0; i < 10; i++) hh_serial_push(&s, HH_SF_TICK, body, 16, NULL, 0);
    rx_reset();
    while ((n = hh_serial_poll(&s, out, sizeof(out))) != 0) rx_bytes(out, n);
    CHECK(rx.count[HH_SF_TICK] == 10 && s.tx_dropped == dropped && s.ring.used == 0);

    /* head + data are joined; what can never fit is refused, not half written */
    static uint8_t big[HH_SER_MAX_PAYLOAD + 1];
    CHECK(hh_serial_push(&s, HH_SF_LOG, big, sizeof(big), NULL, 0) == -1);
    CHECK(hh_serial_push(&s, HH_SF_LOG, big, 500, big, 13) == -1);
    CHECK(hh_serial_push(&s, HH_SF_LOG, "ab", 2, "cd", 2) == 0);
    n = hh_serial_poll(&s, out, sizeof(out));
    rx_reset(); rx_bytes(out, n);
    CHECK(last(HH_SF_LOG)->len == 4 && memcmp(last(HH_SF_LOG)->p, "abcd", 4) == 0);

    /* poll never writes past the buffer it is given, and gives nothing when a frame might not fit */
    for (int i = 0; i < 12; i++) hh_serial_push(&s, HH_SF_READ, body, sizeof(body), NULL, 0);
    CHECK(hh_serial_poll(&s, out, HH_SER_FRAME_MAX - 1) == 0);
    n = hh_serial_poll(&s, out, HH_SER_FRAME_MAX);
    CHECK(n > 0 && n <= HH_SER_FRAME_MAX);

    /* a long log line is cut into frames */
    char text[600];
    memset(text, 'x', sizeof(text));
    hh_ring_clear(&s.ring);
    hh_serial_log(&s, text, sizeof(text));
    rx_reset();
    while ((n = hh_serial_poll(&s, out, sizeof(out))) != 0) rx_bytes(out, n);
    CHECK(rx.count[HH_SF_LOG] == 3);

    /* no ring at all (no memory): nothing happens, nothing crashes */
    hh_serial_init(&s, &clk, NULL, 0, 0, HH_SER_CAP_RX, 921600);
    s.next_hello = s.next_stats = 1e12;
    CHECK(hh_serial_push(&s, HH_SF_READ, body, 8, NULL, 0) == -1 && hh_serial_poll(&s, out, sizeof(out)) == 0);
}

/* ---- the stream -------------------------------------------------------------------- */

TEST(test_stream)
{
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, "prealloc_mb = 1\n", hhsim_callbacks(), 3, 0);
    hh_serial_t *s = hhhost_serial(h, 0, 0);
    rx_reset();
    rx.keep_type = HH_SF_READ;
    size_t bytes = 0;
    while (SIM.now < 10.0) { hhhost_run(h, 1); bytes += drain(h); }

    /* hello: who it is, every 2 s */
    hh_ser_hello_t hello;
    CHECK(rx.count[HH_SF_HELLO] >= 5 && rx.count[HH_SF_HELLO] <= 6 && last(HH_SF_HELLO)->len == sizeof(hello));
    memcpy(&hello, last(HH_SF_HELLO)->p, sizeof(hello));
    hh_core_info_t info;
    hh_core_info(hhhost_core(h), &info);
    CHECK(memcmp(hello.magic, "HHSL", 4) == 0 && hello.rev == HH_SER_REV && hello.variant == HH_SER_VARIANT_DEFAULT);
    CHECK((hello.caps & HH_SER_CAP_RX) && (hello.caps & HH_SER_CAP_FILES) && hello.baud == 921600);
    CHECK(strcmp(hello.fw, HH_FW_VERSION) == 0 && strcmp(hello.device, "HOSTTEST") == 0 && hello.boot_no == 3);
    CHECK(memcmp(hello.map_hash, info.map_hash, 12) == 0 && hello.map_hash[0] != 0);
    CHECK(hello.ecu_id_len == 10 && memcmp(hello.ecu_id, "37820-P2P-", 10) == 0 && hello.link_state == HH_LINK_OK);
    CHECK((hello.flags & 1) && hello.t_us > 7000000 && hello.max_payload == HH_SER_MAX_PAYLOAD);

    /* every read the engine made, with the ECU's bytes */
    hh_snapshot_t snap;
    hhhost_snapshot(h, &snap);
    CHECK(rx.n_keep > 400 && rx.bad == 0 && rx.seq_gaps == 0 && s->tx_dropped == 0);
    CHECK((uint32_t)rx.n_keep <= snap.link.reads && snap.link.reads - (uint32_t)rx.n_keep <= 4);    /* the ECU id and probes are not block reads */
    uint64_t prev = 0;
    int ordered = 1, right = 1, planned = 1;
    const hh_plan_t *plan = hh_core_plan(hhhost_core(h));
    for (int i = 0; i < rx.n_keep; i++) {
        hh_tap_read_t r;
        memcpy(&r, rx.keep[i].p, sizeof(r));
        right &= rx.keep[i].len == sizeof(r) + r.len && r.latency_us > 0 && r.latency_us < 30000;
        for (int k = 0; k < r.len; k++) right &= rx.keep[i].p[sizeof(r) + k] == SIM.mem[(r.addr + k) & 0xFF];
        int found = 0;
        for (int b = 0; b < plan->n_blocks; b++) found |= plan->blocks[b].addr == r.addr && plan->blocks[b].length == r.len;
        planned &= found;
        ordered &= r.t_us >= prev;
        prev = r.t_us;
    }
    CHECK(ordered && right && planned);

    /* a tick per fast cycle; link, stats, the drive's start */
    hh_tap_tick_t tick;
    memcpy(&tick, last(HH_SF_TICK)->p, sizeof(tick));
    CHECK(rx.count[HH_SF_TICK] > 150 && rx.count[HH_SF_TICK] < 210 && tick.hz_c > 1700 && tick.hz_c < 2100);
    hh_tap_link_t link;
    memcpy(&link, last(HH_SF_LINK)->p, sizeof(link));
    CHECK(rx.count[HH_SF_LINK] >= 1 && link.state == HH_LINK_OK && link.up == 1 && link.backend == HH_BACKEND_USB);
    hh_ser_stats_t st;
    memcpy(&st, last(HH_SF_STATS)->p, sizeof(st));
    CHECK(rx.count[HH_SF_STATS] >= 9 && last(HH_SF_STATS)->len == sizeof(st) && st.link_state == HH_LINK_OK);
    CHECK(st.reads > 400 && st.errors == 0 && (st.flags & HH_SER_ST_SD_OK) && (st.flags & HH_SER_ST_RECORDING)
          && (st.flags & HH_SER_ST_ARMED) && !(st.flags & HH_SER_ST_HOST) && st.drive_no == 1 && st.tx_dropped == 0);
    CHECK(rx.count[HH_SF_EVENT] >= 2);          /* drive start, link up */

    /* the budget: this is what 921600 baud has to carry */
    double per_s = (double)bytes / 10.0;
    printf("    stream: %.0f bytes/s, %.1f frames/s, %.1f %% of 921600 baud, ring peak %u bytes\n", per_s,
           rx.total / 10.0, 100.0 * per_s * 10 / 921600, (unsigned)s->ring_peak);
    CHECK(per_s > 1000 && per_s < 4000 && s->ring_peak < 2048);

    /* GPS fixes and markers as they happen */
    hhhost_gps(h, "$GPGGA,123519,3217.100,N,09007.200,W,1,08,0.9,95.4,M,-29.0,M,,*48\r\n");
    hhhost_gps(h, "$GPRMC,123519,A,3217.100,N,09007.200,W,022.4,084.4,151126,003.1,W*7F\r\n");
    hh_core_mark(hhhost_core(h));
    drain(h);
    hh_gps_t fix;
    hh_rec_event_t ev;
    memcpy(&fix, last(HH_SF_GPS)->p, sizeof(fix));
    memcpy(&ev, last(HH_SF_EVENT)->p, sizeof(ev));
    CHECK(rx.count[HH_SF_GPS] == 1 && last(HH_SF_GPS)->len == sizeof(fix) && fix.fix && fix.sats == 8 && fix.lat_e7 == 322850000);
    CHECK(last(HH_SF_EVENT)->len == sizeof(ev) && ev.code == HH_EV_MARKER && ev.v.i == 1);

    /* the engine stalls: the trigger is on the wire at once; then the ECU goes silent */
    rx.keep_type = HH_SF_EVENT; rx.n_keep = 0;
    hhsim_set_rpm(0);
    run(h, 11.0);
    int stall = 0;
    for (int i = 0; i < rx.n_keep; i++) { memcpy(&ev, rx.keep[i].p, sizeof(ev)); stall += ev.code == HH_EV_STALL && ev.src == HH_STALL_RPM; }
    CHECK(stall == 1);
    SIM.powered = 0;
    uint32_t links = rx.count[HH_SF_LINK];
    run(h, 14.0);
    memcpy(&link, last(HH_SF_LINK)->p, sizeof(link));
    CHECK(link.up == 0 && link.state == HH_LINK_OFF && rx.count[HH_SF_LINK] > links + 5);  /* repeated while down: time passes for the laptop */
    int down = 0;
    for (int i = 0; i < rx.n_keep; i++) { memcpy(&ev, rx.keep[i].p, sizeof(ev)); down += ev.code == HH_EV_LINK_DOWN; }
    CHECK(down == 1);
    CHECK(rx.bad == 0 && rx.seq_gaps == 0 && SIM.bad_frames_sent == 0);
    hhhost_free(h);
}

/* Nobody listening and nobody draining: the engine and the card never notice. */
TEST(test_stream_not_drained)
{
    hh_snapshot_t snap;
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, "prealloc_mb = 1\n", hhsim_callbacks(), 1, 0);
    hh_serial_t *s = hhhost_serial(h, 1, 4096);
    hhhost_run_until(h, 20.0);
    hhhost_snapshot(h, &snap);
    CHECK(snap.link.state == HH_LINK_OK && snap.link.reads > 1000 && snap.link.errors == 0 && snap.log.recording);
    CHECK(snap.link.hz_fast > 17.0f);
    CHECK(s->tx_dropped > 500 && s->ring.used <= 4096 && s->tx_frames == 0);
    /* what is left is the newest: whole frames, in order, up to now */
    rx_reset();
    rx.keep_type = HH_SF_READ;
    drain(h);
    hh_tap_read_t r;
    CHECK(rx.n_keep > 20 && rx.bad == 0 && rx.seq_gaps == 0);
    memcpy(&r, rx.keep[rx.n_keep - 1].p, sizeof(r));
    CHECK(r.t_us > 19900000);
    memcpy(&r, rx.keep[0].p, sizeof(r));
    CHECK(r.t_us > 17000000);
    /* the laptop is told how much it missed */
    hhhost_run_until(h, 21.5);
    drain(h);
    hh_ser_stats_t st;
    memcpy(&st, last(HH_SF_STATS)->p, sizeof(st));
    CHECK(st.tx_dropped == s->tx_dropped && st.tx_dropped > 500 && st.ring_peak <= 4096);
    hhhost_free(h);
}

/* ---- tx-only (GPS on P1) ------------------------------------------------------------ */

TEST(test_tx_only)
{
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hh_serial_t *s = hhhost_serial(h, 1, 0);
    rx_reset();
    run(h, 5.0);
    hh_ser_hello_t hello;
    memcpy(&hello, last(HH_SF_HELLO)->p, sizeof(hello));
    CHECK(hello.variant == HH_SER_VARIANT_GPS_P1 && !(hello.caps & (HH_SER_CAP_RX | HH_SER_CAP_FILES | HH_SER_CAP_PUT)));
    CHECK(rx.count[HH_SF_READ] > 200 && rx.count[HH_SF_TICK] > 70);
    /* whatever arrives on the pin (it is the GPS's line) is not a command */
    hh_snapshot_t before, after;
    hhhost_snapshot(h, &before);
    send(h, HH_SC_MARK, NULL, 0);
    send(h, HH_SC_DRIVE, "\0", 1);
    send(h, HH_SC_GET_HELLO, NULL, 0);
    hhhost_snapshot(h, &after);
    CHECK(s->rx_frames == 0 && rx.count[HH_SF_ACK] == 0 && after.markers == before.markers && after.log.recording);
    CHECK(!hh_serial_host_attached(s));
    hhhost_free(h);
}

/* ---- commands ------------------------------------------------------------------------- */

static long file_size(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 ? (long)st.st_size : -1;
}

static uint8_t *slurp(const char *path, long *size)
{
    FILE *f = fopen(path, "rb");
    if (!f) { *size = -1; return NULL; }
    fseek(f, 0, SEEK_END);
    *size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *d = malloc((size_t)*size + 1);
    if (fread(d, 1, (size_t)*size, f) != (size_t)*size) *size = -1;
    fclose(f);
    return d;
}

/* READ_FILE until the end, as the laptop does; from `start` (a resumed transfer). Returns the length or -1. */
static long fetch(hhhost_t *h, uint8_t kind, uint32_t no, uint8_t *into, long start, int *busy)
{
    long off = start;
    if (busy) *busy = 0;
    for (;;) {
        hh_ser_read_file_t req = {kind, 0, HH_SER_CHUNK, no, (uint32_t)off};
        hh_ser_file_data_t d;
        uint32_t before = rx.count[HH_SF_FILE_DATA];
        send(h, HH_SC_READ_FILE, &req, sizeof(req));
        if (rx.count[HH_SF_FILE_DATA] != before + 1) return -1;
        const frame_t *f = last(HH_SF_FILE_DATA);
        memcpy(&d, f->p, sizeof(d));
        if (d.offset == 0xFFFFFFFFu) { if (busy) *busy = (d.flags & HH_SER_FD_BUSY) != 0; return -1; }
        if (d.kind != kind || d.file_no != no || d.offset != (uint32_t)off || f->len != sizeof(d) + d.len) return -1;
        if (hh_crc32(0, f->p + sizeof(d), d.len) != d.crc32) return -1;
        memcpy(into + off, f->p + sizeof(d), d.len);
        off += d.len;
        if (d.flags & HH_SER_FD_EOF) return off;
        if (d.len == 0) return -1;
    }
}

static int put(hhhost_t *h, uint8_t kind, const uint8_t *data, uint32_t size, uint32_t crc, uint32_t stop_after, uint32_t *value)
{
    hh_ser_put_begin_t b = {kind, {0, 0, 0}, size, crc};
    uint32_t off = 0;
    send(h, HH_SC_PUT_BEGIN, &b, sizeof(b));
    int rc = acked(HH_SC_PUT_BEGIN, &off);
    if (rc != HH_SER_OK) return rc;
    while (off < size) {
        uint8_t msg[4 + 400];
        uint32_t n = size - off < 400 ? size - off : 400;
        if (off >= stop_after) return 99;       /* the cable was pulled here */
        memcpy(msg, &off, 4);
        memcpy(msg + 4, data + off, n);
        send(h, HH_SC_PUT_DATA, msg, 4 + n);
        rc = acked(HH_SC_PUT_DATA, &off);
        if (rc != HH_SER_OK) return rc;
    }
    send(h, HH_SC_PUT_END, NULL, 0);
    return acked(HH_SC_PUT_END, value);
}

TEST(test_commands)
{
    char path[400];
    const char *root = fresh_dir();
    hh_snapshot_t snap;
    uint32_t value = 0;
    hhsim_reset();
    SIM.iso = 1;
    SIM.dtc[0] = 0x0135;
    hhhost_t *h = hhhost_new(root, NULL, 0, "prealloc_mb = 1\n", hhsim_callbacks(), 1, 0);
    hh_serial_t *s = hhhost_serial(h, 0, 0);
    rx_reset();
    run(h, 3.0);
    CHECK(!hh_serial_host_attached(s));

    /* hello on request; the laptop is "attached" while it keeps talking */
    uint32_t hellos = rx.count[HH_SF_HELLO];
    send(h, HH_SC_GET_HELLO, NULL, 0);
    CHECK(rx.count[HH_SF_HELLO] == hellos + 1 && hh_serial_host_attached(s) && s->rx_frames == 1);

    /* clock exchange */
    int64_t host_tx = 123456789012LL;
    send(h, HH_SC_TIME_REQ, &host_tx, 8);
    hh_ser_time_rsp_t tr;
    memcpy(&tr, last(HH_SF_TIME_RSP)->p, sizeof(tr));
    CHECK(rx.count[HH_SF_TIME_RSP] == 1 && tr.host_tx_us == host_tx && tr.dev_rx_us > 2900000 && tr.dev_tx_us >= tr.dev_rx_us);

    /* marker, acknowledge */
    send(h, HH_SC_MARK, NULL, 0);
    hhhost_snapshot(h, &snap);
    CHECK(acked(HH_SC_MARK, NULL) == HH_SER_OK && snap.markers == 1);
    send(h, HH_SC_ACK_ALARM, NULL, 0);
    CHECK(acked(HH_SC_ACK_ALARM, NULL) == HH_SER_OK);

    /* the clock: taken while there is none, refused once the GPS has set it */
    int64_t unix_us = 1794700000000000LL;
    send(h, HH_SC_TIME_SET, &unix_us, 8);
    CHECK(acked(HH_SC_TIME_SET, NULL) == HH_SER_OK && hhhost_time_set(h) == unix_us);
    hhhost_gps(h, "$GPRMC,123519,A,3217.100,N,09007.200,W,022.4,084.4,151126,003.1,W*7F\r\n");
    CHECK(hhhost_time_set(h) == 1794746119000000LL);
    send(h, HH_SC_TIME_SET, &unix_us, 8);
    CHECK(acked(HH_SC_TIME_SET, NULL) == HH_SER_IGNORED && hhhost_time_set(h) == 1794746119000000LL);

    /* the open drive is listed, flagged, and not handed out */
    static uint8_t got[2 << 20];
    hh_ser_list_t lreq = {HH_SER_F_DRIVE, {0, 0, 0}, 0};
    rx.keep_type = HH_SF_FILE_INFO; rx.n_keep = 0;
    send(h, HH_SC_LIST_FILES, &lreq, sizeof(lreq));
    hh_ser_file_info_t fi;
    CHECK(rx.n_keep == 2);
    memcpy(&fi, rx.keep[0].p, sizeof(fi));
    CHECK(fi.kind == HH_SER_F_DRIVE && fi.file_no == 1 && (fi.flags & HH_SER_FI_OPEN));
    memcpy(&fi, rx.keep[1].p, sizeof(fi));
    CHECK(fi.file_no == 0xFFFFFFFFu && fi.size == 1 && !(fi.flags & HH_SER_FI_MORE));
    int busy = 0;
    CHECK(fetch(h, HH_SER_F_DRIVE, 1, got, 0, &busy) == -1 && busy);

    /* stop the drive: the file is closed and can be fetched, byte for byte */
    send(h, HH_SC_DRIVE, "\0", 1);
    hhhost_snapshot(h, &snap);
    CHECK(acked(HH_SC_DRIVE, NULL) == HH_SER_OK && !snap.log.recording);
    run(h, 5.0);
    hhhost_snapshot(h, &snap);
    CHECK(!snap.log.recording && snap.link.state == HH_LINK_OK);       /* held: the ECU answers, nothing is recorded */
    snprintf(path, sizeof(path), "%s/DRIVES/D000001.HHD", root);
    long size;
    uint8_t *want = slurp(path, &size);
    CHECK(size > 2000 && size < (1 << 20));                             /* cut to its length at the clean end */
    long n = fetch(h, HH_SER_F_DRIVE, 1, got, 0, &busy);
    CHECK(n == size && want && memcmp(got, want, (size_t)size) == 0);
    /* a transfer that stopped half way carries on from there */
    memset(got + 1000, 0, sizeof(got) - 1000);
    n = fetch(h, HH_SER_F_DRIVE, 1, got, 1000, NULL);
    CHECK(n == size && memcmp(got, want, (size_t)size) == 0);
    free(want);
    /* past the end: an empty chunk marked as the end; no such file: said so */
    hh_ser_read_file_t rreq = {HH_SER_F_DRIVE, 0, HH_SER_CHUNK, 1, (uint32_t)size + 10};
    hh_ser_file_data_t fd;
    send(h, HH_SC_READ_FILE, &rreq, sizeof(rreq));
    memcpy(&fd, last(HH_SF_FILE_DATA)->p, sizeof(fd));
    CHECK(fd.len == 0 && (fd.flags & HH_SER_FD_EOF));
    CHECK(fetch(h, HH_SER_F_DRIVE, 77, got, 0, &busy) == -1 && !busy);

    /* start again: a second drive */
    send(h, HH_SC_DRIVE, "\1", 1);
    hhhost_snapshot(h, &snap);
    CHECK(acked(HH_SC_DRIVE, NULL) == HH_SER_OK && snap.log.recording && snap.log.drive_no == 2);
    run(h, 7.0);

    /* trouble codes: the engine's own Mode 03 read, reported as events */
    rx.keep_type = HH_SF_EVENT; rx.n_keep = 0;
    send(h, HH_SC_DTC, NULL, 0);
    CHECK(acked(HH_SC_DTC, NULL) == HH_SER_OK);
    run(h, 32.0);
    int dtc = 0;
    for (int i = 0; i < rx.n_keep; i++) {
        hh_rec_event_t ev;
        memcpy(&ev, rx.keep[i].p, sizeof(ev));
        dtc += ev.code == HH_EV_DTC && ev.v.i == 0x0135;
    }
    hhhost_snapshot(h, &snap);
    CHECK(dtc == 1 && snap.dtc.valid && snap.link.state == HH_LINK_OK);

    /* paging: 60 drives on the card come out 24 at a time, lowest first, none twice */
    for (int i = 10; i < 70; i++) {
        snprintf(path, sizeof(path), "%s/DRIVES/D%06d.HHD", root, i * 3);
        FILE *f = fopen(path, "wb");
        fwrite("x", 1, (size_t)(i % 5) + 1, f);
        fclose(f);
    }
    uint32_t after = 0, seen = 0, prev_no = 0;
    int pages = 0, sorted = 1;
    for (;;) {
        lreq.kind = HH_SER_F_DRIVE; lreq.after_no = after;
        rx.keep_type = HH_SF_FILE_INFO; rx.n_keep = 0;
        send(h, HH_SC_LIST_FILES, &lreq, sizeof(lreq));
        pages++;
        int more = 0;
        for (int i = 0; i < rx.n_keep; i++) {
            memcpy(&fi, rx.keep[i].p, sizeof(fi));
            if (fi.file_no == 0xFFFFFFFFu) { more = fi.flags & HH_SER_FI_MORE; break; }
            sorted &= fi.file_no > prev_no;
            prev_no = after = fi.file_no;
            seen++;
        }
        if (!more || pages > 10) break;
    }
    CHECK(seen == 62 && pages == 3 && sorted);
    lreq.kind = HH_SER_F_CAPTURE; lreq.after_no = 0;
    rx.n_keep = 0;
    send(h, HH_SC_LIST_FILES, &lreq, sizeof(lreq));
    CHECK(rx.n_keep == 1);
    lreq.kind = 9;
    send(h, HH_SC_LIST_FILES, &lreq, sizeof(lreq));
    CHECK(acked(HH_SC_LIST_FILES, NULL) == HH_SER_E_ARG);

    /* push a map: interrupted, resumed, checked, moved into place; the old one is kept */
    const uint8_t *def;
    size_t def_len = hhhost_default_map(&def);
    uint32_t crc = hh_crc32(0, def, def_len);
    snprintf(path, sizeof(path), "%s/HANDHELD.MAP", root);
    CHECK(put(h, HH_SER_F_MAP, def, (uint32_t)def_len, crc, 800, &value) == 99 && file_size(path) == -1);
    CHECK(s->put_got == 800);
    CHECK(put(h, HH_SER_F_MAP, def, (uint32_t)def_len, crc, 0xFFFFFFFFu, &value) == HH_SER_OK);
    long msize;
    uint8_t *on_card = slurp(path, &msize);
    CHECK(msize == (long)def_len && memcmp(on_card, def, def_len) == 0);
    free(on_card);
    /* it can be read back, and listed */
    CHECK(fetch(h, HH_SER_F_MAP, 1, got, 0, NULL) == (long)def_len && memcmp(got, def, def_len) == 0);
    /* a chunk out of order is refused with the offset that is wanted */
    hh_ser_put_begin_t pb = {HH_SER_F_MAP, {0, 0, 0}, (uint32_t)def_len, crc ^ 1};
    send(h, HH_SC_PUT_BEGIN, &pb, sizeof(pb));
    uint8_t msg[20] = {100, 0, 0, 0};
    send(h, HH_SC_PUT_DATA, msg, sizeof(msg));
    CHECK(acked(HH_SC_PUT_DATA, &value) == HH_SER_E_OFFSET && value == 0);
    /* wrong CRC: refused, the good map stays */
    CHECK(put(h, HH_SER_F_MAP, def, (uint32_t)def_len, crc ^ 1, 0xFFFFFFFFu, &value) == HH_SER_E_CRC);
    CHECK(file_size(path) == (long)def_len);
    /* right CRC, but not a map the handheld would load: refused, the good map stays */
    uint8_t *junk = malloc(def_len);
    memcpy(junk, def, def_len);
    junk[100] ^= 0xFF;
    CHECK(put(h, HH_SER_F_MAP, junk, (uint32_t)def_len, hh_crc32(0, junk, def_len), 0xFFFFFFFFu, &value) == HH_SER_E_REFUSED);
    free(junk);
    on_card = slurp(path, &msize);
    CHECK(msize == (long)def_len && memcmp(on_card, def, def_len) == 0);
    free(on_card);
    snprintf(path, sizeof(path), "%s/HANDHELD.TMP", root);
    CHECK(file_size(path) == -1);
    /* a second good push keeps the first as the backup */
    CHECK(put(h, HH_SER_F_MAP, def, (uint32_t)def_len, crc, 0xFFFFFFFFu, &value) == HH_SER_OK);
    snprintf(path, sizeof(path), "%s/HANDHELD.MBK", root);
    CHECK(file_size(path) == (long)def_len);
    /* settings: taken, with the count of lines the handheld will skip */
    const char *cfg = "drive_gap_s = 60\nserial_baud = 460800\nnonsense = 1\n";
    CHECK(put(h, HH_SER_F_CFG, (const uint8_t *)cfg, (uint32_t)strlen(cfg), hh_crc32(0, cfg, strlen(cfg)), 0xFFFFFFFFu, &value)
          == HH_SER_OK && value == 1);
    snprintf(path, sizeof(path), "%s/HANDHELD.CFG", root);
    CHECK(file_size(path) == (long)strlen(cfg));
    /* drive files cannot be written from the laptop; neither can anything oversize */
    pb.kind = HH_SER_F_DRIVE; pb.size = 100;
    send(h, HH_SC_PUT_BEGIN, &pb, sizeof(pb));
    CHECK(acked(HH_SC_PUT_BEGIN, NULL) == HH_SER_E_ARG);
    pb.kind = HH_SER_F_CFG; pb.size = 1 << 20;
    send(h, HH_SC_PUT_BEGIN, &pb, sizeof(pb));
    CHECK(acked(HH_SC_PUT_BEGIN, NULL) == HH_SER_E_ARG);
    send(h, HH_SC_PUT_END, NULL, 0);
    CHECK(acked(HH_SC_PUT_END, NULL) == HH_SER_E_STATE);

    /* an unknown command, a damaged frame, a frame of another protocol version, noise */
    send(h, 0x7E, NULL, 0);
    CHECK(acked(0x7E, NULL) == HH_SER_E_UNSUPPORTED);
    uint8_t out[HH_SER_FRAME_MAX];
    uint32_t frames = s->rx_frames, bad = s->rx_bad;
    hhhost_snapshot(h, &snap);
    uint32_t markers = snap.markers;
    size_t len = hh_ser_frame(HH_SC_MARK, 1, NULL, 0, out);
    out[1] ^= 0x04;
    hhhost_serial_rx(h, out, len);
    uint8_t raw[5] = {HH_SER_PROTO + 1, HH_SC_MARK, 1}, enc[8];
    uint16_t c16 = hh_crc16(raw, 3);
    raw[3] = (uint8_t)c16; raw[4] = (uint8_t)(c16 >> 8);
    len = hh_cobs_encode(raw, 5, enc);
    enc[len++] = 0;
    hhhost_serial_rx(h, enc, len);
    uint8_t noise[3000];
    uint32_t r = 7;
    for (size_t i = 0; i < sizeof(noise); i++) { r = r * 1664525u + 1013904223u; noise[i] = (uint8_t)(r >> 13); }
    hhhost_serial_rx(h, noise, sizeof(noise));
    memset(noise, 0x55, sizeof(noise));                 /* a long run with no frame end, then a good frame */
    hhhost_serial_rx(h, noise, sizeof(noise));
    hhhost_serial_rx(h, (const uint8_t *)"\0", 1);
    hhhost_snapshot(h, &snap);
    CHECK(s->rx_frames == frames && s->rx_bad >= bad + 3 && snap.markers == markers);
    send(h, HH_SC_MARK, NULL, 0);
    hhhost_snapshot(h, &snap);
    CHECK(acked(HH_SC_MARK, NULL) == HH_SER_OK && snap.markers == markers + 1);

    /* read-only: after every command there is, the K-line saw reads, the wake-up and the Mode 03 request only */
    run(h, 40.0);
    hhhost_snapshot(h, &snap);
    CHECK(SIM.bad_frames_sent == 0 && SIM.mode4_seen == 0 && hh_core_link_stats(hhhost_core(h))->blocked == 0);
    CHECK(snap.link.state == HH_LINK_OK && snap.link.hz_fast > 15.0f);
    CHECK(rx.bad == 0);

    /* silent for 3 s: no longer "attached" */
    CHECK(!hh_serial_host_attached(s));
    hhhost_free(h);
}

/* No card: the stream runs, file commands say they cannot. */
TEST(test_no_card_ops)
{
    hhsim_reset();
    hhhost_t *h = hhhost_new(fresh_dir(), NULL, 0, NULL, hhsim_callbacks(), 1, 0);
    hh_serial_t *s = hhhost_serial(h, 0, 0);
    memset(&s->fs, 0, sizeof(s->fs));
    rx_reset();
    run(h, 3.0);
    hh_ser_list_t lreq = {HH_SER_F_DRIVE, {0, 0, 0}, 0};
    send(h, HH_SC_LIST_FILES, &lreq, sizeof(lreq));
    CHECK(acked(HH_SC_LIST_FILES, NULL) == HH_SER_E_UNSUPPORTED);
    hh_ser_read_file_t rreq = {HH_SER_F_DRIVE, 0, 100, 1, 0};
    send(h, HH_SC_READ_FILE, &rreq, sizeof(rreq));
    CHECK(acked(HH_SC_READ_FILE, NULL) == HH_SER_E_UNSUPPORTED);
    hh_ser_put_begin_t pb = {HH_SER_F_CFG, {0, 0, 0}, 10, 0};
    send(h, HH_SC_PUT_BEGIN, &pb, sizeof(pb));
    CHECK(acked(HH_SC_PUT_BEGIN, NULL) == HH_SER_E_UNSUPPORTED);
    send(h, HH_SC_MARK, NULL, 0);
    CHECK(acked(HH_SC_MARK, NULL) == HH_SER_OK);
    hhhost_free(h);
}

int main(void)
{
    const char *base = getenv("TMPDIR");
    snprintf(TMP, sizeof(TMP), "%s/hhsertest.XXXXXX", base && *base ? base : "/tmp");
    if (!mkdtemp(TMP)) { perror("mkdtemp"); return 2; }
    RUN(test_framing);
    RUN(test_ring_drop_oldest);
    RUN(test_stream);
    RUN(test_stream_not_drained);
    RUN(test_tx_only);
    RUN(test_commands);
    RUN(test_no_card_ops);
    printf("%d checks, %d failed (files left in %s)\n", checks, failures, TMP);
    return failures ? 1 : 0;
}
