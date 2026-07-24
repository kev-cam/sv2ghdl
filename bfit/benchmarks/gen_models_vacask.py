#!/usr/bin/env python3
"""gen_models_vacask.py -- VACASK ports of the perf-league model suite
(gen_models.py) so VACASK gets a column in perf.md. Same topology / values /
.tran spec as the SPICE decks; only the syntax differs:
  - MOSFET LEVEL=1 -> sp_mos1   (spice/mos1.osdi)
  - diode          -> sp_diode  (spice/diode.osdi)
  - NPN            -> sp_bjt     (spice/bjt.osdi)
  - B-source multitone  -> series ideal sine vsources (electrically identical)
Writes <name>.sim into outdir.  Usage: gen_models_vacask.py [outdir] [devdir]
"""
import sys, os

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/vcmodels"
DEV = sys.argv[2] if len(sys.argv) > 2 else "/opt/build.VACASK/Release/devices"
os.makedirs(OUT, exist_ok=True)

HDR = {
 "dio": 'load "%s/spice/diode.osdi"' % DEV,
 "mos": 'load "%s/spice/mos1.osdi"' % DEV,
 "bjt": 'load "%s/spice/bjt.osdi"' % DEV,
 "r":   'load "%s/resistor.osdi"' % DEV,
 "c":   'load "%s/capacitor.osdi"' % DEV,
}
def head(*keys):
    L = [HDR[k] for k in keys]
    L += ["model v vsource", "model i isource"]
    if "r" in keys: L.append("model r resistor")
    if "c" in keys: L.append("model c capacitor")
    return L

M = {}

# ---- rectifier (4 diodes) --------------------------------------------------
M["rectifier"] = "\n".join(
 ["Full-wave bridge rectifier into an RC load (4 diodes)"] + head("dio","r","c") +
 ["model dm sp_diode ( is=1e-14 rs=0.05 n=1.2 cjo=20p )",
  'vac (a b) v type="sine" ampl=12 freq=60',
  "d1 (a out) dm", "d2 (b out) dm", "d3 (0 a) dm", "d4 (0 b) dm",
  "rload (out 0) r r=470", "cload (out 0) c c=220u", "rbleed (a b) r r=1e6",
  "control", "  analysis tran1 tran step=200u stop=480", "endc", ""])

# ---- CMOS inverter chain / ring oscillator (MOSFET level 1) ----------------
def cmos(n, ring):
    L = ["CMOS inverter %s, %d stages (%d transistors)"
         % ("ring oscillator" if ring else "chain", n, 2*n)] + head("mos","r","c") + [
     "model nm sp_mos1 ( type=1  vto=0.6  kp=150u lambda=0.02 gamma=0.4 phi=0.65 )",
     "model pm sp_mos1 ( type=-1 vto=-0.6 kp=50u  lambda=0.02 gamma=0.4 phi=0.65 )",
     "vdd (vdd 0) v dc=3.3",
     "subckt inv (a y vdd)",
     "  mn (y a 0 0) nm w=2u l=0.35u",
     "  mp (y a vdd vdd) pm w=5u l=0.35u",
     "  cl (y 0) c c=8f",
     "ends"]
    if ring:
        nodes = ["n%d" % k for k in range(n)]
        for k in range(n):
            L.append("xi%d (%s %s vdd) inv" % (k, nodes[k], nodes[(k+1) % n]))
        L += ["control",
              '  analysis tran1 tran step=0.05n stop=200n maxstep=0.05n icmode="op" ic=["n0"; 3.3]',
              "endc", ""]
    else:
        L.append('vin (in 0) v type="pulse" val0=0 val1=3.3 delay=1n rise=0.5n fall=0.5n width=20n period=40n')
        prev = "in"
        for k in range(1, n+1):
            nn = "out" if k == n else "n%d" % k
            L.append("xi%d (%s %s vdd) inv" % (k, prev, nn)); prev = nn
        L += ["control", "  analysis tran1 tran step=0.05n stop=600n maxstep=0.05n", "endc", ""]
    return "\n".join(L)
M["inv_chain"] = cmos(100, False)
M["ring_osc"]  = cmos(51, True)

# ---- 5T OTA (multitone diff input = series sines) --------------------------
M["ota_5t"] = "\n".join(
 ["5T OTA -- NMOS diff pair, PMOS mirror load, NMOS tail (5 transistors)"] + head("mos","c") + [
  "model nm sp_mos1 ( type=1  vto=0.6  kp=150u lambda=0.02 gamma=0.4 phi=0.65 )",
  "model pm sp_mos1 ( type=-1 vto=-0.6 kp=50u  lambda=0.02 gamma=0.4 phi=0.65 )",
  "vdd (vdd 0) v dc=3.3", "vcm (cm 0) v dc=1.65", "vbias (gb 0) v dc=0.95",
  'vs1 (inp na) v type="sine" ampl=0.05 freq=0.4e6',
  'vs2 (na nb) v type="sine" ampl=0.05 freq=1e6',
  'vs3 (nb cm) v type="sine" ampl=0.05 freq=2.3e6',
  "mtail (tail gb 0 0) nm w=20u l=0.5u",
  "m1 (d1 inp tail 0) nm w=10u l=0.5u",
  "m2 (out cm tail 0) nm w=10u l=0.5u",
  "m3 (d1 d1 vdd vdd) pm w=10u l=0.5u",
  "m4 (out d1 vdd vdd) pm w=10u l=0.5u",
  "cl (out 0) c c=0.5p",
  "control", "  analysis tran1 tran step=20n stop=24m", "endc", ""])

# ---- BJT 3-stage CE amp (multitone in = series sines) ----------------------
def bjt_stage(k, prev):
    b,c,e = "b%d"%k, "c%d"%k, "e%d"%k
    cc = "cin" if k == 1 else "cc%d"%(k-1)
    return ["%s (%s %s) c c=1u" % (cc, prev, b),
            "rc%d (vcc %s) r r=4.7k" % (k, c), "rb%d (vcc %s) r r=100k" % (k, b),
            "re%d (%s 0) r r=470" % (k, e), "ce%d (%s 0) c c=10u" % (k, e),
            "q%d (%s %s %s) qn" % (k, c, b, e)], c
def bjt_amp(n, stop, maxstep=None, multitone=True):
    L = ["%d-stage common-emitter BJT amplifier (%d transistors)" % (n, n)] + head("bjt","r","c") + [
     "model qn sp_bjt ( type=1 subs=1 is=1e-14 bf=200 vaf=100 rb=10 rc=1 re=0.5 cjc=3p cje=8p tf=0.4n tr=10n )",
     "vcc (vcc 0) v dc=12"]
    if multitone:
        L += ['vs1 (in na) v type="sine" ampl=0.005 freq=5e3',
              'vs2 (na nb) v type="sine" ampl=0.005 freq=13e3',
              'vs3 (nb 0) v type="sine" ampl=0.005 freq=27e3']
    else:
        L += ['vin (in 0) v dc=0 type="sine" ampl=0.005 freq=10k']
    prev = "in"
    for k in range(1, n+1):
        lines, prev = bjt_stage(k, prev); L += lines
    L += ["cout (%s out) c c=1u" % prev, "rload (out 0) r r=10k", "control",
          "  analysis tran1 tran step=%s stop=%s%s" % ("2u" if n<=3 else "20n", stop,
              (" maxstep=%s" % maxstep) if maxstep else ""),
          "endc", ""]
    return "\n".join(L)
M["bjt_amp"] = bjt_amp(3, "1.2")
M["breaker"] = bjt_amp(3000, "200u", maxstep="20n", multitone=False)

# ---- 2-stage Miller op-amp (multitone + dc offset = series sources) --------
M["opamp"] = "\n".join(
 ["2-stage Miller CMOS op-amp, unity-gain follower (8 transistors)"] + head("mos","c") + [
  "model nm sp_mos1 ( type=1  vto=0.6  kp=150u lambda=0.02 )",
  "model pm sp_mos1 ( type=-1 vto=-0.6 kp=50u  lambda=0.02 )",
  "vdd (vdd 0) v dc=3.3",
  "iref (vdd nbias) i dc=30u",
  "mbias (nbias nbias 0 0) nm w=10u l=1u",
  "m5 (tail nbias 0 0) nm w=20u l=1u",
  "m7 (no nbias 0 0) nm w=40u l=1u",
  "m1 (n1 no  tail 0) nm w=20u l=1u",
  "m2 (n2 inp tail 0) nm w=20u l=1u",
  "m3 (n1 n1 vdd vdd) pm w=20u l=1u",
  "m4 (n2 n1 vdd vdd) pm w=20u l=1u",
  "m6 (no n2 vdd vdd) pm w=80u l=1u",
  "cc (n2 no) c c=2p", "cl (no 0) c c=1p",
  "vdc (inp na) v dc=1.35",
  'vs1 (na nb) v type="sine" ampl=0.05 freq=100e3',
  'vs2 (nb nc) v type="sine" ampl=0.05 freq=270e3',
  'vs3 (nc 0) v type="sine" ampl=0.05 freq=730e3',
  "control", "  analysis tran1 tran step=30n stop=210m", "endc", ""])

if __name__ == "__main__":
    only = os.environ.get("ONLY")
    for name, txt in M.items():
        if only and name != only: continue
        with open(os.path.join(OUT, name + ".sim"), "w") as f:
            f.write(txt)
        print("wrote %-14s (%s)" % (name + ".sim", txt.splitlines()[0]))
