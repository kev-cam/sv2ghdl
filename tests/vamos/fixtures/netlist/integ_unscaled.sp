* vamos e2e 25, the reference: integ_scale.sp written in metres without .option scale.
.model n1 nmos level=1 vto=0.5 kp=1e-4 ld=0.2u tnom=25
.model n54 nmos level=54 version=4.8 toxe=2n vth0=0.4 u0=0.04 tnom=25
vg g 0 1
vd1 d1 0 1
m1 d1 g 0 0 n1 w=2u l=1u
vd54 d54 0 1
m54 d54 g 0 0 n54 w=2u l=1u ad=2p as=2p pd=6u ps=6u
* a LEVEL 1 diode: AREA is a unitless factor that .option scale does not touch
.model dlin d is=1e-14
vdl dl 0 0.6
ddl dl 0 dlin 2
.tran 0.1n 1n
.print tran i(vd1) i(vd54) i(vdl)
.end
