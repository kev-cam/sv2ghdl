* vamos e2e 23: PULSE(0 1 1n 0.1n 0.1n) holds at v2 (docs/VAMOS_AMS_DESIGN.md §4.3.8, §9):
* pw omitted -> TSTOP and no period, so after the rising edge the source stays at 1 V to
* the end of the run on both engines.  The second source spells out zero edges and a period.
v1 a 0 pulse(0 1 1n 0.1n 0.1n)
r1 a 0 1k
v2 b 0 pulse 0 1 2n 0 0 3n 10n
r2 b 0 1k
.tran 10p 20n
.print tran v(a) v(b)
.end
