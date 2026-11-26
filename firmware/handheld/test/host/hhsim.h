/* A simulated ECU on a simulated K-line with a virtual clock, for the C
 * tests: exact byte timing, the cable's echo, and faults on request. */
#ifndef HHSIM_H
#define HHSIM_H

#include "hhhost.h"

typedef struct {
    uint8_t mem[256];
    double now;
    /* behaviour */
    int honda;              /* answers Honda read requests */
    int iso;                /* answers the ISO 9141-2 5-baud init and Mode 01 / 03 */
    int echo;               /* the cable echoes what is sent */
    int powered;            /* 0 = ECU silent (key off) */
    int port_ok;            /* 0 = the cable is unplugged */
    uint32_t honda_baud;    /* Honda reads are answered at this baud rate only (9600; a real P2P: 10400) */
    int need_wake;          /* Honda reads are answered only after the wake-up bytes (a real P2P) */
    int woken;              /* the wake-up bytes came since power-up */
    int closed_loop;        /* PID 03: fuel system 1 in closed loop */
    double turnaround_s;
    /* faults, each applied to the next reply and then cleared */
    int drop_next, corrupt_next, truncate_next, junk_before_next, junk_after_next;
    double late_next_s;
    /* random faults: probability per reply, 0..1 */
    double p_drop, p_corrupt, p_truncate, p_junk;
    uint32_t rng;
    /* what was seen */
    uint32_t requests, bad_frames_sent;     /* frames that are neither a read, the wake-up nor allowed ISO */
    uint32_t iso_inits, opens, mode4_seen, wakes, honda_ignored;
    uint32_t pid_seen;      /* Mode 01 PIDs 01-20 asked for: bit pid-1 */
    uint16_t dtc[3];
} hhsim_t;

extern hhsim_t SIM;
void hhsim_reset(void);
const hhhost_cb_t *hhsim_callbacks(void);
void hhsim_set_rpm(double rpm);

#endif
