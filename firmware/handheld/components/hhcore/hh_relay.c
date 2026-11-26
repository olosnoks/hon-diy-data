/* hh_relay: the laptop's ECU reads, relayed to the handheld (SERIAL.md "Relay: the laptop's ECU data to the
 * handheld"). The laptop owns the K-line (KKL cable on the laptop) and forwards every reply over the USB-C link;
 * this file checks the RELAY_* payloads and hands them to the engine (hh_core_relay_*, hh_core.c), which runs them
 * through the path of its own reads under its lock. Called from the serial RX path (hh_serial.c dispatch). */
#include <string.h>

#include "hh_serial.h"

int hh_relay_frame(hh_core_t *c, uint8_t type, const uint8_t *p, size_t len)
{
    if (!c) return -1;
    switch (type) {
    case HH_SR_RELAY_READ: {
        hh_ser_relay_read_t r;
        if (len < sizeof(r)) return -1;
        memcpy(&r, p, sizeof(r));
        if (r.len < 1 || r.len > HH_MAX_READ || len < sizeof(r) + r.len || r.addr + r.len > 256) return -1;
        return hh_core_relay_read(c, r.addr, p + sizeof(r), r.len, r.latency_us);
    }
    case HH_SR_RELAY_ISO: {
        hh_ser_relay_iso_t r;
        if (len < sizeof(r)) return -1;
        memcpy(&r, p, sizeof(r));
        if (r.len > 8 || len < sizeof(r) + r.len) return -1;
        return hh_core_relay_iso(c, r.mode, r.pid, p + sizeof(r), r.len);
    }
    case HH_SR_RELAY_TICK: {
        hh_ser_relay_tick_t r;
        if (len < sizeof(r)) return -1;
        memcpy(&r, p, sizeof(r));
        return hh_core_relay_tick(c, r.latency_us, r.hz_c);
    }
    case HH_SR_RELAY_LINK: {
        hh_ser_relay_link_t r;
        if (len < sizeof(r)) return -1;
        memcpy(&r, p, sizeof(r));
        uint8_t n = r.ecu_id_len > 10 ? 10 : r.ecu_id_len;
        if (len < sizeof(r) + n) n = (uint8_t)(len - sizeof(r));
        return hh_core_relay_link(c, r.state, r.up, r.iso, p + sizeof(r), n);
    }
    default:
        return -1;
    }
}
