* vamos §9 netlist unit deck: the control values are parameters.  Every VACASK
* control-block value and the Xyce .options device temp= value must be an evaluated float.
.param tsim=10n tt=50 vh=0.7
.tran 1n 'tsim'
.temp tt
.ic v(b)='vh'
* the .ic node: an RC released from 0.7 V at t=0 (tau = 1 us, so ~0.7 V over the run)
vs a 0 0
r1 a b 1meg
c1 b 0 1p
* the temperature: R(T) = 1k * (1 + 0.01 * (50 - 25)) = 1.25k, so v(c) = 1.25 V
i2 0 c 1m
r2 c 0 1k tc1=0.01
.print tran v(b) v(c)
.end
