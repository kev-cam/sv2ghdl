* vamos e2e 21: cross-engine parameter deck (docs/VAMOS_AMS_DESIGN.md §9), parsed with
* --vamos-parhier=local.  Every value below is a node voltage or a source current, and
* both engines must give the HSPICE value.

* -- parameter rules (§4.3.3) ---------------------------------------------------
* duplicate: the last definition wins for every use, the earlier ones included
.param dup=1
vdup ndup 0 'dup'
.param dup=3
* an out-of-order chain
.param c1='b1*2'
.param b1='a1+1'
.param a1=1
vchain nchain 0 'c1'
* a top-level/subckt collision (an error under PARHIER=GLOBAL; local: the inner wins)
.param wcol=1
.subckt colcell a wcol=2
r1 a 0 'wcol*1k'
.ends colcell
vcol ncol 0 1
xcol ncol colcell

* -- HSPICE built-ins: parameter context (folded by vamos, printed as numbers) --
* (and HSPICE's spelling of the same values through parameters, so the printers see names)
.param two=2 m10=-10 m2=-2 m4=-4 m25=-2.5 zero=0
vp1 np1 0 'pow(two,1.5)'
vp2 np2 0 'sgn(zero)'
vp3 np3 0 'db(m10)'
vp4 np4 0 'log(m2)'
vp5 np5 0 'sqrt(m4)'
vp6 np6 0 'nint(m25)'
vp7 np7 0 'pwr(m2,0.5)'
vp8 np8 0 'm2**3+(m2)**two+two**1.5'
vp9 np9 0 'int(m25)+abs(m25)'
vp10 np10 0 'sign(m4,zero)+sign(two,m10)'
vp11 np11 0 'atan2(1,m2)'
vp12 np12 0 'if(zero,1,2)+limit(m4,-1,1)+max(m2,two,1)'

* -- the same built-ins in behavioral sources (node-dependent arguments) ------
vc2 c2 0 2
vc0 c0 0 0
vcm10 cm10 0 -10
vcm2 cm2 0 -2
vcm4 cm4 0 -4
vcm25 cm25 0 -2.5
eb1 nb1 0 vol='pow(v(c2),1.5)'
eb2 nb2 0 vol='sgn(v(c0))'
eb3 nb3 0 vol='db(v(cm10))'
eb4 nb4 0 vol='log(v(cm2))'
eb5 nb5 0 vol='sqrt(v(cm4))'
eb6 nb6 0 vol='nint(v(cm25))'
eb7 nb7 0 vol='pwr(v(cm2),0.5)'
eb8 nb8 0 vol='v(cm2)**3+v(cm2)**v(c2)+v(c2)**1.5'
eb9 nb9 0 vol='int(v(cm25))+abs(v(cm25))'
eb10 nb10 0 vol='sign(v(cm4),v(c0))+sign(v(c2),v(cm10))'
eb11 nb11 0 vol='atan2(1,v(cm2))'
eb12 nb12 0 vol='if(v(c0),1,2)+limit(v(cm4),-1,1)+max(v(cm2),v(c2),1)'

* -- the multiplier (§4.3.7) ----------------------------------------------------
.subckt rleaf x
r1 x 0 1k
.ends rleaf
.subckt rmid x
xi x rleaf m=2
.ends rmid
vx nx 0 1
x1 nx rmid m=3
.global s
vs s 0 1
.subckt gsub x
g1 0 x cur='1m*v(s)'
.ends gsub
x3 nz gsub m=3
rz nz 0 1k

* -- polarity and models (§4.3.6) ----------------------------------------------
.model qp pnp is=1e-15 bf=100
vcc vcc 0 5
rb b 0 430k
rc c 0 1k
q1 c b vcc qp
.model n3 nmos level=49 version=3.1 tox=4e-9 vth0=0.4
vd3 d3 0 1
vg3 g3 0 1
m3 d3 g3 0 0 n3 w=1u l=0.18u

.tran 1n 10n
.print tran v(ndup) v(nchain) i(vcol) v(np1) v(np2) v(np3) v(np4) v(np5) v(np6) v(np7) v(np8)
+ v(np9) v(np10) v(np11) v(np12) v(nb1) v(nb2) v(nb3) v(nb4) v(nb5) v(nb6) v(nb7) v(nb8) v(nb9)
+ v(nb10) v(nb11) v(nb12) i(vx) v(nz) v(c) i(vd3)
.end
