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
