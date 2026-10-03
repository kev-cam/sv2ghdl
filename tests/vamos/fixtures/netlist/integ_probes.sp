* vamos: i() probes of every element kind VACASK can save (§4.7 Saves), at top level and
* inside a subckt.  Both engines must report the same current with the same sign (SPICE's:
* positive into the first terminal through the element).
v1 a 0 1
r1 a b 1k
l1 b c 1n
rc c 0 1k
e1 e 0 b 0 2
re e 0 1k
h1 h 0 v1 1k
rh h 0 1k
eb f 0 vol='v(b)*0.5'
rf f 0 1k
.subckt cell p
rx p 0 2k
vx p q 0
rq q 0 2k
.ends cell
x1 a cell
.tran 0.1n 1n
.print tran i(v1) i(r1) i(l1) i(e1) i(h1) i(eb) i(x1.rx) i(x1.vx) v(a,b) v(*)
.end
