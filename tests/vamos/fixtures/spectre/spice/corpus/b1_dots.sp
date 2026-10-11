* t
.dc V1 0 5 0.1
.ac dec 10 1 1meg
.tran 1ns 100ns 0 0.5ns uic
.global vdd gnd
.temp 27
.options reltol=1e-4
.print v(out) i(v1)
