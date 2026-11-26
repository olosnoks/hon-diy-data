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

/* Where the fake GPS drives (the Map screen's screenshots): real roads in
 * Jackson, Mississippi, from OpenStreetMap. The car goes along the route at
 * the route's speed, from start_m metres along it, and turns round at the end. */
typedef enum {
    HH_ROUTE_NONE = 0,      /* the default: a made-up straight line east of Jackson, speed as the VSS */
    HH_ROUTE_STATE_ST,      /* State Street, downtown, northbound, 25 km/h */
    HH_ROUTE_I55,           /* I-55 northbound through north Jackson, 113 km/h (70 mph) */
    HH_ROUTE_PARKED,        /* standing on State Street */
} hh_stub_route_t;

void hh_stub_set_scene(hh_stub_scene_t scene);
void hh_stub_set_route(hh_stub_route_t route, double start_m);
/* Replace the clock (microseconds). NULL restores the platform clock. */
void hh_stub_set_clock(int64_t (*now_us)(void));
uint32_t hh_stub_marks(void);
uint32_t hh_stub_acks(void);
/* What hh_display_units() answers (HH_UNITS_US by default, like HANDHELD.CFG); ui_create picks it up. */
void hh_stub_set_units(int units);

#endif
