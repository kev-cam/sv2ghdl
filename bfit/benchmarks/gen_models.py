#!/usr/bin/env python3
"""gen_models.py -- emit a small, portable, varied model suite for the perf table.

Each netlist uses only the universally-portable SPICE subset (R/L/C/V, MOSFET
LEVEL=1, standard NPN/diode, SIN/PULSE sources, .tran) so the same file runs on
QSPICE, LTspice, ngspice and Xyce unmodified. Writes <name>.cir into the target
dir (default: the perfbench scratch dir).

Usage: gen_models.py [outdir]

BEST-EFFORT methodology: each .tran uses a SENSIBLE output step (~signal_period/50),
not an artificially fine tstep. A fine tstep only handicaps ngspice (it honours tstep
as a step ceiling; QSPICE and Xyce ignore it and stride), which silently inflated the
old analog "speedups". Fine steps are kept ONLY where physics demands them -- the
digital decks (inv_chain, ring_osc), whose edges force them regardless. Don't game the
numbers: smooth analog circuits are intrinsically fast (startup-bound) on QSPICE/Xyce
and stay that way; bfit's real win shows only on the physics-stiff/digital circuits.
"""
import os, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/mnt/c/cygwin64/tmp/perfbench/models"
os.makedirs(OUT, exist_ok=True)

MODELS = {}

# ---- 2. full-wave bridge rectifier (0 transistors, 4 diodes) ---------------
MODELS["rectifier"] = """\
* Full-wave bridge rectifier into an RC load (4 diodes)
Vac a b SIN(0 12 60)
D1 a out DM
D2 b out DM
D3 0 a DM
D4 0 b DM
Rload out 0 470
Cload out 0 220u
Rbleed a b 1meg
.model DM D(IS=1e-14 RS=0.05 N=1.2 CJO=20p)
.tran 200u 480
.end
"""

# ---- 3. CMOS inverter chain (digital) --------------------------------------
def cmos_chain(n, ring=False):
    L = ["* CMOS inverter %s, %d stages, MOSFET LEVEL=1 (%d transistors)"
         % ("ring oscillator" if ring else "chain", n, 2 * n),
         ".model NM NMOS (LEVEL=1 VTO=0.6 KP=150u LAMBDA=0.02 GAMMA=0.4 PHI=0.65)",
         ".model PM PMOS (LEVEL=1 VTO=-0.6 KP=50u LAMBDA=0.02 GAMMA=0.4 PHI=0.65)",
         "Vdd vdd 0 3.3",
         ".subckt inv a y vdd",
         "Mn y a 0 0 NM W=2u L=0.35u",
         "Mp y a vdd vdd PM W=5u L=0.35u",
         "Cl y 0 8f",
         ".ends"]
    if ring:
        nodes = ["n%d" % k for k in range(n)]
        for k in range(n):
            L.append("Xi%d %s %s vdd inv" % (k, nodes[k], nodes[(k + 1) % n]))
        L.append(".ic v(n0)=3.3")
        L.append(".tran 0.05n 200n 0 0.05n")
    else:
        L.append("Vin in 0 PULSE(0 3.3 1n 0.5n 0.5n 20n 40n)")
        prev = "in"
        for k in range(1, n + 1):
            nn = "out" if k == n else "n%d" % k
            L.append("Xi%d %s %s vdd inv" % (k, prev, nn)); prev = nn
        L.append(".tran 0.05n 600n 0 0.05n")
    L.append(".end")
    return "\n".join(L) + "\n"

MODELS["inv_chain"] = cmos_chain(100, ring=False)
MODELS["ring_osc"]  = cmos_chain(51, ring=True)

# ---- 4. 5-transistor OTA: diff pair + current-mirror load ------------------
MODELS["ota_5t"] = """\
* 5T OTA -- NMOS diff pair, PMOS current-mirror load, NMOS tail (5 transistors)
.model NM NMOS (LEVEL=1 VTO=0.6 KP=150u LAMBDA=0.02 GAMMA=0.4 PHI=0.65)
.model PM PMOS (LEVEL=1 VTO=-0.6 KP=50u LAMBDA=0.02 GAMMA=0.4 PHI=0.65)
Vdd  vdd 0 3.3
Vcm  cm  0 1.65
Bsig inp cm V={0.05*(sin(6.2832*0.4e6*time)+sin(6.2832*1e6*time)+sin(6.2832*2.3e6*time))}
Vbias gb 0 0.95
* tail current source
Mtail tail gb 0 0 NM W=20u L=0.5u
* NMOS differential pair (inn tied to common-mode)
M1 d1 inp tail 0 NM W=10u L=0.5u
M2 out cm  tail 0 NM W=10u L=0.5u
* PMOS current-mirror load
M3 d1 d1 vdd vdd PM W=10u L=0.5u
M4 out d1 vdd vdd PM W=10u L=0.5u
Cl out 0 0.5p
.tran 20n 24m
.end
"""

# ---- 5. BJT 3-stage CE amplifier (bfit recognizes this one) ----------------
MODELS["bjt_amp"] = """\
* 3-stage common-emitter BJT amplifier (bfit ce_stage pattern, 3 transistors)
Vcc vcc 0 12
Bin in 0 V={0.005*(sin(6.2832*5e3*time)+sin(6.2832*13e3*time)+sin(6.2832*27e3*time))}
Cin in b1 1u
Rc1 vcc c1 4.7k
Rb1 vcc b1 100k
Re1 e1 0 470
Ce1 e1 0 10u
Q1 c1 b1 e1 QN
Cc1 c1 b2 1u
Rc2 vcc c2 4.7k
Rb2 vcc b2 100k
Re2 e2 0 470
Ce2 e2 0 10u
Q2 c2 b2 e2 QN
Cc2 c2 b3 1u
Rc3 vcc c3 4.7k
Rb3 vcc b3 100k
Re3 e3 0 470
Ce3 e3 0 10u
Q3 c3 b3 e3 QN
Cout c3 out 1u
Rload out 0 10k
.model QN NPN(BF=200 IS=1e-14 VAF=100 RB=10 RC=1 RE=0.5 CJC=3p CJE=8p TF=0.4n TR=10n)
.tran 2u 1.2
.end
"""

# ---- 6. 2-stage Miller CMOS op-amp, unity-gain follower ---------------------
# Several current mirrors of both polarities: an NMOS bias bank (one reference
# fans out to the tail + 2nd-stage sink) and a PMOS load mirror. The forced fine
# .tran step is what bfit's smooth mirror macromodels let the solver relax.
MODELS["opamp"] = """\
* 2-stage Miller CMOS op-amp, unity-gain follower (NMOS bias bank + PMOS load mirror)
.model NM NMOS (LEVEL=1 VTO=0.6 KP=150u LAMBDA=0.02)
.model PM PMOS (LEVEL=1 VTO=-0.6 KP=50u LAMBDA=0.02)
Vdd vdd 0 3.3
Iref vdd nbias 30u
Mbias nbias nbias 0 0 NM W=10u L=1u
M5 tail nbias 0 0 NM W=20u L=1u
M7 no nbias 0 0 NM W=40u L=1u
M1 n1 no  tail 0 NM W=20u L=1u
M2 n2 inp tail 0 NM W=20u L=1u
M3 n1 n1 vdd vdd PM W=20u L=1u
M4 n2 n1 vdd vdd PM W=20u L=1u
M6 no n2 vdd vdd PM W=80u L=1u
Cc n2 no 2p
Cl no 0 1p
Bin inp 0 V={1.35+0.05*(sin(6.2832*100e3*time)+sin(6.2832*270e3*time)+sin(6.2832*730e3*time))}
.tran 30n 210m
.end
"""

for name, txt in MODELS.items():
    with open(os.path.join(OUT, name + ".cir"), "w") as f:
        f.write(txt)
    ntx = txt.splitlines()[0]
    print("wrote %-14s %s" % (name + ".cir", ntx))
