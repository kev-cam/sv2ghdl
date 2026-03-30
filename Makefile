default: all

test_misc:
	cd tests ; ../sv2ghdl.pl -verbose -find . -d work

tests_atpg:
	$(MAKE) -C tests-atpg clean
	$(MAKE) -C tests-atpg all

IVTEST_DIR ?= /usr/local/src/iverilog/ivtest

tests_ivl:
	cd $(IVTEST_DIR) && perl vvp_reg.pl --suffix=-sv2ghdl 2>&1 | tee /tmp/ivtest_results.txt
	@echo "--- Summary ---"
	@tail -5 /tmp/ivtest_results.txt

all: test_misc tests_atpg

# Yosys cycle-based state machine generator
YOSYS_DIR ?= /usr/local/src/yosys
YOSYS_CXXFLAGS = -std=c++17 -O2 -I$(YOSYS_DIR) -D_YOSYS_ -DYOSYS_ENABLE_READLINE=0 -DYOSYS_ENABLE_TCL=0
YOSYS_LDFLAGS = -L$(YOSYS_DIR) -lyosys -Wl,-rpath,$(YOSYS_DIR)

yosys/gen_statemachine: yosys/gen_statemachine.cpp
	g++ $(YOSYS_CXXFLAGS) -o $@ $< $(YOSYS_LDFLAGS)

gen_sm: yosys/gen_statemachine
	@echo "Usage: yosys/gen_statemachine <input.v> <top_module> <output.c>"
