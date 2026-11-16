# PC build of the handheld UI: the same main/ui sources, LVGL 9.2 rendering
# RGB565 into memory, the stub data. No window; it writes PNGs.
#
#   make lvgl          fetch LVGL v9.2.2 into .lvgl/ (once; or pass LVGL_DIR=...)
#   make test          logic, contrast, layout checks
#   make screenshots   every screen, both themes -> ../docs/screenshots/
#   make budget        pixels redrawn per frame, per screen
#
# Needs: cc, make, libpng (Debian/Ubuntu: libpng-dev).
LVGL_DIR ?= .lvgl
LVGL_TAG ?= v9.2.2
OUT ?= build
HH := ..

LVGL_SRC := $(shell find $(LVGL_DIR)/src -name '*.c' 2>/dev/null)
UI_SRC := $(wildcard $(HH)/main/ui/*.c) $(wildcard $(HH)/main/ui/fonts/*.c) \
          $(HH)/components/hhcore_stub/hh_stub.c sim_main.c

INC := -I. -I$(LVGL_DIR) -I$(HH)/main/ui -I$(HH)/components/hhcore_stub/include
DEFS := -DLV_CONF_INCLUDE_SIMPLE -DHH_SIM=1 $(EXTRA_DEFS)
CFLAGS_LVGL := -std=c11 -O2 -g $(INC) $(DEFS)
CFLAGS_UI := -std=c11 -D_POSIX_C_SOURCE=200809L -O2 -g -Wall -Wextra -Werror $(INC) $(DEFS)

LVGL_OBJ := $(patsubst $(LVGL_DIR)/%.c,$(OUT)/lvgl/%.o,$(LVGL_SRC))
UI_OBJ := $(patsubst %.c,$(OUT)/ui/%.o,$(notdir $(UI_SRC)))
vpath %.c $(sort $(dir $(UI_SRC)))

all: $(OUT)/hhsim

lvgl:
	test -d $(LVGL_DIR)/src || git clone --depth 1 -b $(LVGL_TAG) https://github.com/lvgl/lvgl.git $(LVGL_DIR)

$(OUT)/hhsim: $(LVGL_OBJ) $(UI_OBJ)
	@test -n "$(LVGL_SRC)" || { echo "no LVGL in $(LVGL_DIR): run 'make lvgl'"; exit 1; }
	$(CC) -o $@ $^ -lpng -lm

$(OUT)/lvgl/%.o: $(LVGL_DIR)/%.c lv_conf.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_LVGL) -c -o $@ $<

$(OUT)/ui/%.o: %.c lv_conf.h $(wildcard $(HH)/main/ui/*.h) $(wildcard $(HH)/components/hhcore_stub/include/*.h) $(wildcard $(HH)/components/hhcore/include/*.h)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_UI) -c -o $@ $<

test: $(OUT)/hhsim
	$(OUT)/hhsim test

screenshots: $(OUT)/hhsim
	$(OUT)/hhsim shots $(HH)/docs/screenshots

budget: $(OUT)/hhsim
	$(OUT)/hhsim budget

clean:
	rm -rf $(OUT)

.PHONY: all lvgl test screenshots budget clean
