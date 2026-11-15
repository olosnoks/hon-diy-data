/* State shapes. Shape first, colour second (HANDOFF.md section 3 and 4): a
 * warning is a triangle, an alarm an octagon, an unverified address a dashed
 * box, whatever the theme and however washed out the panel is.
 */
#ifndef UI_SHAPES_H
#define UI_SHAPES_H

#include "lvgl.h"

typedef enum {
    UI_SHAPE_NONE,
    UI_SHAPE_DOT,            /* filled circle: up, on, recording */
    UI_SHAPE_SQUARE,         /* hollow square: off, not connected, flag OFF */
    UI_SHAPE_SQUARE_FILLED,  /* flag ON */
    UI_SHAPE_HALF,           /* half-filled circle: working on it */
    UI_SHAPE_RING,           /* stall catcher armed */
    UI_SHAPE_CROSS,          /* failed / silent */
    UI_SHAPE_TRIANGLE,       /* warn */
    UI_SHAPE_OCTAGON,        /* alarm */
    UI_SHAPE_VERIFIED,       /* solid square with a check */
    UI_SHAPE_UNVERIFIED,     /* dashed box with a question mark */
} ui_shape_t;

lv_obj_t *ui_shape_create(lv_obj_t *parent, int size);
/* colour: the shape. ink: the check inside VERIFIED (ignored otherwise). */
void ui_shape_set(lv_obj_t *obj, ui_shape_t kind, lv_color_t colour, lv_color_t ink);

#endif
