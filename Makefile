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

# Yosys cycle-based state machine generator.  The source tree carries no
# generated headers (kernel/yosys_config.h) and no libyosys.so — both live
# in the CMake build tree, so it must be on the include/link paths too.
YOSYS_DIR ?= /usr/local/src/yosys
YOSYS_BUILD ?= /usr/local/src/yosys-build
YOSYS_CXXFLAGS = -std=c++20 -O2 -I$(YOSYS_DIR) -I$(YOSYS_BUILD) -I$(YOSYS_BUILD)/share/include -D_YOSYS_ -DYOSYS_ENABLE_READLINE=0 -DYOSYS_ENABLE_TCL=0 -DYOSYS_ENABLE_ABC -DYOSYS_ENABLE_GLOB -DYOSYS_ENABLE_ZLIB -DYOSYS_ENABLE_PLUGINS -fPIC
YOSYS_LDFLAGS = -L$(YOSYS_BUILD) -lyosys -Wl,-rpath,$(YOSYS_BUILD)

yosys/gen_statemachine: yosys/gen_statemachine.cpp
	g++ $(YOSYS_CXXFLAGS) -o $@ $< $(YOSYS_LDFLAGS)

gen_sm: yosys/gen_statemachine
	@echo "Usage: yosys/gen_statemachine <input.v> <top_module> <output.c>"

# Z3-based coverage solver
YOSYS_DIR ?= /usr/local/src/yosys
Z3_LDFLAGS = -lz3
yosys/cover_solve: yosys/cover_solve.cpp
	g++ -std=c++20 -O2 -I$(YOSYS_DIR) -D_YOSYS_ -DYOSYS_ENABLE_READLINE=0 -DYOSYS_ENABLE_TCL=0 -o $@ $< -L$(YOSYS_DIR) -lyosys -Wl,-rpath,$(YOSYS_DIR) $(Z3_LDFLAGS)
