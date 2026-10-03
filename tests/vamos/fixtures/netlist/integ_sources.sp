* vamos: every independent-source waveform through spice.parse (docs/VAMOS_AMS_DESIGN.md §4.3.8).
* HSPICE's defaults are resolved in the IR and every field is printed, so both engines must
* produce the same waveform, sample for sample.
.param vlo=0.1 vhi=0.9 per=10n
* PULSE without parentheses, parameter levels, explicit edges and a period
vp1 p1 0 pulse 'vlo' 'vhi' 1n 1n 2n 3n 'per'
* SIN with a delay, damping and a phase; frequency omitted on the second (1/TSTOP)
vs1 s1 0 sin(0.5 0.4 100meg 2n 1e7 90)
vs2 s2 0 sin(0 1)
* EXP with every field, and with tau2/td2 omitted (TSTEP, td1+TSTEP)
ve1 e1 0 exp(0 1 2n 1n 8n 2n)
ve2 e2 0 exp(1 0 3n 2n)
* PWL starting after 0 (HSPICE holds the DC value before it), with TD=
vw1 w1 0 dc 0.3 pwl(2n 0.3 4n 1 6n -0.5) td=1n
* PL: value-time pairs
vw2 w2 0 pl(0 0 1 5n 0.25 10n)
* a current source with a waveform into a resistor
iw i1 0 pulse(0 -1m 1n 0.5n 0.5n 4n 12n)
ri i1 0 1k
.tran 0.05n 20n
.print tran v(p1) v(s1) v(s2) v(e1) v(e2) v(w1) v(w2) v(i1)
.end
