/* STUB ONLY. Controls for the fake-data generator; nothing here exists in the
 * real hh-core. The simulator uses these to stage a scene and a clock.
 */
#ifndef HH_STUB_H
#define HH_STUB_H

#include <stdint.h>

#define HH_STUB 1

typedef enum {
    HH_SCENE_DEMO = 0,     /* cycle through the scenes below, 8 s each (device default) */
    HH_SCENE_DRIVE,        /* everything in range */
    HH_SCENE_WARN,         /* intake air over its warn limit */
    HH_SCENE_ALARM,        /* coolant over its alarm limit, battery warn */
    HH_SCENE_STALL,        /* engine dies, stall catcher fires */
    HH_SCENE_LINK_LOST,    /* ECU silent: every ECU channel stale */
    HH_SCENE_CONNECTING,   /* just powered up: no ECU, no fix, no card */
} hh_stub_scene_t;

void hh_stub_set_scene(hh_stub_scene_t scene);
/* Replace the clock (microseconds). NULL restores the platform clock. */
void hh_stub_set_clock(int64_t (*now_us)(void));
uint32_t hh_stub_marks(void);
uint32_t hh_stub_acks(void);

#endif
