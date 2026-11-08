* vamos: a Verilog-A device through .hdl (docs/VAMOS_AMS_DESIGN.md §4.2, §4.4, §4.5, e2e 9).
* y1 instantiates the module directly with its default r=2000; y2 names a .model card of the
* module (r=500) and adds m=2; y3 overrides the card's r on the X line.  VACASK honours all
* three; Xyce takes no multiplier on a Verilog-A device, so vamos refuses y2 there (y3 runs, FIX2E8).
.hdl "emit_vres.va"
.model rcard vres r=500
v1 s1 0 1
x1 s1 0 vres
v2 s2 0 1
x2 s2 0 rcard m=2
v3 s3 0 1
x3 s3 0 rcard r=250
.tran 0.1n 1n
.print tran i(v1) i(v2) i(v3)
.end
