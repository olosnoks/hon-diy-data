/* One button, three gestures. Pure logic: feed it the raw pin level and a
 * millisecond clock.
 *
 *   short      released before 1 s          -> next screen
 *   long       released between 1 s and 3 s -> MARK
 *   very long  still held at 3 s            -> acknowledge alarm, else Dracula/Darker
 *
 * MARK is decided on release, so that holding on for the 3 s gesture does not
 * also drop a mark into the log. The hint strip shows which one is armed.
 */
#ifndef UI_INPUT_H
#define UI_INPUT_H

#include <stdbool.h>
#include <stdint.h>

#define UI_BTN_DEBOUNCE_MS 30
#define UI_BTN_LONG_MS     1000
#define UI_BTN_VLONG_MS    3000

typedef enum { UI_BTN_NONE, UI_BTN_SHORT, UI_BTN_LONG, UI_BTN_VLONG } ui_btn_event_t;

typedef struct {
    bool raw, down, spent;       /* spent: the very long press fired, ignore the release */
    uint32_t t_raw, t_down;
} ui_btn_t;

ui_btn_event_t ui_btn_step(ui_btn_t *b, bool pressed, uint32_t now_ms);
/* How long the button has been held, 0 when up or after the 3 s gesture fired. */
uint32_t ui_btn_held_ms(const ui_btn_t *b, uint32_t now_ms);

#endif
