# Makefile for synthesizing RTL to gate-level netlists using Yosys
# Output goes to work/ subdirectory

VERILOG_FILES = $(wildcard *.v)
WORK_DIR = work

# Synthesized gate-level netlists
GATE_NETLISTS = $(patsubst %.v,work/%_syn.v,$(VERILOG_FILES))

# Default target
all: $(WORK_DIR) $(GATE_NETLISTS)

# Create work directory
$(WORK_DIR):
	@mkdir -p $(WORK_DIR)

# Synthesis rule: RTL -> gate-level netlist
$(WORK_DIR)/%_syn.v: %.v | $(WORK_DIR)
	@echo "Synthesizing $< -> $@"
	@yosys -q -p "read_verilog $<; \
	              hierarchy -auto-top; \
	              proc; opt; fsm; opt; memory; opt; \
	              techmap; opt; \
	              clean; \
	              write_verilog -noattr $@"

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
