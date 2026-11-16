# Host build of the handheld's portable core (components/hhcore).
#
#   make          build, then run the C unit tests (non-zero exit if one fails)
#   make lib      only the shared library the Python tests load (tests/test_hh_*.py)
#
# -ffp-contract=off: the planner and scheduler are compared with the Python
# bit for bit; a fused multiply-add would round differently.
CORE := ../../components/hhcore
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -fPIC -ffp-contract=off -D_DEFAULT_SOURCE -I$(CORE)/include
SRC := $(CORE)/hh_proto.c $(CORE)/hh_link.c $(CORE)/hh_plan.c $(CORE)/hh_expr.c $(CORE)/hh_map.c \
       $(CORE)/hh_alarm.c $(CORE)/hh_iso.c $(CORE)/hh_nmea.c $(CORE)/hh_log.c $(CORE)/hh_core.c \
       $(CORE)/hh_text.c hhhost.c
DEPS := $(CORE)/include/hhcore.h $(CORE)/include/hh_data.h $(CORE)/port/hh_default_map.inc hhhost.h hhsim.h
OUT ?= build

all: lib test

lib: $(OUT)/libhhhost.so

$(OUT)/libhhhost.so: $(SRC) $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -shared -o $@ $(SRC) -lm

$(OUT)/hhtest: $(SRC) hhsim.c hhtest.c $(DEPS)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC) hhsim.c hhtest.c -lm

test: $(OUT)/hhtest
	$(OUT)/hhtest

clean:
	rm -rf $(OUT)

.PHONY: all lib test clean
