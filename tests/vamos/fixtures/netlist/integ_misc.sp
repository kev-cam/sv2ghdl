* vamos: constructs that cross the parser/emitter seam: a top-level parameter that depends
* on temper (kept symbolic: R = 1k*(1+0.01*(50-25)) = 1.25k at .temp 50), a subckt defined
* inside another, and .ic on a node inside a subckt instance.
.temp 50
.param rt='1k*(1+0.01*(temper-25))'
vt t 0 1
rtemp t 0 'rt'
.subckt outer a
.subckt inner p
r1 p 0 2k
.ends inner
xi a inner
r2 a n 1meg
c2 n 0 1p
.ends outer
vo o 0 1
x1 o outer
.ic v(x1.n)=0.5
.tran 1n 10n
.print tran i(vt) i(vo) v(x1.n)
.end
