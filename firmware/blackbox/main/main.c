/* hondalog black box: application. Hardware-independent: uses bbcore for the
 * logic and bb_hal.h for the hardware.
 *
 *   acq task      ADC batches + edge queue -> bb_engine (1 kHz records, rules)
 *   storage task  event files, summary log, CSV export
 *   link task     USB frames both ways, GPS, LED, sleep
 *
 * One mutex guards the engine. bbcore drops it around file I/O, so a slow
 * card never holds up acquisition.
 */
#include <stdio.h>
#include <string.h>

#include "bb_hal.h"
#include "bbcore.h"

#define ADC_HZ_PER_CHANNEL 5000
#define ADC_BATCH          (BB_N_ANALOG * 25)   /* 5 ms of conversions */
#define CFG_PATH           "/BLACKBOX.CFG"

static bb_engine_t *engine;
static bb_config_t config;
static int selftest_ok = 1;

static const char CFG_HELP[] =
    "# hondalog black box settings. Edit, save, power-cycle the box.\n"
    "# <rail>_scale: car mV = pin mV * scale / 1000 + offset. Calibrate with\n"
    "#   hondalog-bb cal (two meter readings per rail).\n"
    "# rule.N = source op level hysteresis hold_ms qualifiers action\n"
    "#   sources: vbat ecub pump ig1 alt (mV), slew_<rail> (mV per ms),\n"
    "#            tach_age ign_age inj_age (ms since last edge),\n"
    "#            rpm_tach rpm_ign inj_pw (us) inj_duty (0.5 %) button\n"
    "#   qualifiers: ig1 running ign_running tach_alive, joined with +, or -\n"
    "#   action: capture | mark | off.  N is the reason code in the event file.\n";

/* ---- self-test ---------------------------------------------------------- */

static void say(const char *text)
{
    hal_log("%s", text);
    if (engine) bb_engine_text(engine, text);
}

static void selftest(void *ctx)
{
    char line[96];
    int fail = 0, rc;
    (void)ctx;

    rc = bb_core_selftest();
    snprintf(line, sizeof(line), "selftest core: %s (%d)\n", rc ? "FAIL" : "PASS", rc);
    say(line); fail |= rc != 0;

    size_t need = bb_engine_mem_size(&config);
    int pre = engine ? bb_engine_config(engine)->pre_s : config.pre_s;
    snprintf(line, sizeof(line), "selftest ram: %s (ring %u KB, pre-trigger %d s of %d s asked)\n",
             pre == config.pre_s ? "PASS" : "FAIL", (unsigned)(need / 1024), pre, (int)config.pre_s);
    say(line); fail |= pre != config.pre_s;

    rc = hal_storage_selftest();
    snprintf(line, sizeof(line), "selftest sd: %s\n", rc ? "FAIL (no card, or write/read-back failed)" : "PASS");
    say(line); fail |= rc != 0;

    if (engine) {
        bb_status_t st;
        hal_sleep_ms(50);
        bb_engine_status(engine, &st);
        unsigned vbat = st.now.mv[BB_A_VBAT];
        int adc_ok = st.samples > 0 && vbat > 6000 && vbat < 18000 && hal_adc_overruns() == 0;
        snprintf(line, sizeof(line), "selftest adc: %s (battery %u.%02u V, %lu records, %lu overruns)\n",
                 adc_ok ? "PASS" : "FAIL", vbat / 1000, vbat % 1000 / 10, (unsigned long)st.samples,
                 (unsigned long)hal_adc_overruns());
        say(line); fail |= !adc_ok;
        snprintf(line, sizeof(line), "selftest edges: %s (timer %lu Hz, %lu edges seen, %lu dropped)\n",
                 hal_edges_dropped() ? "FAIL" : "PASS", (unsigned long)hal_edges_resolution_hz(),
                 (unsigned long)st.edges, (unsigned long)hal_edges_dropped());
        say(line); fail |= hal_edges_dropped() != 0;
        snprintf(line, sizeof(line), "selftest inputs: ecu+B %u.%01u V, pump %u.%01u V, ig1 %u.%01u V, "
                 "rpm %u, button %d (information only)\n",
                 st.now.mv[BB_A_ECUB] / 1000, st.now.mv[BB_A_ECUB] % 1000 / 100,
                 st.now.mv[BB_A_PUMP] / 1000, st.now.mv[BB_A_PUMP] % 1000 / 100,
                 st.now.mv[BB_A_IG1] / 1000, st.now.mv[BB_A_IG1] % 1000 / 100,
                 st.now.rpm_tach, hal_button());
        say(line);
    }
    selftest_ok = !fail;
    if (engine) bb_engine_set_selftest_failed(engine, fail);
    say(fail ? "selftest: FAIL\n" : "selftest: PASS\n");
}

/* ---- tasks -------------------------------------------------------------- */

static void acq_task(void *arg)
{
    static bb_conv_t conv[ADC_BATCH];
    static hal_edge_t edges[256];
    (void)arg;
    for (;;) {
        uint64_t t_us = 0;
        int n = hal_adc_read(conv, ADC_BATCH, &t_us, 100);
        size_t n_edges = hal_edges_read(edges, 256);
        int button = hal_button();
        hal_lock(NULL);
        for (size_t i = 0; i < n_edges; i++)
            bb_engine_edge(engine, edges[i].ch, edges[i].gpio_level, edges[i].t_us);
        bb_engine_button(engine, button);
        if (n > 0) bb_engine_adc_chunk(engine, t_us, conv, (size_t)n);
        hal_unlock(NULL);
    }
}

static void storage_task(void *arg)
{
    (void)arg;
    for (;;) {
        hal_lock(NULL);
        int busy = bb_engine_storage_step(engine);
        hal_unlock(NULL);
        if (!busy) hal_sleep_ms(5);
    }
}

static int led_state(const bb_status_t *st)
{
    if (!selftest_ok) return BB_LED_FAULT;
    if (st->capturing) return BB_LED_CAPTURE;
    if (!st->sd_ok || st->io_errors) return BB_LED_NO_SD;
    if (st->captures_this_boot) return BB_LED_WAITING;      /* a capture is on the card */
    return (st->state & BB_ST_RUNNING) ? BB_LED_RUNNING : BB_LED_IDLE;
}

static void link_task(void *arg)
{
    static uint8_t buf[256];
    static bb_nmea_t nmea;
    uint64_t last_rx_us = 0;
    (void)arg;
    for (;;) {
        int n = hal_usb_read(buf, sizeof(buf), 5);
        hal_lock(NULL);
        if (n > 0) { bb_engine_rx(engine, buf, (size_t)n); last_rx_us = hal_now_us(); }
        bb_engine_link_poll(engine);
        hal_unlock(NULL);

        if (config.gps_baud) {
            int g = hal_gps_read(buf, sizeof(buf));
            for (int i = 0; i < g; i++) {
                if (!bb_nmea_feed(&nmea, (char)buf[i])) continue;
                nmea.fix.t_us = hal_now_us();
                hal_lock(NULL);
                bb_engine_gps(engine, &nmea.fix);
                hal_unlock(NULL);
            }
        }

        bb_status_t st;
        bb_engine_status(engine, &st);
        uint64_t now = hal_now_us();
        hal_led(bb_led_pattern(led_state(&st), (uint32_t)(now / 1000u)));

        /* key off for a while, nothing being written, no laptop talking: sleep */
        if (config.sleep_after_s && !st.capturing
            && st.ig1_off_ms > (uint32_t)config.sleep_after_s * 1000u
            && now - last_rx_us > 60u * 1000000u && now > 60u * 1000000u) {
            hal_log("IG1 off for %lu s, sleeping\n", (unsigned long)(st.ig1_off_ms / 1000u));
            hal_lock(NULL);                     /* stops the other two tasks touching the card */
            while (bb_engine_storage_step(engine)) { }
            hal_storage_unmount();
            hal_led(0);
            hal_deep_sleep();
        }
    }
}

/* ---- start-up ----------------------------------------------------------- */

static void usb_tx(void *ctx, const uint8_t *wire, size_t len) { (void)ctx; hal_usb_write(wire, len); }
static uint64_t now_cb(void *ctx) { (void)ctx; return hal_now_us(); }

static void load_config(int sd_ok)
{
    static char text[4096];
    bb_config_defaults(&config);
    if (!sd_ok) return;
    int n = hal_storage_read_text(CFG_PATH, text, sizeof(text));
    if (n > 0) {
        int line = 0, bad = bb_config_parse(&config, text, (size_t)n, &line);
        if (bad) hal_log("BLACKBOX.CFG: %d bad line(s), first at line %d; the rest was used\n", bad, line);
        return;
    }
    size_t w = sizeof(CFG_HELP) - 1;            /* no file yet: write the defaults */
    memcpy(text, CFG_HELP, w);
    w += bb_config_dump(&config, text + w, sizeof(text) - w);
    hal_storage_write_text(CFG_PATH, text, w);
}

void bb_app_main(void)
{
    bb_platform_t plat;
    memset(&plat, 0, sizeof(plat));
    hal_init();
    hal_led(1);

    int sd_ok = hal_storage_mount(&plat.io) == 0;
    if (sd_ok) { hal_storage_mkdir("/EVENTS"); hal_storage_mkdir("/SUMMARY"); }
    load_config(sd_ok);

    hal_usb_start();
    if (config.gps_baud) hal_gps_start((uint32_t)config.gps_baud);
    if (hal_adc_start(ADC_HZ_PER_CHANNEL) != 0 || hal_edges_start() != 0) {
        hal_log("ADC or capture did not start\n");
        selftest_ok = 0;
    }

    plat.lock = hal_lock; plat.unlock = hal_unlock;
    plat.tx = usb_tx; plat.now_us = now_cb; plat.selftest = selftest;
    plat.adc_conv_hz = hal_adc_conv_hz();
    plat.device = hal_device_id();
    plat.boot_no = hal_next_boot_no();
    plat.last_event_no = sd_ok ? hal_storage_last_event_no() : 0;
    plat.sd_ok = sd_ok;

    /* the ring takes what the config asks, or as much as there is */
    size_t want = bb_engine_mem_size(&config), have = hal_big_free();
    size_t size = want < have ? want : have - have / 16;
    void *mem = hal_big_alloc(size);
    engine = mem ? bb_engine_init(mem, size, &config, &plat) : NULL;
    if (!engine) {
        hal_log("not enough RAM for the ring buffer (%u KB wanted)\n", (unsigned)(want / 1024));
        for (uint32_t t = 0;; t += 10) { hal_led(bb_led_pattern(BB_LED_FAULT, t)); hal_sleep_ms(10); }
    }
    hal_log("black box %s fw %s boot %lu, ring %u KB, pre-trigger %d s, %lu events on card\n",
            plat.device, BB_FW_VERSION, (unsigned long)plat.boot_no, (unsigned)(size / 1024),
            (int)bb_engine_config(engine)->pre_s, (unsigned long)plat.last_event_no);

    hal_task_start(acq_task, "bb-acq", 3, 4096, 1);
    hal_task_start(storage_task, "bb-store", 2, 12288, 0);
    hal_sleep_ms(300);                          /* let some samples in before judging the ADC */
    selftest(NULL);
    hal_task_start(link_task, "bb-link", 1, 12288, 0);
}
