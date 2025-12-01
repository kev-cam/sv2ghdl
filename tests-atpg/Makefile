# Makefile for running Atalanta ATPG on logic gates
# Output goes to work/ subdirectory

VERILOG_FILES = $(wildcard *.v)
WORK_DIR = work

# Atalanta output files in work directory
FAULT_VEC = $(patsubst %.v,work/%.tst,$(VERILOG_FILES))
VHDL_CASE = $(patsubst %.v,work/%.vhd,$(VERILOG_FILES))
VHD_TESTS = $(patsubst %.v,work/%_tb.vhd,$(VERILOG_FILES))
TEST_LOGS = $(patsubst %.v,work/%.run,$(VERILOG_FILES))

fault_tests: $(FAULT_VEC)

# Default target
all: $(WORK_DIR) vhdl fault_tests $(VHD_TESTS) $(TEST_LOGS) summary

vhdl:
	../sv2ghdl.pl -verbose -find . -d work

summary:
	./compare_results.pl

show:
	@echo Verilog = $(VERILOG_FILES)
	@echo Fault tests = $(FAULT_VEC)

V2B=verilog2bench.pl
ATALANTA=atalanta

# Create work directory
$(WORK_DIR):
	@mkdir -p $(WORK_DIR)

$(WORK_DIR)/%.bench: %.v | $(WORK_DIR)
	$(V2B) $< $@

.PRECIOUS: $(WORK_DIR)/%.bench
%.tst : %.bench 
	cd $$(dirname $@) ; $(ATALANTA) $$(basename $<)
	TEST=$(patsubst %.tst,%.test,$@) ; if [ -s $$TEST ] ; then mv $$TEST $@ ; else exit 1 ; fi

%_tb.vhd: %.tst
	cd $$(dirname $@) ; ../test2vhdl.pl $$(basename $<)

%.run: %_tb.vhd
	ghdl -a $< $(subst _tb,,$<)
	ghdl -r  $(patsubst $(WORK_DIR)/%.vhd,%,$<) > $@

# Clean work directory
clean:
	rm -rf $(WORK_DIR)

# Phony targets
.PHONY: all clean

# Help target
help:
	@echo "Makefile for Atalanta ATPG on logic gates"
	@echo ""
	@echo "Targets:"
	@echo "  all    - Run Atalanta on all gates (default)"
	@echo "  clean  - Remove work directory"
	@echo "  help   - Show this help message"
	@echo ""
	@echo "Gates: $(GATES)"
	@echo ""
	@echo "Output directory: $(WORK_DIR)/"
	@echo "  *.patterns - Test patterns"
	@echo "  *.faults   - Fault lists"
	@echo "  *.log      - Atalanta output logs"
