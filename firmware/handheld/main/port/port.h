#ifndef HH_PORT_H
#define HH_PORT_H

/* Start the display, LVGL and the UI in their own task on core 1. Returns at once. */
void ui_port_start(void);

#endif
