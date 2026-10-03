* vamos e2e 22: a binned BSIM4 deck (docs/VAMOS_AMS_DESIGN.md §4.3.6, §9).  Bins split at
* L = 0.22u and W = 1u; each binned device has a reference device on the plain card of
* the bin it must select, so equal currents prove the selection on each engine.
.model nch.1 nmos level=54 lmin=0.1u lmax=0.22u wmin=0.1u wmax=1u vth0=0.30 version=4.8 toxe=2n u0=0.04
.model nch.2 nmos level=54 lmin=0.1u lmax=0.22u wmin=1u wmax=100u vth0=0.60 version=4.8 toxe=2n u0=0.04
.model nch.3 nmos level=54 lmin=0.22u lmax=10u wmin=0.1u wmax=1u vth0=0.45 version=4.8 toxe=2n u0=0.04
.model nch.4 nmos level=54 lmin=0.22u lmax=10u wmin=1u wmax=100u vth0=0.50 version=4.8 toxe=2n u0=0.04
.model ref1 nmos level=54 vth0=0.30 version=4.8 toxe=2n u0=0.04
.model ref2 nmos level=54 vth0=0.60 version=4.8 toxe=2n u0=0.04
.model ref3 nmos level=54 vth0=0.45 version=4.8 toxe=2n u0=0.04
.model ref4 nmos level=54 vth0=0.50 version=4.8 toxe=2n u0=0.04
vg g 0 1

* w=1.6u nf=2: total W 1.6u is bin 2, the per-finger 0.8u is bin 1 (vamos's rule on both engines)
vd1 d1 0 1
m1 d1 g 0 0 nch w=1.6u l=0.2u nf=2 ad=1p as=1p
vr1 r1 0 1
mr1 r1 g 0 0 ref1 w=1.6u l=0.2u nf=2 ad=1p as=1p

* l = 0.22*1e-6 = 2.1999999999999998e-07, a hair below the 2.2e-7 edge: bins 2 and (through
* the 1e-15 lower-bound tolerance) 4 both hold it; the first in Xyce's order, bin 2, wins
vd2 d2 0 1
m2 d2 g 0 0 nch w=2u l='0.22*1e-6'
vr2 r2 0 1
mr2 r2 g 0 0 ref2 w=2u l='0.22*1e-6'

* no ad/as: BSIM4 computes them
vd3 d3 0 1
m3 d3 g 0 0 nch w=0.5u l=1u
vr3 r3 0 1
mr3 r3 g 0 0 ref3 w=0.5u l=1u

* a sky130-style wrapper: the device reads l, w and nf from subckt parameters; on Xyce
* each instance path is evaluated and bound to its bin (one subckt copy per binding)
.subckt fet d g s b l=1u w=1u nf=1
mfet d g s b nch l={l} w={w} nf={nf}
.ends fet
vd4 d4 0 1
x4 d4 g 0 0 fet w=1.6u l=0.2u nf=2
vr4 r4 0 1
mr4 r4 g 0 0 ref1 w=1.6u l=0.2u nf=2
vd5 d5 0 1
x5 d5 g 0 0 fet w=1.6u l=0.5u nf=2
vr5 r5 0 1
mr5 r5 g 0 0 ref3 w=1.6u l=0.5u nf=2
vd6 d6 0 1
x6 d6 g 0 0 fet w=3u l=0.5u
vr6 r6 0 1
mr6 r6 g 0 0 ref4 w=3u l=0.5u

.tran 0.1n 1n
.print tran i(vd1) i(vr1) i(vd2) i(vr2) i(vd3) i(vr3) i(vd4) i(vr4) i(vd5) i(vr5) i(vd6) i(vr6)
.end
