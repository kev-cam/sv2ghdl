Sky130 CMOS Inverter - Transient Simulation
.lib "../skywater-pdk-libs-sky130_fd_pr/combined_models/sky130.lib.spice" tt

.param wp=1 wn=0.65 lmin=0.15

Xp out in vdd vdd sky130_fd_pr__pfet_01v8 l=lmin w=wp
Xn out in 0 0 sky130_fd_pr__nfet_01v8 l=lmin w=wn

Vdd vdd 0 1.8
Vin in 0 PULSE(0 1.8 0 100p 100p 5n 10n)

.tran 100p 20n
.print tran v(in) v(out)
.end
