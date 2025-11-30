# Makefile for running Atalanta ATPG on logic gates
# Output goes to work/ subdirectory

GATES = and_gate or_gate nand_gate nor_gate xor_gate not_gate
VERILOG_FILES = $(addsuffix .v, $(GATES))
WORK_DIR = work

# Atalanta output files in work directory
ATPG_RESULTS = $(addprefix $(WORK_DIR)/, $(addsuffix .patterns, $(GATES)))
FAULT_LISTS = $(addprefix $(WORK_DIR)/, $(addsuffix .faults, $(GATES)))

# Default target
all: $(WORK_DIR) $(ATPG_RESULTS)

# Create work directory
$(WORK_DIR):
	@mkdir -p $(WORK_DIR)

# Pattern rule for running Atalanta on each gate
# Atalanta typical usage: atalanta [options] <input.v>
# Common options:
#   -t : Generate test patterns
#   -f : Generate fault list
#   -D : Diagnostic mode
$(WORK_DIR)/%.patterns: %.v | $(WORK_DIR)
	@echo "Running Atalanta ATPG on $<..."
	@atalanta -t $(WORK_DIR)/$*.patterns -f $(WORK_DIR)/$*.faults $< 2>&1 | tee $(WORK_DIR)/$*.log || \
		echo "Note: Atalanta may not be installed. Skipping $<"

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
