# Host build of the OTA core.
# The same sources are compiled by Keil/CubeIDE for the STM32 target;
# only src/port/host/* gets replaced by the platform port there.

CC       ?= cc
CFLAGS   ?= -std=c99 -O2 -g -Wall -Wextra
CPPFLAGS += -Iinclude -Isrc/port/host
BUILD    := build

CORE_SRC := $(wildcard src/core/*.c)
PORT_SRC := $(wildcard src/port/host/*.c)
TEST_SRC := $(wildcard tests/*.c)

OBJS := $(patsubst %.c,$(BUILD)/%.o,$(CORE_SRC) $(PORT_SRC) $(TEST_SRC))
BIN  := $(BUILD)/ota_tests

all: $(BIN)

$(BIN): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^

$(BUILD)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(CPPFLAGS) -c -o $@ $<

test: $(BIN)
	./$(BIN)

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
