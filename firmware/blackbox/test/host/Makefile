# Host build of the black box core: a shared library for the Python tests
# (tests/test_bb_*.py load it with ctypes) and a small C self-check.
CORE := ../../components/bbcore
CFLAGS ?= -std=c11 -O1 -g -Wall -Wextra -Werror -fPIC -I$(CORE)/include
SRC := $(CORE)/bb_util.c $(CORE)/bb_trigger.c $(CORE)/bb_engine.c bbhost.c
OUT ?= build

all: $(OUT)/libbbhost.so

$(OUT)/libbbhost.so: $(SRC) $(CORE)/include/bbcore.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -shared -o $@ $(SRC)

clean:
	rm -rf $(OUT)

.PHONY: all clean
