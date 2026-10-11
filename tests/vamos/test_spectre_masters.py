"""SPECTRE_MASTERS against ref5: every parameter of every v1 master has a disposition row, and every
default row is a non-redundant write (docs/VAMOS_SPECTRE_DESIGN.md §3.9, §4.4, §10, §11 T0, §12 phase 0;
owner S0C).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_masters.py' -v

TestMasters     one test per v1 master (§0).  It fails on a ref5 card or instance parameter of that master
                with no row in tables.SPECTRE_MASTERS[master], on a row whose name is neither a ref5
                parameter of that list, a rename target of the same list, nor one of the two target-only
                written names (badmos3, nbv), and on a MasterRow whose element, DISPATCH level, polarity
                key, kinds, terminals or default geometry differ from §3.8/§3.9.  REF5 below lists the
                "Instance Parameters" and "Model Parameters" sections of the Spectre Circuit Simulator
                Reference 5.0 component chapters (ref5; the pages are in the comments), extracted from
                the manual, so it is an oracle independent of the data.
TestDefaults    every `default` row against the targets' own defaults (TARGET_DEFAULTS: VACASK 3becb73d
                devices/spice/*.va and Xyce 7cd78110 N_DEV_*.C, file:line in every entry).  A write is
                redundant, and fails here, when both targets already default to the written value; the
                MOS instance geometry rows are exempt (§3.9 writes w/l on every instance) and must equal
                MasterRow.geometry instead.  Every default row has an oracle entry and every entry a row,
                so a default added later must be compared with both targets.
TestVocabulary  the ParamRule grammar of §10 (actions, value, when, match, warn, cite), the first-match
                order (no rule shadowed by an earlier unconditional rule of the same name), the rows §3.9
                decided (capmod, hcomp, nsub/phi/gamma/vto, mjsw, badmos3, bsim3v3 capmod, bjt fc, diode
                eg and fcs, resistor af, mos1 tox), the equal defaults §3.9 says are never written, the
                folded card geometry §5.2 refuses as a sweep target, the master set, DISPATCH and
                ELEMENT_OF consistency.

Runs on both legs (Cygwin Python 3.9, WSL Python 3.14), no engine.  The data block is pasted into
vamos/netlist/tables.py by the phase-0 merge (scratchpad sp0/S0C/spectre_masters_data.py); until then
every test here fails naming the master the table lacks (§12 "Done when").
"""

import re
import unittest

from vamos_testlib import ROOT  # noqa: F401  (puts the repository on sys.path)

from vamos.netlist import tables  # noqa: E402

MERGE = ("tables.SPECTRE_MASTERS has no %r: the phase-0 S0C data block (scratchpad sp0/S0C/"
         "spectre_masters_data.py) is not merged into vamos/netlist/tables.py yet "
         "(VAMOS_SPECTRE_DESIGN.md §12 phase 0)")


def mos_geometry(wl):
    """§3.9: the master's default instance w/l and the default model-group bounds [M ref5 p.200]."""
    return (("w", wl), ("l", wl), ("lmin", 0.0), ("lmax", 1.0), ("wmin", 0.0), ("wmax", 1.0))


# the v1 masters (§0) with the row facts of §3.8 (element, terminals) and §3.9 (level, polarity, kinds,
# default geometry): master -> (element, level, polarity_key, kinds, terminals, geometry)
ROWS = {
    "resistor": ("r", None, "", (("", "r"),), ("1", "2"), ()),
    "capacitor": ("c", None, "", (("", "c"),), ("1", "2"), ()),
    "inductor": ("l", None, "", (("", "l"),), ("1", "2"), ()),
    "vsource": ("v", None, "", (), ("p", "n"), ()),
    "isource": ("i", None, "", (), ("sink", "src"), ()),
    "iprobe": ("v", None, "", (), ("in", "out"), ()),
    "vcvs": ("e", None, "", (), ("p", "n", "ps", "ns"), ()),
    "vccs": ("g", None, "", (), ("sink", "src", "ps", "ns"), ()),
    "ccvs": ("h", None, "", (), ("p", "n"), ()),
    "cccs": ("f", None, "", (), ("sink", "src"), ()),
    "mutual_inductor": ("k", None, "", (), (), ()),
    "diode": ("d", 1, "", (("", "d"),), ("a", "c"), ()),
    "bjt": ("q", 1, "type", (("npn", "npn"), ("pnp", "pnp")), ("c", "b", "e", "s"), ()),
    "jfet": ("j", 1, "type", (("n", "njf"), ("p", "pjf")), ("d", "g", "s", "b"), ()),
    "mos1": ("m", 1, "type", (("n", "nmos"), ("p", "pmos")), ("d", "g", "s", "b"), mos_geometry(3e-6)),
    "mos2": ("m", 2, "type", (("n", "nmos"), ("p", "pmos")), ("d", "g", "s", "b"), mos_geometry(3e-6)),
    "mos3": ("m", 3, "type", (("n", "nmos"), ("p", "pmos")), ("d", "g", "s", "b"), mos_geometry(3e-6)),
    "bsim3v3": ("m", 49, "type", (("n", "nmos"), ("p", "pmos")), ("d", "g", "s", "b"), mos_geometry(5e-6)),
    "bsim4": ("m", 54, "type", (("n", "nmos"), ("p", "pmos")), ("d", "g", "s", "b"), mos_geometry(5e-6)),
}

# default rows written under the target's name: §3.9's badmos3 (no Spectre parameter), and nbv for Spectre's
# nz (the targets' nbv follows n when absent, Spectre's nz is 1)
TARGET_ONLY = {"mos3": {"badmos3"}, "diode": {"nbv"}}

ACTIONS = ("fold", "pass", "rename", "default", "strip", "error")
WARNS = ("", "card", "analyses=ac,noise,xf,tran", "analyses=noise", "temp!=tnom")
NUMBER = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?(/(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?)?$")
WORD = re.compile(r"^[a-z_][a-z0-9_]*$")


def value_of(text):
    """A Spectre default as the rows write it: "1/3", "1e-7", "1.13e16", "0.5", "2"."""
    if "/" in text:
        a, b = text.split("/")
        return float(a) / float(b)
    return float(text)


def same(a, b):
    if a == 0 or b == 0:
        return a == b
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b))


def rule_key(r):
    return (r.name, r.action, r.value, r.when, tuple(r.match), r.warn)


def R(name, action, value="", when="", match=(), warn=""):
    return (name, action, value, when, tuple(match), warn)


# the rows §3.9 decided (its table and the paragraph below it; the §10 examples), for the card lists
MOS123_DECIDED = [
    R("capmod", "strip", match=("absent", "bsim"), warn="analyses=ac,noise,xf,tran"),
    R("capmod", "strip", match=("meyer",)),
    R("capmod", "error", match=("none", "yang")),
    R("nsub", "default", "1.13e16", "absent:nsub"),
    R("phi", "default", "0.7", "absent:phi,absent:nsub"),
    R("gamma", "default", "0", "absent:gamma,absent:nsub"),
    R("vto", "default", "0", "absent:vto,absent:nsub"),
    R("phi", "pass", "", "absent:phi,given:nsub", ("absent",), "card"),       # the derivation warning
    R("gamma", "pass", "", "absent:gamma,given:nsub", ("absent",), "card"),
    R("vto", "pass", "", "absent:vto,given:nsub", ("absent",), "card"),
    R("mjsw", "default", "1/3", "absent:mjsw"),
]
DECIDED = {
    "mos1": MOS123_DECIDED + [R("tox", "default", "1e-7", "absent:tox")],
    "mos2": MOS123_DECIDED,
    "mos3": MOS123_DECIDED + [R("badmos3", "default", "1", "absent:badmos3", warn="card")],
    "bsim3v3": [R("capmod", "default", "2", "absent:capmod")],
    "bjt": [R("fc", "default", "0.5", "absent:fc")],
    "diode": [R("eg", "default", "1.124481", "absent:eg", warn="temp!=tnom"),
              R("fcs", "default", "=fc", "absent:fcs,given:fc"),
              R("hcomp", "error", match=("nonzero",))],
    "resistor": [R("af", "default", "2", "absent:af,given:kf")],
}
# §3.9: "Equal defaults are not written": diode vj=1 fc=0.5 mjsw=0.33, bsim3v3 xpart=0 cj=5e-4 cjsw=5e-10,
# mos fc=0.5; and tox=1e-7 on mos2/mos3, where both targets already default to it (mos2.va:137, mos3.va:139,
# N_DEV_MOSFET2.C:282, N_DEV_MOSFET3.C:283)
NOT_WRITTEN = {"diode": ("vj", "fc", "mjsw"), "bsim3v3": ("xpart", "cj", "cjsw"),
               "mos1": ("fc",), "mos2": ("fc", "tox"), "mos3": ("fc", "tox")}
# §5.2: a mod= target whose rule folds it is an error: the card geometry spectre.py folds into instances (§3.8, §3.9)
FOLDED_CARD = {"resistor": "r rsh l w etch etchl", "capacitor": "c w l etch cj cjsw",
               "mos1": "w l", "mos2": "w l", "mos3": "w l", "bsim3v3": "w l", "bsim4": "w l"}

# The targets' own defaults behind every `default` row: (master, "card" | "instance", written name) ->
# (Spectre's default as ref5 states it, VACASK's default, Xyce's default, the sources).  A target default is
# a number, None (no such parameter) or a string naming a derivation applied when the parameter is not
# given ("=n": nbv follows n; "derived:nsub": SPICE3 derives it from a given nsub, which the written value
# pins).  A row whose value is "=p" copies the card's p.  Verified 2026-10-10 (scratchpad sp0/S0C/comparison.md).
TARGET_DEFAULTS = {
    ("resistor", "card", "af"): ("2", 1.0, None,
        "resistor.va:115 af=1.0; N_DEV_Resistor.C has no AF (no noise parameters)"),
    ("diode", "card", "ikp"): ("ik", 0.0, None,
        "diode.va:143 ikp=0; not given = the sidewall knee is disabled (diode.va:555); N_DEV_Diode.C has no IKP"),
    ("diode", "card", "fcs"): ("fc", 0.5, 0.5,
        "diode.va:168 fcs=0.5; N_DEV_Diode.C:305 FCS 0.5"),
    ("diode", "card", "nbv"): ("1 (Spectre's nz)", "=n", "=n",
        "diode.va:602 nbv=n when not given (declared 0, :144; alias nz, :145); N_DEV_Diode.C:1631 NBV=N when not given (:335)"),
    ("diode", "card", "eg"): ("1.124481", 1.11, 1.11,
        "diode.va:605-611 1.11 when not given (1.16 with tlev=2; declared 0, :150); N_DEV_Diode.C:248 EG 1.11"),
    ("bjt", "card", "mje"): ("1/3", 0.33, 0.33, "bjt.va:121 mje=0.33; N_DEV_BJT.C:443 MJE 0.33"),
    ("bjt", "card", "mjc"): ("1/3", 0.33, 0.33, "bjt.va:131 mjc=0.33; N_DEV_BJT.C:537 MJC 0.33"),
    ("bjt", "card", "fc"): ("0.5", 0.0, 0.5, "bjt.va:145 fc=0; N_DEV_BJT.C:723 FC 0.5"),
    ("jfet", "card", "kf"): ("0", 0.0, 0.05, "jfet1.va:116 kf=0; N_DEV_JFET.C:117 KF 0.05"),
    ("mos1", "card", "vto"): ("0", "derived:nsub", "derived:nsub",
        "declared 0 (mos1.va:113; N_DEV_MOSFET1.C:166), derived from a given nsub when absent (mos1.va:704, 748-767; N_DEV_MOSFET1.C:3969-3994)"),
    ("mos1", "card", "phi"): ("0.7", 0.6, 0.6,
        "mos1.va:117 phi=0.6; N_DEV_MOSFET1.C:186 PHI 0.6; both derive it from a given nsub (mos1.va:750; N_DEV_MOSFET1.C:3973)"),
    ("mos1", "card", "gamma"): ("0", "derived:nsub", "derived:nsub",
        "declared 0 (mos1.va:116; N_DEV_MOSFET1.C:181), derived from a given nsub when absent (mos1.va:761; N_DEV_MOSFET1.C:3988)"),
    ("mos1", "card", "nsub"): ("1.13e16", 0.0, 0.0,
        "mos1.va:139 nsub=0 (not given); N_DEV_MOSFET1.C:305 NSUB 0.0"),
    ("mos1", "card", "tpg"): ("+1", 1.0, 0.0, "mos1.va:140 tpg=1; N_DEV_MOSFET1.C:331 TPG 0"),
    ("mos1", "card", "tox"): ("1e-7", 0.0, 1e-7,
        "mos1.va:134 tox=0 (not given: no oxide capacitance, mos1.va:741-747); N_DEV_MOSFET1.C:279 TOX 1e-7"),
    ("mos1", "card", "mjsw"): ("1/3", 0.5, 0.5, "mos1.va:132 mjsw=0.5; N_DEV_MOSFET1.C:269 MJSW 0.5"),
    ("mos2", "card", "vto"): ("0", "derived:nsub", "derived:nsub",
        "declared 0 (mos2.va:116; N_DEV_MOSFET2.C:169), derived from a given nsub when absent (SPICE3 mos2 setup)"),
    ("mos2", "card", "phi"): ("0.7", 0.6, 0.6, "mos2.va:120 phi=0.6; N_DEV_MOSFET2.C:189 PHI 0.6"),
    ("mos2", "card", "gamma"): ("0", "derived:nsub", "derived:nsub",
        "declared 0 (mos2.va:119; N_DEV_MOSFET2.C:184), derived from a given nsub when absent"),
    ("mos2", "card", "nsub"): ("1.13e16", 0.0, 0.0, "mos2.va:142 nsub=0; N_DEV_MOSFET2.C:308 NSUB 0.0"),
    ("mos2", "card", "tpg"): ("+1", 1.0, 0.0, "mos2.va:143 tpg=1; N_DEV_MOSFET2.C:372 TPG 0"),
    ("mos2", "card", "mjsw"): ("1/3", 0.33, 0.5, "mos2.va:135 mjsw=0.33; N_DEV_MOSFET2.C:272 MJSW 0.5"),
    ("mos3", "card", "vto"): ("0", "derived:nsub", "derived:nsub",
        "declared 0 (mos3.va:119; N_DEV_MOSFET3.C:175), derived from a given nsub when absent (mos3.va:873-897)"),
    ("mos3", "card", "phi"): ("0.7", 0.6, 0.6, "mos3.va:123 phi=0.6; N_DEV_MOSFET3.C:195 PHI 0.6"),
    ("mos3", "card", "gamma"): ("0", "derived:nsub", "derived:nsub",
        "declared 0 (mos3.va:122; N_DEV_MOSFET3.C:190), derived from a given nsub when absent"),
    ("mos3", "card", "nsub"): ("1.13e16", 0.0, 0.0, "mos3.va:149 nsub=0; N_DEV_MOSFET3.C:309 NSUB 0.0"),
    ("mos3", "card", "mjsw"): ("1/3", 0.33, 0.33, "mos3.va:137 mjsw=0.33; N_DEV_MOSFET3.C:273 MJSW 0.33"),
    ("mos3", "card", "badmos3"): ("(undocumented; §3.9 decides 1)", 0.0, 0.0,
        "mos3.va:159 badmos3=0; N_DEV_MOSFET3.C:349 BADMOS3 0"),
    ("bsim3v3", "card", "capmod"): ("2", 3.0, 3.0, "bsim3v3.va:140 capmod=3; N_DEV_MOSFET_B3.C:3021 CAPMOD 3"),
    ("bsim4", "card", "rdsmod"): ("1", 0.0, 0.0,
        "bsim4v8.va:146 rdsmod=0 (and :3795 when not given); N_DEV_MOSFET_B4.C:4735 RDSMOD 0"),
    ("bsim4", "card", "igbmod"): ("1", 0.0, 0.0,
        "bsim4v8.va:159 igbmod=0 (and :3861 when not given); N_DEV_MOSFET_B4.C:4808 IGBMOD 0"),
    # instance defaults: the master's (§3.9 default MOS geometry; ref5's card defaults nrd=nrs=0)
    ("mos1", "instance", "w"): ("3e-6 (card w)", 1e-4, 1e-4,
        "mos1.va:101 w=0 -> defw=$simparam(defw, 1e-4) (:397); N_DEV_MOSFET1.C:74 W 0 -> model W 1e-4 (:161)"),
    ("mos1", "instance", "l"): ("3e-6 (card l)", 1e-4, 1e-4,
        "mos1.va:100 l=0 -> defl 1e-4 (:396); N_DEV_MOSFET1.C:67 L 0 -> model L 1e-4 (:156)"),
    ("mos1", "instance", "nrd"): ("0 (card nrd)", 1.0, 1.0, "mos1.va:106 nrd=1; N_DEV_MOSFET1.C:93 NRD 1.0"),
    ("mos1", "instance", "nrs"): ("0 (card nrs)", 1.0, 1.0, "mos1.va:107 nrs=1; N_DEV_MOSFET1.C:98 NRS 1.0"),
    ("mos2", "instance", "w"): ("3e-6 (card w)", 1e-4, 1e-4,
        "mos2.va:104 w=0 -> defw 1e-4 (:569); N_DEV_MOSFET2.C:77 W 0 -> model W 1e-4 (:164)"),
    ("mos2", "instance", "l"): ("3e-6 (card l)", 1e-4, 1e-4,
        "mos2.va:103 l=0 -> defl 1e-4 (:568); N_DEV_MOSFET2.C:70 L 0 -> model L 1e-4 (:159)"),
    ("mos2", "instance", "nrd"): ("0 (card nrd)", 1.0, 1.0, "mos2.va:109 nrd=1; N_DEV_MOSFET2.C:96 NRD 1.0"),
    ("mos2", "instance", "nrs"): ("0 (card nrs)", 1.0, 1.0, "mos2.va:110 nrs=1; N_DEV_MOSFET2.C:101 NRS 1.0"),
    ("mos3", "instance", "w"): ("3e-6 (card w)", 1e-4, 1e-4,
        "mos3.va:107 w=0 -> defw 1e-4 (:517); N_DEV_MOSFET3.C:83 W 0 -> model W 1e-4 (:170)"),
    ("mos3", "instance", "l"): ("3e-6 (card l)", 1e-4, 1e-4,
        "mos3.va:106 l=0 -> defl 1e-4 (:516); N_DEV_MOSFET3.C:76 L 0 -> model L 1e-4 (:165)"),
    ("mos3", "instance", "nrd"): ("0 (card nrd)", 1.0, 1.0, "mos3.va:112 nrd=1; N_DEV_MOSFET3.C:102 NRD 1.0"),
    ("mos3", "instance", "nrs"): ("0 (card nrs)", 1.0, 1.0, "mos3.va:113 nrs=1; N_DEV_MOSFET3.C:107 NRS 1.0"),
    ("bsim3v3", "instance", "w"): ("5e-6 (card w)", 5e-6, 5e-6,
        "bsim3v3.va:126 w=5e-06; N_DEV_MOSFET_B3.C:129 W 0 -> model W 5e-6 (:2974); equal: written because §3.9 writes the geometry"),
    ("bsim3v3", "instance", "l"): ("5e-6 (card l)", 5e-6, 5e-6,
        "bsim3v3.va:125 l=5e-06; N_DEV_MOSFET_B3.C:120 L 0 -> model L 5e-6 (:2967); equal: written because §3.9 writes the geometry"),
    ("bsim3v3", "instance", "nrd"): ("0 (card nrd)", 0.0, 1.0, "bsim3v3.va:131 nrd=0; N_DEV_MOSFET_B3.C:154 NRD 1.0"),
    ("bsim3v3", "instance", "nrs"): ("0 (card nrs)", 0.0, 1.0, "bsim3v3.va:132 nrs=0; N_DEV_MOSFET_B3.C:161 NRS 1.0"),
    ("bsim4", "instance", "w"): ("5e-6 (card w)", 5e-6, 5e-6,
        "bsim4v8.va:104 w=5e-06; N_DEV_MOSFET_B4.C:114 W 5.0e-6; equal: written because §3.9 writes the geometry"),
    ("bsim4", "instance", "l"): ("5e-6 (card l)", 5e-6, 5e-6,
        "bsim4v8.va:103 l=5e-06; N_DEV_MOSFET_B4.C:108 L 5.0e-6; equal: written because §3.9 writes the geometry"),
    ("bsim4", "instance", "nrd"): ("0 (card nrd)", 1.0, 1.0, "bsim4v8.va:118 nrd=1.0; N_DEV_MOSFET_B4.C:194 NRD 1.0"),
    ("bsim4", "instance", "nrs"): ("0 (card nrs)", 1.0, 1.0, "bsim4v8.va:119 nrs=1.0; N_DEV_MOSFET_B4.C:200 NRS 1.0"),
}

# ref5's parameter lists, master -> (card names, instance names), one space-separated string each, in the
# manual's order.  Chapters (ref5 pages): bjt 49-61, bsim3v3 186-207, bsim4 208-236, capacitor 282-285,
# cccs 286-287, ccvs 288-289, diode 302-308, inductor 372-374, iprobe 379, isource 380-384, jfet 385-391,
# mos1 408-423, mos2 487-502, mos3 503-519, mutual_inductor 577, resistor 627-634, vccs 678-679,
# vcvs 680-681, vsource 682-687.  Model-only masters have no model parameters (an empty string); iprobe has
# no parameters at all.  Each rule's `cite` names the page of its parameter.
REF5 = {
    "resistor": (
        # card (pp.630-632)
        "r rsh thresh l w etch etchl scaler tc1 tc2 tnom trise coeffs nonlinform symmetric kf af wdexp ldexp "
        "weexp leexp fexp mr mrl mrlp mrw mrwp mrlw1 mrlw1p mrlw2 mrlw2p c cj cjsw thick di cratio tc1c tc2c "
        "shrink scalec",
        # instance (p.629)
        "r l w m scale resform tc1 tc2 trise isnoisy c tc1c tc2c",
    ),
    "capacitor": (
        # card (pp.284-285)
        "c tc1 tc2 trise tnom w l etch cj cjsw scalec coeffs rforce",
        # instance (pp.283-284)
        "c w l m scale trise tc1 tc2 ic area perim",
    ),
    "inductor": (
        # card (pp.373-374)
        "l r tc1 tc2 trise tnom rforce coeffs scalei kf af",
        # instance (p.373)
        "l r m trise ic isnoisy",
    ),
    "vsource": (
        # card
        "",
        # instance (pp.683-686)
        "dc type fundname delay val0 val1 period rise fall width file wave offset scale stretch allbrkpts "
        "pwlperiod twidth sinedc ampl freq sinephase ampl2 freq2 sinephase2 fundname2 fmmodindex fmmodfreq "
        "ammodindex ammodfreq ammodphase damp td1 tau1 td2 tau2 noisefile noisevec mag phase xfmag pacmag "
        "pacphase m tc1 tc2 tnom",
    ),
    "isource": (
        # card
        "",
        # instance (pp.380-383)
        "dc type fundname delay val0 val1 period rise fall width file wave offset scale stretch allbrkpts "
        "pwlperiod twidth sinedc ampl freq sinephase ampl2 freq2 sinephase2 fundname2 fmmodindex fmmodfreq "
        "ammodindex ammodfreq ammodphase damp td1 tau1 td2 tau2 noisefile noisevec mag phase xfmag pacmag "
        "pacphase m tc1 tc2 tnom",
    ),
    "iprobe": (
        # card
        "",
        # instance
        "",
    ),
    "vcvs": (
        # card
        "",
        # instance (p.681)
        "m type delta gain min max abs file pwl scale stretch tc1",
    ),
    "vccs": (
        # card
        "",
        # instance (p.679)
        "m type delta gm min max abs file pwl scale stretch tc1 tc2",
    ),
    "ccvs": (
        # card
        "",
        # instance (p.289)
        "m probe port probes ports type delta rm min max abs file pwl scale",
    ),
    "cccs": (
        # card
        "",
        # instance (pp.286-287)
        "m probe port probes ports type delta gain min max abs file pwl scale stretch tc1 tc2",
    ),
    "mutual_inductor": (
        # card
        "",
        # instance (p.577)
        "coupling ind1 ind2",
    ),
    "diode": (
        # card (pp.303-308)
        "level hcomp dcap etch etchl shrink l w js jsw n ns ik ikp ikr area perim allow_scaling tt cd cjo vj pb m "
        "cjsw vjsw mjsw fc fcs lm lp wm wp xm xp xoi xom xw bv vb ibv nz bvj rs rsw gleak gleaksw minr tlev tlevc "
        "eg gap1 gap2 xti tbv1 tbv2 tnom trise trs trs2 tgs tgs2 cta ctp pta ptp jmelt jmax dskip if ir ecrf ecrr "
        "nf nr tox kf af",
        # instance (pp.302-303)
        "area perim l w m scale region trise lm lp wm wp",
    ),
    "bjt": (
        # card (pp.51-58)
        "type struct is ise isc iss c2 c4 cbo gbo vbo tcbo tgbo nf nr ne nc ns bf br ikf ikr vaf var ke kc rb rbm "
        "irb rbmod rc rcv rcm dope cex cco re minr cje vje mje cjc vjc mjc xcjc xcjc2 cjs vjs mjs fc cbcp cbep "
        "ccsp tf td xtf vtf itf tr ptf tnom trise eg xtb xti trb1 trb2 trm1 trm2 trc1 trc2 tre1 tre2 tlev tlevc "
        "gap1 gap2 tikf1 tikf2 tikr1 tikr2 tirb1 tirb2 tis1 tis2 tise1 tise2 tisc1 tisc2 tiss1 tiss2 tbf1 tbf2 "
        "tbr1 tbr2 tvaf1 tvaf2 tvar1 tvar2 titf1 titf2 ttf1 ttf2 ttr1 ttr2 tnf1 tnf2 tnr1 tnr2 tne1 tne2 tnc1 "
        "tnc2 tns1 tns2 tmje1 tmje2 tmjc1 tmjc2 tmjs1 tmjs2 cte ctc cts tvje tvjc tvjs tvtf1 tvtf2 txtf1 txtf2 "
        "dskip imelt bvbe bvbc bvce bvsub vbefwd vbcfwd vsubfwd imax imax1 alarm kf af kb bnoisefc rbnoi",
        # instance (p.50)
        "area areab areac m trise region",
    ),
    "jfet": (
        # card (pp.386-389)
        "type level vto beta lambda lambda1 np alpha io ns ai bi vtop vtos vtoe vtoc rd rs rg rb minr is n imelt "
        "dskip tt cgs cgd mj pb fc isb nb cgbs cgbd mjb pbb tnom trise xti tlev tlevc eg gap1 gap2 tcv bto bte "
        "lto lte tc1 tc2 alarm imax bvj kf af kfd afg",
        # instance (pp.385-386)
        "area m region",
    ),
    "mos1": (
        # card (pp.410-419)
        "type vto kp lambda phi gamma uo vmax theta nsub nss nfs tpg ld wd xw xl tox ai0 lai0 wai0 bi0 lbi0 wbi0 "
        "cgso cgdo cgbo meto capmod xpart xqc rs rd rss rdd rsh rsc rdc minr ldif hdif lgcs lgcd sc js is n dskip "
        "imelt jmelt cbs cbd cj mj pb fc cjsw mjsw pbsw fcsw alarm imax jmax bvj vbox tnom trise uto ute tlev "
        "tlevc eg gap1 gap2 f1ex lamex trs trd xti ptc tcv pta ptp cta ctp w l as ad ps pd nrd nrs ldd lds "
        "noisemod kf af ef wnoi wmax wmin lmax lmin degramod degradation dvthc dvthe duoc duoe crivth criuo crigm "
        "criids wnom lnom vbsn vdsni vgsni vdsng vgsng esat esatg vpg vpb subc1 subc2 sube strc stre h0 hgd m0 "
        "mgd ecrit0 lecrit0 wecrit0 ecritg lecritg wecritg ecritb lecritb wecritb lc0 llc0 wlc0 lc1 llc1 wlc1 lc2 "
        "llc2 wlc2 lc3 llc3 wlc3 lc4 llc4 wlc4 lc5 llc5 wlc5 lc6 llc6 wlc6 lc7 llc7 wlc7",
        # instance (pp.409-410)
        "w l as ad ps pd nrd nrs ld ls m region trise degradation",
    ),
    "mos2": (
        # card (pp.488-498)
        "type vto kp lambda phi gamma uo vmax ucrit uexp utra neff delta smooth nsub nss nfs tpg tox ld wd xw xl "
        "xj ai0 lai0 wai0 bi0 lbi0 wbi0 cgso cgdo cgbo meto capmod xpart xqc rs rd rsh rss rdd rsc rdc minr ldif "
        "hdif lgcs lgcd sc js is n dskip imelt jmelt cbs cbd cj mj pb fc cjsw mjsw pbsw fcsw alarm imax jmax bvj "
        "vbox tnom trise uto ute tlev tlevc eg gap1 gap2 f1ex lamex trs trd xti ptc tcv pta ptp cta ctp w l as ad "
        "ps pd nrd nrs ldd lds noisemod kf af ef wnoi wmax wmin lmax lmin degramod degradation dvthc dvthe duoc "
        "duoe crivth criuo crigm criids wnom lnom vbsn vdsni vgsni vdsng vgsng esat esatg vpg vpb subc1 subc2 "
        "sube strc stre h0 hgd m0 mgd ecrit0 lecrit0 wecrit0 ecritg lecritg wecritg ecritb lecritb wecritb lc0 "
        "llc0 wlc0 lc1 llc1 wlc1 lc2 llc2 wlc2 lc3 llc3 wlc3 lc4 llc4 wlc4 lc5 llc5 wlc5 lc6 llc6 wlc6 lc7 llc7 "
        "wlc7",
        # instance (pp.487-488)
        "w l as ad ps pd nrd nrs ld ls m region trise degradation",
    ),
    "mos3": (
        # card (pp.505-514)
        "type vto kp theta phi gamma uo vmax eta kappa delta nsub nss nfs tpg tox ld wd xw xl xj ai0 lai0 wai0 "
        "bi0 lbi0 wbi0 cgso cgdo cgbo meto capmod xpart xqc rs rd rsh rss rdd rsc rdc minr ldif hdif lgcs lgcd sc "
        "js is n dskip imelt jmelt cbs cbd cj mj pb fc cjsw mjsw pbsw fcsw alarm imax jmax bvj vbox tnom trise "
        "uto ute tlev tlevc eg gap1 gap2 f1ex lamex trs trd xti ptc tcv pta ptp cta ctp w l as ad ps pd nrd nrs "
        "ldd lds noisemod kf af ef wnoi wmax wmin lmax lmin degramod degradation dvthc dvthe duoc duoe crivth "
        "criuo crigm criids wnom lnom vbsn vdsni vgsni vdsng vgsng esat esatg vpg vpb subc1 subc2 sube strc stre "
        "h0 hgd m0 mgd ecrit0 lecrit0 wecrit0 ecritg lecritg wecritg ecritb lecritb wecritb lc0 llc0 wlc0 lc1 "
        "llc1 wlc1 lc2 llc2 wlc2 lc3 llc3 wlc3 lc4 llc4 wlc4 lc5 llc5 wlc5 lc6 llc6 wlc6 lc7 llc7 wlc7",
        # instance (p.504)
        "w l as ad ps pd nrd nrs ld ls m region trise degradation",
    ),
    "bsim3v3": (
        # card (pp.188-202)
        "type vtho vfb k1 k2 k3 k3b w0 nlx gamma1 gamma2 vbx vbm dvt0 dvt1 dvt2 dvt0w dvt1w dvt2w a0 b0 b1 a1 a2 "
        "ags keta vfbflag nsub nch ngate xj lint wint ll lln lw lwn lwl wl wln ww wwn wwl dwg dwb tox dtoxcv toxm "
        "xt rdsw prwb prwg wr binunit binflag lref wref mobmod u0 vsat ua ub uc drout pclm pdiblc1 pdiblc2 "
        "pdiblcb pscbe1 pscbe2 pvag delta cdsc cdscb cdscd nfactor cit voff dsub eta0 etab alpha0 alpha1 beta0 "
        "rsh rs rd lgcs lgcd rsc rdc rss rdd sc ldif hdif minr js jsw is n dskip imelt ijth jmelt vnds nds tt "
        "cgso cgdo cgbo meto cgsl cgdl ckappa cbs cbd cj mj pb fc cjsw mjsw pbsw cjswg mjswg pbswg fcsw capmod "
        "nqsmod dwc dlc clc cle cf elm vfbcv acde moin noff voffcv xpart llc lwc lwlc wlc wwc wwlc wmlt lmlt w l "
        "as ad ps pd nrd nrs version paramchk fullreinit level acm geo calcacm tnom trise tlev tlevc eg gap1 gap2 "
        "diomod kt1 kt1l kt2 at ua1 ub1 uc1 prt trs trd ute xti pta tpb ptp tpbsw tpbswg cta tcj ctp tcjsw tcjswg "
        "noimod kf af ef noia noib noic noid wnoi em flkmod gamma nlev bforward breverse cforward creverse tcc "
        "wmax wmin lmax lmin alarm imax jmax bvj vbox warn apwarn xl xw mvtwl mvtwl2 mvt0 mbewl mbe0 mos_method "
        "sa0 sb0 wlod ku0 kvsat kvth0 tku0 llodku0 wlodku0 llodvth wlodvth lku0 wku0 pku0 lkvth0 wkvth0 pkvth0 "
        "stk2 lodk2 steta0 lodeta0",
        # instance (pp.187-188)
        "w l as ad ps pd nrd nrs m region nqsmod trise aforward areverse delvto mulmu0 delk1 delnfct geo rdc rsc "
        "sa sb",
    ),
    "bsim4": (
        # card (pp.211-229)
        "type vtho vfb phin k1 k2 k3 k3b w0 lpe0 lpeb gamma1 gamma2 vbx vbm dvt0 dvt1 dvt2 dvtp0 dvtp1 dvt0w "
        "dvt1w dvt2w a0 b0 b1 a1 a2 ags keta epsrox toxe toxp dtox ndep nsd nsub ngate xj lint wint ll lln lw lwn "
        "lwl wl wln ww wwn wwl dwg dwb toxm xt binunit rdsmod rdsw rdswmin rdw rdwmin rsw rswmin prwb prwg wr "
        "mobmod u0 vsat ua ub uc eu drout fprout pclm pdiblc1 pdiblc2 pdiblcb pscbe1 pscbe2 pvag delta pdits "
        "pditsl pditsd cdsc cdscb cdscd nfactor cit voff voffl minv dsub eta0 etab alpha0 alpha1 beta0 rgatemod "
        "rsh rshg dmcg dmci dmdg dmcgt dwj xgw xgl ngcon nf min permod geomod rgeomod xw xl minr agidl bgidl "
        "cgidl egidl igcmod igbmod aigbacc bigbacc cigbacc nigbacc aigbinv bigbinv cigbinv eigbinv nigbinv aigc "
        "bigc cigc aigsd bigsd cigsd dlcig nigc poxedge pigcd ntox toxref diomod js jss jsd jsws jswd jswgs jswgd "
        "is n njs njd dskip imelt jmelt ijthsrev ijthdrev ijthsfwd ijthdfwd xjbvs xjbvd cgso cgdo cgbo meto cgsl "
        "cgdl ckappas ckappad cj cjs cjd mj mjs mjd pb pbs pbd fc cjsw cjsws cjswd mjsw mjsws mjswd pbsw pbsws "
        "pbswd cjswg cjswgs cjswgd mjswg mjswgs mjswgd pbswg pbswgs pbswgd fcsw bvs bvd capmod trnqsmod acnqsmod "
        "dwc dlc clc cle cf vfbcv acde moin noff voffcv xpart llc lwc lwlc wlc wwc wwlc w l as ad ps pd nrd nrs "
        "version level paramchk fullreinit tnom trise tlev tlevc eg gap1 gap2 kt1 kt1l kt2 at ua1 ub1 uc1 prt ute "
        "xti xtis xtid pta tpb ptp tpbsw tpbswg cta tcj ctp tcjsw tcjswg saref sbref wlod ku0 kvsat kvth0 tku0 "
        "llodku0 wlodku0 llodvth wlodvth lku0 wku0 pku0 lkvth0 wkvth0 pkvth0 stk2 lodk2 steta0 lodeta0 fnoimod "
        "tnoimod kf af ef noia noib noic wnoi em flkmod ntnoi tnoia tnoib rbodymod xrcrg1 xrcrg2 rbpb rbpd rbps "
        "rbdb rbsb gbmin wmax wmin lmax lmin alarm imax jmax bvj vbox warn mvtwl mvtwl2 mvt0 mbewl mbe0 "
        "mos_method",
        # instance (pp.209-211)
        "w l as ad ps pd nrd nrs m region trnqsmod acnqsmod trise rgatemod rbodymod geomod rgeomod rbpb rbpd rbps "
        "rbdb rbsb nf min delvto mulmu0 delk1 delnfct sa sb sd",
    ),
}


def ref5_names(master, section):
    return REF5[master][0 if section == "card" else 1].split()


def rules_of(row, section):
    return row.params if section == "card" else row.instance


class MastersCase(unittest.TestCase):
    def master_row(self, master):
        row = tables.SPECTRE_MASTERS.get(master)
        if row is None:
            self.fail(MERGE % master)
        return row

    def every_list(self):
        """(master, section, rules) for every master, card lists then instance lists."""
        for master in ROWS:
            row = self.master_row(master)
            for section in ("card", "instance"):
                yield master, section, rules_of(row, section)


class TestMasters(MastersCase):
    """One test per v1 master: every ref5 parameter has a disposition row (§3.9, §12 "Done when")."""

    def check(self, master):
        row = self.master_row(master)
        element, level, polarity_key, kinds, terminals, geometry = ROWS[master]
        self.assertEqual((row.master, row.element, row.level, row.polarity_key),
                         (master, element, level, polarity_key))
        self.assertEqual(row.kinds, kinds)
        self.assertEqual(row.terminals, terminals)
        self.assertEqual(row.geometry, geometry)
        for section in ("card", "instance"):
            names = ref5_names(master, section)
            rules = rules_of(row, section)
            self.assertEqual(len(set(names)), len(names), "ref5 list of %s %s has a duplicate" % (master, section))
            have = {r.name for r in rules}
            missing = [n for n in names if n not in have]
            self.assertEqual(missing, [], "%s %s: ref5 parameters without a disposition row: %s"
                             % (master, section, " ".join(missing)))
            renames = {r.value for r in rules if r.action == "rename"}
            allowed = set(names) | renames | TARGET_ONLY.get(master, set())
            unknown = sorted(have - allowed)
            self.assertEqual(unknown, [], "%s %s: rows naming no ref5 parameter: %s"
                             % (master, section, " ".join(unknown)))
            for r in rules:
                self.assertIsInstance(r, tables.ParamRule)
                self.assertEqual(r.name, r.name.lower(), "%s %s: %s is not lower case" % (master, section, r.name))
            self.assertEqual(len(rules), len(tuple(rules)))
        self.assertIsInstance(row.params, tuple)
        self.assertIsInstance(row.instance, tuple)

    def test_resistor(self):
        self.check("resistor")

    def test_capacitor(self):
        self.check("capacitor")

    def test_inductor(self):
        self.check("inductor")

    def test_vsource(self):
        self.check("vsource")

    def test_isource(self):
        self.check("isource")

    def test_iprobe(self):
        self.check("iprobe")

    def test_vcvs(self):
        self.check("vcvs")

    def test_vccs(self):
        self.check("vccs")

    def test_ccvs(self):
        self.check("ccvs")

    def test_cccs(self):
        self.check("cccs")

    def test_mutual_inductor(self):
        self.check("mutual_inductor")

    def test_diode(self):
        self.check("diode")

    def test_bjt(self):
        self.check("bjt")

    def test_jfet(self):
        self.check("jfet")

    def test_mos1(self):
        self.check("mos1")

    def test_mos2(self):
        self.check("mos2")

    def test_mos3(self):
        self.check("mos3")

    def test_bsim3v3(self):
        self.check("bsim3v3")

    def test_bsim4(self):
        self.check("bsim4")

    def test_master_set_is_the_v1_list(self):
        for master in ROWS:
            self.master_row(master)
        self.assertEqual(sorted(tables.SPECTRE_MASTERS), sorted(ROWS))

    def test_ref5_lists_are_complete_counts(self):
        """The section sizes of the ref5 chapters (a guard on the oracle itself): 1740 parameters."""
        counts = {m: (len(ref5_names(m, "card")), len(ref5_names(m, "instance"))) for m in ROWS}
        self.assertEqual(counts["mos1"], (167, 14))
        self.assertEqual(counts["bsim4"], (327, 31))
        self.assertEqual(counts["vsource"], (0, 47))
        self.assertEqual(counts["iprobe"], (0, 0))
        self.assertEqual(sum(a + b for a, b in counts.values()), 1740)


class TestDefaults(MastersCase):
    """Every default row writes a value at least one target would not simulate on its own (§3.9)."""

    def defaults(self):
        return [(master, section, r) for master, section, rules in self.every_list()
                for r in rules if r.action == "default"]

    def test_every_default_row_has_an_oracle_entry_and_vice_versa(self):
        keys = {(m, s, r.name) for m, s, r in self.defaults()}
        self.assertEqual(sorted(keys - set(TARGET_DEFAULTS)), [], "default rows without a TARGET_DEFAULTS entry")
        self.assertEqual(sorted(set(TARGET_DEFAULTS) - keys), [], "TARGET_DEFAULTS entries without a default row")

    def test_no_default_is_redundant(self):
        for master, section, r in self.defaults():
            with self.subTest(master=master, section=section, name=r.name):
                _spectre, vacask, xyce, _sources = TARGET_DEFAULTS[(master, section, r.name)]
                if r.value.startswith("="):            # a copy of another card parameter (fcs=fc, ikp=ik)
                    p = r.value[1:]
                    self.assertIn(p, ref5_names(master, section))
                    self.assertIn("given:%s" % p, r.when.split(","))
                    continue
                v = value_of(r.value)
                if section == "instance" and r.name in ("w", "l"):   # §3.9 writes the MOS geometry always
                    self.assertEqual(v, dict(self.master_row(master).geometry)[r.name])
                    continue
                redundant = all(isinstance(t, float) and same(t, v) for t in (vacask, xyce))
                self.assertFalse(redundant, "%s %s %s=%s: both targets already default to it (%r, %r)"
                                 % (master, section, r.name, r.value, vacask, xyce))

    def test_default_rows_are_conditional_on_absence(self):
        """A default is written only when the card omits the parameter (its when= names its own absence,
        or the Spectre name's for a target-only written name)."""
        for master, section, r in self.defaults():
            with self.subTest(master=master, name=r.name):
                spectre_name = {"nbv": "nz"}.get(r.name, r.name)
                self.assertIn("absent:%s" % spectre_name, r.when.split(","))
                self.assertTrue(r.value.startswith("=") or NUMBER.match(r.value), r.value)


class TestVocabulary(MastersCase):
    """The ParamRule grammar of §10 and the rows §3.9 decided."""

    def test_grammar(self):
        for master, section, rules in self.every_list():
            names = set(ref5_names(master, section)) | TARGET_ONLY.get(master, set())
            for r in rules:
                with self.subTest(master=master, section=section, rule=rule_key(r)):
                    self.assertIn(r.action, ACTIONS)
                    if r.action in ("rename", "default"):
                        self.assertTrue(r.value)
                    else:
                        self.assertEqual(r.value, "")
                    if r.action == "rename":
                        self.assertTrue(WORD.match(r.value), r.value)
                        self.assertNotEqual(r.value, r.name)
                    for tok in (r.when.split(",") if r.when else []):
                        kind, _, p = tok.partition(":")
                        self.assertIn(kind, ("absent", "given"))
                        self.assertIn(p, names)
                    for tok in r.match:
                        self.assertTrue(tok in ("absent", "nonzero") or WORD.match(tok) or NUMBER.match(tok), tok)
                    self.assertIsInstance(r.match, tuple)
                    self.assertIn(r.warn, WARNS)
                    self.assertTrue(r.cite, "every row cites its source")
                    if r.name in names and r.name not in TARGET_ONLY.get(master, set()):
                        self.assertRegex(r.cite, r"ref5 p\.\d+")

    def test_first_match_order(self):
        """Several rules may share a name; the first whose when and match hold applies (§10), so every
        rule but the last of a name must be conditional, or the later ones are dead."""
        for master, section, rules in self.every_list():
            last = {}
            for r in rules:
                prev = last.get(r.name)
                if prev is not None:
                    self.assertTrue(prev.when or prev.match, "%s %s: %s shadowed by an unconditional %s"
                                    % (master, section, r.name, prev.action))
                last[r.name] = r

    def test_rows_decided_in_section_3_9(self):
        for master, decided in DECIDED.items():
            have = {rule_key(r) for r in self.master_row(master).params}
            for d in decided:
                self.assertIn(d, have, "%s: %r" % (master, d))

    def test_equal_defaults_are_not_written(self):
        for master, names in NOT_WRITTEN.items():
            for r in self.master_row(master).params:
                if r.name in names:
                    self.assertNotEqual(r.action, "default", "%s: %s equals the targets' default" % (master, r.name))

    def test_folded_card_geometry(self):
        for master, names in FOLDED_CARD.items():
            row = self.master_row(master)
            for n in names.split():
                first = [r for r in row.params if r.name == n][0]
                self.assertEqual((first.action, first.when, first.match), ("fold", "", ()), "%s %s" % (master, n))

    def test_dispatch_and_kinds(self):
        for master in ROWS:
            row = self.master_row(master)
            if row.level is not None:
                self.assertIn((row.element, row.level), tables.DISPATCH, master)
            for _polarity, kind in row.kinds:
                self.assertEqual(tables.ELEMENT_OF[kind], row.element, master)
            if row.polarity_key:
                self.assertEqual(row.polarity_key, "type")
                self.assertEqual([tables.POLARITY[k] for _p, k in row.kinds], [1.0, -1.0], master)
            else:
                self.assertLessEqual(len(row.kinds), 1)


if __name__ == "__main__":
    unittest.main()
