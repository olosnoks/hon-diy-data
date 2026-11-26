/* Host tests of the relay (SERIAL.md "Relay: the laptop's ECU data to the handheld"; hh_relay.c and the relay
 * section of hh_core.c): the laptop reads the ECU and forwards every reply as RELAY_* frames; the handheld, with no
 * K-line cable of its own, shows, alarms on, catches stalls in and logs them as if it had read them itself.
 * The "laptop" here builds its frames from the simulated ECU's memory (SIM.mem), so values can be checked against
 * what the handheld decodes. `make` runs it after hhsertest. */
#include <math.h>
#include <stddef.h>
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

/* ---- what the handheld sends, in miniature ------------------------------------------ */

typedef struct { uint8_t type; uint16_t len; uint8_t p[HH_SER_MAX_PAYLOAD]; } frame_t;
static struct {
    uint8_t buf[HH_SER_FRAME_MAX + 8];
    size_t n;
    uint32_t count[256], bad;
    frame_t last[256];
    hh_rec_event_t ev[512]; int n_ev;
} rx;

static void rx_reset(void) { memset(&rx, 0, sizeof(rx)); }

static void rx_bytes(const uint8_t *d, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        if (d[i]) { if (rx.n < sizeof(rx.buf)) rx.buf[rx.n++] = d[i]; else rx.bad++; continue; }
        uint8_t raw[HH_SER_FRAME_MAX];
        int m = hh_cobs_decode(rx.buf, rx.n, raw);
        rx.n = 0;
        if (m < 5 || raw[0] != HH_SER_PROTO || hh_crc16(raw, (size_t)m - 2) != (uint16_t)(raw[m - 2] | (raw[m - 1] << 8))) {
            if (m > 0) rx.bad++;
            continue;
        }
        frame_t *f = &rx.last[raw[1]];
        f->type = raw[1]; f->len = (uint16_t)(m - 5);
        memcpy(f->p, raw + 3, f->len);
        rx.count[raw[1]]++;
        if (raw[1] == HH_SF_EVENT && rx.n_ev < 512) memcpy(&rx.ev[rx.n_ev++], raw + 3, sizeof(hh_rec_event_t));
    }
}

static void drain(hhhost_t *h)
{
    uint8_t out[2048];
    size_t n;
    while ((n = hhhost_serial_poll(h, out, sizeof(out))) != 0) rx_bytes(out, n);
}

static int events(uint8_t code)
{
    int n = 0;
    for (int i = 0; i < rx.n_ev; i++) n += rx.ev[i].code == code;
    return n;
}

static hh_tap_link_t last_link(void)
{
    hh_tap_link_t l;
    memset(&l, 0, sizeof(l));
    if (rx.last[HH_SF_LINK].len == sizeof(l)) memcpy(&l, rx.last[HH_SF_LINK].p, sizeof(l));
    return l;
}

/* ---- the laptop's side ---------------------------------------------------------------- */

static uint8_t cmd_seq;
static uint32_t relayed_reads, relayed_bytes;
static double host_t;

static void send(hhhost_t *h, uint8_t type, const void *head, size_t head_len, const void *data, size_t data_len)
{
    uint8_t payload[HH_SER_MAX_PAYLOAD], out[HH_SER_FRAME_MAX];
    memcpy(payload, head, head_len);
    if (data_len) memcpy(payload + head_len, data, data_len);
    size_t n = hh_ser_frame(type, ++cmd_seq, payload, head_len + data_len, out);
    hhhost_serial_rx(h, out, n);
}

static void relay_link(hhhost_t *h, uint8_t state, uint8_t up, uint8_t iso)
{
    hh_ser_relay_link_t l = {(uint64_t)(host_t * 1e6), state, up, iso, 10};
    send(h, HH_SR_RELAY_LINK, &l, sizeof(l), "37820-P2P-", 10);
}

/* One laptop fast cycle: every block of the handheld's own plan (the laptop plans from the same map), from the ECU's
 * memory, then the tick. Then the handheld's link task runs for `steps` steps (20 ms each while it relays). */
static void relay_cycle(hhhost_t *h, int steps)
{
    const hh_plan_t *plan = hh_core_plan(hhhost_core(h));
    for (int b = 0; b < plan->n_blocks; b++) {
        hh_ser_relay_read_t r = {(uint64_t)(host_t * 1e6), (uint8_t)plan->blocks[b].addr, (uint8_t)plan->blocks[b].length, 4200};
        send(h, HH_SR_RELAY_READ, &r, sizeof(r), SIM.mem + r.addr, r.len);
        relayed_reads++; relayed_bytes += r.len;
    }
    hh_ser_relay_tick_t tk = {(uint64_t)(host_t * 1e6), 4200, 2400, relayed_reads};
    send(h, HH_SR_RELAY_TICK, &tk, sizeof(tk), NULL, 0);
    host_t += 0.04;
    hhhost_run(h, steps);
    drain(h);
}

/* the laptop relays for `seconds`, with a RELAY_LINK once a second */
static void relay_for(hhhost_t *h, double seconds)
{
    double until = SIM.now + seconds, next_link = SIM.now;
    while (SIM.now < until) {
        if (SIM.now >= next_link) { relay_link(h, 2, 1, 0); next_link = SIM.now + 1.0; }
        relay_cycle(h, 2);
    }
}

static void run(hhhost_t *h, double until)
{
    while (SIM.now < until) { hhhost_run(h, 1); drain(h); }
}

static int ch_index(hhhost_t *h, const char *name)
{
    uint16_t n;
    const hh_channel_info_t *info = hh_core_channels(hhhost_core(h), &n);
    for (int i = 0; i < n; i++) if (strcmp(info[i].name, name) == 0) return i;
    return -1;
}

/* A handheld with no K-line cable of its own, on the laptop's USB-C cable. */
static hhhost_t *no_cable(const char **root)
{
    hhsim_reset();
    SIM.port_ok = 0;
    *root = fresh_dir();
    char dir[300];
    snprintf(dir, sizeof(dir), "%s", *root);
    hhhost_t *h = hhhost_new(dir, NULL, 0, "prealloc_mb = 0\n", hhsim_callbacks(), 7, 0);
    hhhost_serial(h, 0, 0);
    rx_reset();
    relayed_reads = relayed_bytes = 0;
    host_t = 5000.0;
    run(h, 2.0);
    return h;
}

/* ---- a small reader of drive files (FORMAT.md) ------------------------------------------- */

typedef struct {
    hh_file_hdr_t hdr;
    int ok, reads, iso, ended, stalls, rpm_reads;
    uint32_t read_bytes;
    int data_ok;                /* every READS record's bytes are the ECU's (SIM.mem at addr) */
    uint8_t first_iso_pid;
} scan_t;

static void scan(const char *path, scan_t *s, const uint8_t *mem)
{
    memset(s, 0, sizeof(*s));
    s->data_ok = 1;
    FILE *f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *d = malloc((size_t)size + 1);
    if (fread(d, 1, (size_t)size, f) != (size_t)size) { fclose(f); free(d); return; }
    fclose(f);
    if (size < (long)sizeof(s->hdr)) { free(d); return; }
    memcpy(&s->hdr, d, sizeof(s->hdr));
    if (s->hdr.magic != HH_FILE_MAGIC || s->hdr.crc != hh_crc32(0, d, offsetof(hh_file_hdr_t, crc))) { free(d); return; }
    s->ok = 1;
    long pos = sizeof(s->hdr);
    while (pos + 28 <= size && !s->ended) {
        hh_block_hdr_t b;
        memcpy(&b, d + pos, sizeof(b));
        if (b.magic != HH_BLOCK_MAGIC || pos + 28 + (long)b.payload_len > size) { s->ok = 0; break; }
        uint32_t crc = hh_crc32(hh_crc32(0, &s->hdr.nonce, 4), d + pos, 24 + b.payload_len), want;
        memcpy(&want, d + pos + 24 + b.payload_len, 4);
        if (crc != want) { s->ok = 0; break; }
        const uint8_t *p = d + pos + 24;
        if (b.type == HH_B_READS) {
            uint32_t at = 0;
            for (int i = 0; i < b.count; i++) {
                hh_rec_read_t r;
                memcpy(&r, p + at, sizeof(r));
                if (mem && memcmp(p + at + sizeof(r), mem + r.addr, r.len) != 0) s->data_ok = 0;
                if (r.latency_us != 4200) s->data_ok = 0;
                s->reads++; s->read_bytes += r.len; s->rpm_reads += r.addr == 0;
                at += sizeof(r) + r.len;
            }
        }
        if (b.type == HH_B_ISO) {
            hh_rec_iso_t r;
            memcpy(&r, p, sizeof(r));
            if (!s->iso) s->first_iso_pid = r.pid;
            s->iso += b.count;
        }
        if (b.type == HH_B_EVENTS)
            for (uint32_t i = 0; i + 16 <= b.payload_len; i += 16) {
                hh_rec_event_t e;
                memcpy(&e, p + i, sizeof(e));
                s->stalls += e.code == HH_EV_STALL;
            }
        if (b.type == HH_B_END) s->ended = 1;
        pos += 28 + (long)b.payload_len;
    }
    free(d);
}

static const char *file_path(const char *root, const char *dir, char letter, int no)
{
    static char path[400];
    snprintf(path, sizeof(path), "%s/%s/%c%06d.HHD", root, dir, letter, no);
    return path;
}

/* ---- tests -------------------------------------------------------------------------------- */

/* RELAY frames through hh_serial_rx -> the engine's channel values, the link state, the screen's snapshot */
TEST(test_relay_values)
{
    const char *root;
    hhhost_t *h = no_cable(&root);
    hh_snapshot_t snap;
    hhhost_snapshot(h, &snap);
    CHECK(snap.link.state == HH_LINK_ERROR && snap.link.backend == HH_BACKEND_USB);     /* K-line cable not found */
    hh_ser_hello_t hello;
    memcpy(&hello, rx.last[HH_SF_HELLO].p, sizeof(hello));
    CHECK(rx.count[HH_SF_HELLO] >= 1 && (hello.caps & HH_SER_CAP_RELAY) && (hello.caps & HH_SER_CAP_HL));
    CHECK(!hh_core_relaying(hhhost_core(h)) && rx.count[HH_SF_READ] == 0);

    uint32_t opens = SIM.opens, requests = SIM.requests;
    uint32_t reads_before = rx.count[HH_SF_READ], ticks_before = rx.count[HH_SF_TICK], iso_before = rx.count[HH_SF_ISO];
    hhsim_set_rpm(2468);
    SIM.mem[5] = 0x7B;
    relay_for(h, 4.0);

    CHECK(hh_core_relaying(hhhost_core(h)));
    hhhost_snapshot(h, &snap);
    int rpm = ch_index(h, "rpm");
    CHECK(rpm >= 0 && fabsf(snap.value[rpm] - 2468.0f) < 0.5f && snap.fresh[rpm]);
    int measured = 0, filled = 0;
    uint16_t n_ch;
    const hh_channel_info_t *info = hh_core_channels(hhhost_core(h), &n_ch);
    for (int i = 0; i < n_ch; i++) {
        if (info[i].kind == HH_KIND_MATH) continue;
        measured++;
        filled += !isnan(snap.value[i]);
    }
    CHECK(measured > 3 && filled == measured);          /* every reading filled */
    CHECK(snap.link.state == HH_LINK_RELAY && snap.link.backend == HH_BACKEND_LAPTOP);
    CHECK(strcmp(snap.status, "via laptop") == 0 && memcmp(snap.link.ecu_id, "37820-P2P-", 10) == 0);
    CHECK(snap.link.hz_fast > 23.9f && snap.link.hz_fast < 24.1f);   /* the laptop's own rate */
    CHECK(snap.log.recording && snap.log.drive_no == 1 && snap.stall.armed);
    /* the K-line was left alone: not reopened, nothing asked of it */
    CHECK(SIM.opens == opens && SIM.requests == requests);
    /* nothing echoed back; LINK, EVENT, STATS, HELLO go on */
    CHECK(rx.count[HH_SF_READ] == reads_before && rx.count[HH_SF_TICK] == ticks_before && rx.count[HH_SF_ISO] == iso_before);
    hh_tap_link_t l = last_link();
    CHECK(l.state == HH_LINK_RELAY && l.up == 1 && l.backend == HH_BACKEND_LAPTOP);
    CHECK(events(HH_EV_LINK_UP) == 1 && events(HH_EV_DRIVE_START) == 1 && rx.count[HH_SF_STATS] >= 3);
    run(h, SIM.now + 0.0);
    memcpy(&hello, rx.last[HH_SF_HELLO].p, sizeof(hello));
    CHECK(hello.link_state == HH_LINK_RELAY && hello.backend == HH_BACKEND_LAPTOP && hello.ecu_id_len == 10);
    hh_ser_stats_t st;
    memcpy(&st, rx.last[HH_SF_STATS].p, sizeof(st));
    CHECK(st.link_state == HH_LINK_RELAY && (st.flags & HH_SER_ST_RECORDING) && st.reads > 0);    /* STATS is once a second */
    CHECK(rx.bad == 0 && hhhost_serial_stat(h, "rx_bad") == 0);

    /* RELAY_LINK up = 0 (key off): a link loss, as on the handheld's own cable */
    relay_link(h, 0, 0, 0);
    drain(h);
    hhhost_snapshot(h, &snap);
    l = last_link();
    CHECK(l.state == HH_LINK_RELAY && l.up == 0 && events(HH_EV_LINK_DOWN) == 1);
    CHECK(isnan(snap.value[rpm]) && strcmp(snap.status, "via laptop: no reply from the ECU") == 0);
    hhhost_free(h);
}

/* the drive file holds the relayed reads, with the record types of FORMAT.md */
TEST(test_relay_drive_file)
{
    const char *root;
    hhhost_t *h = no_cable(&root);
    relay_for(h, 3.0);
    uint32_t sent = relayed_reads, bytes = relayed_bytes;
    hh_core_stop(hhhost_core(h), HH_END_STOPPED);
    hhhost_run(h, 2);
    scan_t s;
    scan(file_path(root, "DRIVES", 'D', 1), &s, SIM.mem);
    CHECK(s.ok && s.ended && s.hdr.kind == HH_FK_DRIVE && s.hdr.backend == HH_BACKEND_LAPTOP && s.hdr.link_mode == 0);
    CHECK(s.hdr.ecu_id_len == 10 && memcmp(s.hdr.ecu_id, "37820-P2P-", 10) == 0);
    CHECK(s.reads == (int)sent && s.read_bytes == bytes && s.data_ok && s.rpm_reads > 50);
    printf("    drive: %d relayed reads, %u bytes, all on the card\n", s.reads, (unsigned)s.read_bytes);

    /* OBD2-only on the laptop: RELAY_ISO, decoded by the PID table; another protocol, another drive */
    hh_core_drive(hhhost_core(h), 1);
    relay_link(h, 3, 1, 1);
    for (int i = 0; i < 20; i++) {
        uint8_t d[2] = {0x1A, 0xF8};                    /* (0x1AF8) / 4 = 1726 rpm */
        hh_ser_relay_iso_t r = {(uint64_t)(host_t * 1e6), 0x41, 0x0C, 2, 0};
        send(h, HH_SR_RELAY_ISO, &r, sizeof(r), d, 2);
        uint8_t e[1] = {40 + 88};                       /* 88 C */
        hh_ser_relay_iso_t r2 = {(uint64_t)(host_t * 1e6), 0x41, 0x05, 1, 0};
        send(h, HH_SR_RELAY_ISO, &r2, sizeof(r2), e, 1);
        hh_ser_relay_tick_t tk = {(uint64_t)(host_t * 1e6), 30000, 500, (uint32_t)i};
        send(h, HH_SR_RELAY_TICK, &tk, sizeof(tk), NULL, 0);
        hhhost_run(h, 10);
        drain(h);
    }
    hh_snapshot_t snap;
    hhhost_snapshot(h, &snap);
    int rpm = ch_index(h, "rpm"), ect = ch_index(h, "ect");
    CHECK(rpm >= 0 && fabsf(snap.value[rpm] - 1726.0f) < 0.01f);
    CHECK(ect < 0 || fabsf(snap.value[ect] - 88.0f) < 0.01f);
    CHECK(strcmp(snap.status, "via laptop: OBD2 only") == 0 && rx.count[HH_SF_ISO] == 0);
    hh_core_stop(hhhost_core(h), HH_END_STOPPED);
    hhhost_run(h, 2);
    scan(file_path(root, "DRIVES", 'D', 2), &s, NULL);  /* DRIVE on: a new drive at once, Honda reads as before */
    CHECK(s.ok && s.ended && s.hdr.link_mode == 0 && s.iso == 0);
    scan(file_path(root, "DRIVES", 'D', 3), &s, NULL);
    CHECK(s.ok && s.ended && s.hdr.backend == HH_BACKEND_LAPTOP && s.hdr.link_mode == 1 && s.iso == 40 && s.first_iso_pid == 0x0C);
    hhhost_free(h);
}

/* a stall from relayed RPM falling to 0: the rule fires, the event goes to the laptop, the capture file is cut */
TEST(test_relay_stall)
{
    const char *root;
    hhhost_t *h = no_cable(&root);
    hhsim_set_rpm(850);
    relay_for(h, 3.0);
    hh_snapshot_t snap;
    hhhost_snapshot(h, &snap);
    CHECK(snap.stall.armed && snap.stall.count == 0);
    hhsim_set_rpm(0);
    relay_for(h, 1.0);
    hhhost_snapshot(h, &snap);
    int stalls = 0;
    for (int i = 0; i < rx.n_ev; i++) stalls += rx.ev[i].code == HH_EV_STALL && rx.ev[i].src == HH_STALL_RPM;
    CHECK(stalls == 1 && snap.stall.count == 1 && snap.stall.triggered);
    CHECK(strcmp(snap.stall.last_reason, "rpm dropped to 0") == 0);
    relay_for(h, 30.0);                                 /* the capture window closes */
    scan_t s;
    scan(file_path(root, "CAPTURES", 'C', 1), &s, NULL);
    CHECK(s.ok && s.ended && s.hdr.kind == HH_FK_CAPTURE && s.hdr.reason == HH_STALL_RPM && s.hdr.backend == HH_BACKEND_LAPTOP);
    CHECK(s.reads > 50);                                /* the pre-trigger seconds, from the relayed reads */
    hh_core_stop(hhhost_core(h), HH_END_STOPPED);
    hhhost_run(h, 2);
    scan(file_path(root, "DRIVES", 'D', 1), &s, NULL);
    CHECK(s.ok && s.ended && s.stalls == 1 && s.reads > 1000);
    hhhost_free(h);
}

/* 3 s without a RELAY frame: back to the handheld's own K-line; and none of this while its own link is up */
TEST(test_relay_timeout)
{
    const char *root;
    hhhost_t *h = no_cable(&root);
    relay_for(h, 2.0);
    CHECK(hh_core_relaying(hhhost_core(h)));
    uint32_t opens = SIM.opens;
    double last = SIM.now;
    run(h, last + 2.9);
    CHECK(hh_core_relaying(hhhost_core(h)) && SIM.opens == opens);
    run(h, last + 3.2);
    hh_snapshot_t snap;
    hhhost_snapshot(h, &snap);
    CHECK(!hh_core_relaying(hhhost_core(h)) && snap.link.state != HH_LINK_RELAY && snap.link.backend == HH_BACKEND_USB);
    CHECK(events(HH_EV_LINK_DOWN) == 1);
    hh_tap_link_t l = last_link();
    CHECK(l.state != HH_LINK_RELAY && l.up == 0 && l.backend == HH_BACKEND_USB);
    run(h, last + 6.0);
    CHECK(SIM.opens > opens);                           /* its own K-line handling again: the cable is looked for */

    /* a cable on the handheld now: its own reads, tapped to the laptop again */
    SIM.port_ok = 1;
    uint32_t reads = rx.count[HH_SF_READ];
    run(h, last + 12.0);
    hhhost_snapshot(h, &snap);
    CHECK(snap.link.state == HH_LINK_OK && rx.count[HH_SF_READ] > reads + 100);

    /* its own link is up: RELAY frames are ignored, the K-line stays its own */
    uint32_t requests = SIM.requests, stalls = snap.stall.count;   /* 1: the laptop vanishing while armed is a link loss */
    reads = rx.count[HH_SF_READ];
    hh_ser_relay_read_t r = {1, 0, 2, 100};
    uint8_t zero[2] = {0, 0};
    for (int i = 0; i < 10; i++) { send(h, HH_SR_RELAY_READ, &r, sizeof(r), zero, 2); run(h, SIM.now + 0.1); }
    hhhost_snapshot(h, &snap);
    CHECK(!hh_core_relaying(hhhost_core(h)) && snap.link.state == HH_LINK_OK && SIM.requests > requests + 20);
    CHECK(snap.value[ch_index(h, "rpm")] > 700 && rx.count[HH_SF_READ] > reads + 20 && snap.stall.count == stalls);
    hhhost_free(h);
}

/* malformed RELAY frames are counted and change nothing */
TEST(test_relay_malformed)
{
    const char *root;
    hhhost_t *h = no_cable(&root);
    hh_ser_relay_read_t r = {1, 0xF0, 32, 100};         /* past the end of the address space */
    uint8_t data[64] = {0};
    send(h, HH_SR_RELAY_READ, &r, sizeof(r), data, 32);
    r.addr = 0; r.len = 10;
    send(h, HH_SR_RELAY_READ, &r, sizeof(r), data, 4);  /* shorter than it says */
    send(h, HH_SR_RELAY_TICK, &r, 4, NULL, 0);          /* too short */
    CHECK(hhhost_serial_stat(h, "rx_bad") == 3 && !hh_core_relaying(hhhost_core(h)));
    CHECK(hh_relay_frame(hhhost_core(h), HH_SC_MARK, data, 4) == -1);
    hhhost_free(h);
}

int main(void)
{
    const char *base = getenv("TMPDIR");
    snprintf(TMP, sizeof(TMP), "%s/hhrelaytest.XXXXXX", base && *base ? base : "/tmp");
    if (!mkdtemp(TMP)) { perror("mkdtemp"); return 2; }
    RUN(test_relay_values);
    RUN(test_relay_drive_file);
    RUN(test_relay_stall);
    RUN(test_relay_timeout);
    RUN(test_relay_malformed);
    printf("%d checks, %d failed (files left in %s)\n", checks, failures, TMP);
    return failures ? 1 : 0;
}
