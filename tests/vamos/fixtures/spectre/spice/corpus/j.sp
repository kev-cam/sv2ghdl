* t
.subckt buf a y
.param g=2
R1 a y {g*1k}
V1 y 0 DC 0
.ends buf
Xinst n1 n2 buf
