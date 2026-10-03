* vamos e2e 25: .option scale applied once, and the default temperature (docs/VAMOS_AMS_DESIGN.md
* §4.3.5, §9).  Geometry is in microns; vamos keeps it unscaled in the IR and both emitters
* multiply it once.  Level 1 with LD=0.2u: Leff = 1u - 2*0.2u = 0.6u, so
* Id = kp/2 * W/Leff * (vgs-vto)^2 = 0.5e-4 * 2/0.6 * 0.25 = 4.1667e-5 A (unscaled it would be
* 2.5e-5 A).  No .temp: HSPICE runs at 25 C, the cards' tnom=25, so nothing is temperature
* shifted (an engine default of 27 C would move kp and vto).
.option scale=1e-6
.model n1 nmos level=1 vto=0.5 kp=1e-4 ld=0.2u tnom=25
.model n54 nmos level=54 version=4.8 toxe=2n vth0=0.4 u0=0.04 tnom=25
vg g 0 1
vd1 d1 0 1
m1 d1 g 0 0 n1 w=2 l=1
vd54 d54 0 1
m54 d54 g 0 0 n54 w=2 l=1 ad=2 as=2 pd=6 ps=6
* a LEVEL 1 diode: AREA is a unitless factor that .option scale does not touch
.model dlin d is=1e-14
vdl dl 0 0.6
ddl dl 0 dlin 2
.tran 0.1n 1n
.print tran i(vd1) i(vd54) i(vdl)
.end
