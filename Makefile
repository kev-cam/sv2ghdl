default: all

test_misc:
	cd tests ; ../sv2ghdl.pl -verbose -find . -d work

tests_atpg:
	$(MAKE) -C tests-atpg clean
	$(MAKE) -C tests-atpg all

all: test_misc tests_atpg
