default: all

test_misc:
	cd tests ;      ../sv2ghdl.pl -verbose -find . -d work

test_atpg:
	cd tests-atpg ; ../sv2ghdl.pl -verbose -find . -d work
	$(MAKE) -C tests-atpg all

all: test_misc test_atpg
