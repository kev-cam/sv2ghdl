OPENVAF ?= /usr/local/src/OpenVAF/openvaf
CC     ?= gcc
CFLAGS  = -Wall -Wextra

all: test_resistor.osdi test_ngspice_osdi

test_resistor.osdi: test_resistor.va
	$(OPENVAF) $< -o $@

test_ngspice_osdi: test_ngspice_osdi.c
	$(CC) $(CFLAGS) -o $@ $< -lngspice

test: all
	LD_LIBRARY_PATH=/usr/local/lib ./test_ngspice_osdi

clean:
	rm -f test_resistor.osdi test_ngspice_osdi

.PHONY: all test clean
