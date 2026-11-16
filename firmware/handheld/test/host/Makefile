# Host build of the handheld's portable core (components/hhcore).
#
#   make          build, then run the C unit tests (non-zero exit if one fails)
#   make lib      only the shared library the Python tests load (tests/test_hh_*.py)
#   make hmtest   only the road map reader and renderer (components/hhmap)
#
# -ffp-contract=off: the planner and scheduler are compared with the Python
# bit for bit; a fused multiply-add would round differently.
CORE := ../../components/hhcore
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -fPIC -ffp-contract=off -D_DEFAULT_SOURCE -I$(CORE)/include
SRC := $(CORE)/hh_proto.c $(CORE)/hh_link.c $(CORE)/hh_plan.c $(CORE)/hh_expr.c $(CORE)/hh_map.c \
       $(CORE)/hh_alarm.c $(CORE)/hh_iso.c $(CORE)/hh_nmea.c $(CORE)/hh_log.c $(CORE)/hh_core.c \
       $(CORE)/hh_text.c $(CORE)/hh_serial.c $(CORE)/port/hh_serfs.c hhhost.c
MAP := ../../components/hhmap
MAP_SRC := $(MAP)/hm_format.c $(MAP)/hm_store.c $(MAP)/hm_render.c
MAP_CFLAGS := -std=c11 -O1 -g -Wall -Wextra -Werror -Wshadow -Wconversion -Wno-sign-conversion -D_DEFAULT_SOURCE -I$(MAP)/include
DEPS := $(CORE)/include/hhcore.h $(CORE)/include/hh_data.h $(CORE)/include/hh_serial.h $(CORE)/port/hh_default_map.inc hhhost.h hhsim.h
OUT ?= build

all: lib test

lib: $(OUT)/libhhhost.so

$(OUT)/libhhhost.so: $(SRC) $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -shared -o $@ $(SRC) -lm

$(OUT)/hhtest: $(SRC) hhsim.c hhtest.c $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC) hhsim.c hhtest.c -lm

# the laptop link (hh_serial.c): framing, the ring and its drop policy, the stream, the commands
$(OUT)/hhsertest: $(SRC) hhsim.c hhsertest.c $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC) hhsim.c hhsertest.c -lm

# the same file built as the GPS-on-P1 firmware builds it: the command side is not there
$(OUT)/hh_serial_txonly.o: $(CORE)/hh_serial.c $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -DHH_GPS_P1=1 -c -o $@ $(CORE)/hh_serial.c
	@if nm $@ | grep -q ' T hh_serial_rx'; then echo "hh_serial_rx is in the tx-only build"; rm -f $@; exit 1; fi

# the offline road map (components/hhmap) against a MAP the Python builder made (fixtures/tinymap)
$(OUT)/hmtest: $(MAP_SRC) hmtest.c $(MAP)/include/hhmap.h
	mkdir -p $(OUT)
	$(CC) $(MAP_CFLAGS) -o $@ $(MAP_SRC) hmtest.c -lm

test: $(OUT)/hhtest $(OUT)/hhsertest $(OUT)/hh_serial_txonly.o $(OUT)/hmtest
	$(OUT)/hhtest
	$(OUT)/hhsertest
	$(OUT)/hmtest

clean:
	rm -rf $(OUT)

.PHONY: all lib test clean
