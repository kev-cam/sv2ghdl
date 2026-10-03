* vamos: .option scale on a geometric (LEVEL 3) diode, VACASK only (Xyce has no LEVEL 3
* diode).  W, L and AREA scale (AREA by scale^2); IS is per square metre.  All three diodes
* have IS*area = 1e-12 A, so the three currents are equal.
.option scale=1e-6
.model dgeo d level=3 is=1e-2
.model dlin d is=1e-12
vg g 0 0.5
dg g 0 dgeo w=10 l=10
vh h 0 0.5
dh h 0 dgeo area=100
vr r 0 0.5
dr r 0 dlin
.tran 0.1n 1n
.print tran i(vg) i(vh) i(vr)
.end
