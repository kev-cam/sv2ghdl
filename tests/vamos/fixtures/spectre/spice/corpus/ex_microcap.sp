* MicroCap Library Test Cases
* Test subcircuits with numerical names (common in MicroCap libraries)

* Kemet ceramic capacitor model from MicroCap
.SUBCKT 03063C102KAT 1 2
C1 1 2 1n
R1 1 2 1G
.ENDS 03063C102KAT

* Another numerical subcircuit name
.SUBCKT 1N4148 A K
D1 A K DMOD
.MODEL DMOD D(IS=2.52e-9 RS=0.568)
.ENDS 1N4148

* Mixed alphanumeric starting with digit
.SUBCKT 2N2222A C B E
Q1 C B E QMOD
.MODEL QMOD NPN(BF=100)
.ENDS 2N2222A

* Test instantiation of numerical subcircuits
X1 VCC GND 03063C102KAT
X2 NET1 NET2 1N4148
X3 VCC NET3 GND 2N2222A

* Standard voltage source for completeness
VCC VCC GND 5V

* Test POLY expressions for voltage and current controlled sources
EOS 7 1 POLY(1) 16 49 2E-3 1
F6 50 99 POLY(1) V6 300U 1
GD16 16 1 TABLE {V(16,1)} ((-100,-1p)(0,0)(1m,1u)(2m,1m))

.END