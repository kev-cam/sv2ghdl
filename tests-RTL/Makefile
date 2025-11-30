# Makefile for synthesizing RTL to gate-level netlists using Yosys
# Output goes to work/ subdirectory

VERILOG_FILES = $(wildcard *.v)
WORK_DIR = work

# Synthesized gate-level netlists
GATE_NETLISTS = $(patsubst %.v,work/%_syn.v,$(VERILOG_FILES))
BENCH_FILES = $(patsubst %.v,work/%_syn.bench,$(VERILOG_FILES))
TEST_FILES = $(patsubst %.v,work/%_syn.test,$(VERILOG_FILES))

# Tool paths
V2B = verilog2bench.pl
ATALANTA = atalanta

# Default target
all: $(WORK_DIR) $(GATE_NETLISTS)

# Create work directory
$(WORK_DIR):
	@mkdir -p $(WORK_DIR)

# Synthesis rule: RTL -> gate-level netlist
$(WORK_DIR)/%_syn.v: %.v | $(WORK_DIR)
	@echo "Synthesizing $< -> $@-tmp"
	@yosys -q -p "read_verilog $<; \
	              hierarchy -auto-top; \
	              synth -flatten; \
	              abc -g AND,OR,NAND,NOR; \
	              clean; \
	              write_verilog -noattr -noexpr $@-tmp"
	./yo-fix.pl < $@-tmp >$@

# Convert gate-level netlist to BENCH format
$(WORK_DIR)/%_syn.bench: $(WORK_DIR)/%_syn.v
	@echo "Converting $< -> $@"
	@$(V2B) $< $@

# Run Atalanta ATPG on BENCH file
.PRECIOUS: $(WORK_DIR)/%_syn.bench
$(WORK_DIR)/%_syn.test: $(WORK_DIR)/%_syn.bench
	@echo "Running Atalanta on $<"
	@cd $(WORK_DIR) && $(ATALANTA) $(notdir $(basename $<))
	@if [ -f $(WORK_DIR)/$(notdir $(basename $<)).test ]; then \
		mv $(WORK_DIR)/$(notdir $(basename $<)).test $@; \
	else \
		echo "Error: Atalanta did not generate test file"; \
		exit 1; \
	fi

# Targets for different stages
bench: $(BENCH_FILES)

atpg: $(TEST_FILES)

# Show what will be built
show:
	@echo "RTL files: $(VERILOG_FILES)"
	@echo "Gate netlists: $(GATE_NETLISTS)"

# Clean work directory
clean:
	rm -rf $(WORK_DIR)

# Phony targets
.PHONY: all clean show

# Help target
help:
	@echo "Makefile for RTL synthesis using Yosys"
	@echo ""
	@echo "Targets:"
	@echo "  all    - Synthesize all RTL files to gate-level (default)"
	@echo "  show   - Show files to be processed"
	@echo "  clean  - Remove work directory"
	@echo "  help   - Show this help message"
	@echo ""
	@echo "RTL files: $(VERILOG_FILES)"
	@echo ""
	@echo "Output directory: $(WORK_DIR)/"
	@echo "  *_syn.v - Synthesized gate-level netlists"
