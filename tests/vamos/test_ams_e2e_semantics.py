"""vcs-ams end-to-end: Verilog case statements on an analog-driven inout (BIDIR) pad (TC-01).

While nothing digital drives an inout pad, the BIDIR A2D drives it at weak strength (L3D_H /
L3D_L, like VCS's resistance-derived strength for an inout a2d).  Verilog case equality
ignores strength, so `case (pad) 1'b1:' must match a weak 1, and so must `case (1'b1) pad:',
a casez, a reg copied from the pad and a net assigned from it.  Each design is compiled with
vcs-ams and run on every available analog engine (one test method per engine); the sampled
case decisions are compared with vvp running the same testbench with a Verilog model of the
SPICE cell (a pullup, or a pull-strength driver).

Needs the whole stack (nvc, iverilog, VACASK and/or Xyce): WSL/Linux.

    cd tests/vamos && python3 -m unittest test_ams_e2e_semantics -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple

from ams_e2e_lib import AmsCase, engines_available, needs_ams

IVERILOG = os.environ.get("VAMOS_TEST_IVERILOG", "/usr/local/src/iverilog/_install/bin/iverilog")
VVP = os.environ.get("VAMOS_TEST_VVP", "/usr/local/src/iverilog/_install/bin/vvp")

# Sampled after settling: case decisions only, no event counts (the analog transition passes
# through x on its way between the A2D thresholds, which vvp's ideal pull never does).
# CASEZ is left out of one design so that, without the fix, that design still compiles and
# shows the silent mis-simulation (a scalar casez used not to compile at all).
CASEZ = """\
      casez (pad) 1'b1: $display("@ %0d casez 1", tag); 1'b0: $display("@ %0d casez 0", tag);
                  default: $display("@ %0d casez default", tag); endcase
"""
_SAMPLE = """\
  task sample(input integer tag);
    begin
      case (pad) 1'b1: $display("@ %0d case 1", tag); 1'b0: $display("@ %0d case 0", tag);
                 default: $display("@ %0d case default", tag); endcase
@CASEZ@      case (1'b1) pad: $display("@ %0d case(1) hit", tag); default: $display("@ %0d case(1) default", tag); endcase
      r = pad;
      case (r) 1'b1: $display("@ %0d case r 1", tag); 1'b0: $display("@ %0d case r 0", tag);
               default: $display("@ %0d case r default", tag); endcase
      case (o) 1'b1: $display("@ %0d case o 1", tag); 1'b0: $display("@ %0d case o 0", tag);
               default: $display("@ %0d case o default", tag); endcase
      if (pad === 1'b1) $display("@ %0d ===1", tag); else if (pad === 1'b0) $display("@ %0d ===0", tag);
      else $display("@ %0d === other", tag);
    end
  endtask
"""

# A pad with an internal 10k pull-up; the testbench drives it low, then releases it.
PULLUP_SP = """\
* pad with an internal 10k pull-up to vdd
.subckt padcell pad
rpu pad vdd 10k
cpad pad 0 10f
.ends
.global vdd
vsup vdd 0 1.8
"""
PULLUP_MODEL = """\
module padcell(inout pad);
  pullup (pad);
endmodule
"""
PULLUP_TB = """\
`timescale 1ns/1ps
module tb;
  wire pad;
  reg en = 1, d = 0;
  reg r;
  wire o;
  assign o = pad;
  assign pad = en ? d : 1'bz;
  padcell u (.pad(pad));
""" + _SAMPLE.replace("@CASEZ@", CASEZ) + """\
  initial begin
    #10 sample(1);
    en = 0;               // release: the internal pull-up takes the pad to 1
    #40 sample(2);
    $finish;
  end
endmodule
"""

# A pad driven from the analog side through 1k by a pulse; the digital side never drives it.
PULSE_SP = """\
* pad driven from the analog side through 1k by a pulse source
.subckt padcell pad
rdrv pad drv 1k
vdrv drv 0 pulse(0 1.8 10n 1n 1n 10n 22n)
cpad pad 0 10f
.ends
"""
PULSE_MODEL = """\
`timescale 1ns/1ps
module padcell(inout pad);
  reg v = 0;
  initial begin #10.5; forever begin v = 1; #11; v = 0; #11; end end
  assign (pull1, pull0) pad = v;
endmodule
"""
PULSE_TB = """\
`timescale 1ns/1ps
module tb;
  wire pad;
  reg en = 0, d = 0;
  reg r;
  wire o;
  assign o = pad;
  assign pad = en ? d : 1'bz;
  padcell u (.pad(pad));
""" + _SAMPLE.replace("@CASEZ@", "") + """\
  initial begin
    #16 sample(1);
    #11 sample(2);
    #11 sample(3);
    #11 sample(4);
    $finish;
  end
endmodule
"""

INIT = "choose xa cells.sp;\nport_dir -cell padcell (inout pad);\n"


def tagged(text: str) -> List[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("@ ")]


class _CaseOnPad(AmsCase):
    """Compiles and runs each engine once per class."""

    SP = ""
    MODEL = ""
    TB = ""
    _root = None          # type: Optional[str]
    _cache = None         # type: Optional[Dict[str, Tuple[subprocess.CompletedProcess, Optional[subprocess.CompletedProcess]]]]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._root = tempfile.mkdtemp(prefix="vamos-e2e-sem-%s-" % cls.__name__)
        cls._cache = {}

    @classmethod
    def tearDownClass(cls):
        if cls._root:
            if os.environ.get("VAMOS_TEST_KEEP"):
                print("kept %s" % cls._root)
            else:
                shutil.rmtree(cls._root, ignore_errors=True)
        super().tearDownClass()

    def _write(self, d: str, files: Dict[str, str]) -> None:
        os.makedirs(d, exist_ok=True)
        for rel, text in files.items():
            with open(os.path.join(d, rel), "w") as fh:
                fh.write(text)

    def ams(self, engine: str):
        if engine not in self._cache:
            d = os.path.join(self._root, engine)
            self._write(d, {"tb.sv": self.TB, "cells.sp": self.SP, "vcsAD.init": INIT})
            comp = self.compile(d, "-sverilog", "tb.sv", engine=engine, expect_rc=None)
            sim = None
            if comp.returncode == 0 and os.path.isfile(os.path.join(d, "simv")):
                sim = self.simv(d, expect_rc=None)
            self._cache[engine] = (comp, sim)
        return self._cache[engine]

    def vvp(self) -> str:
        if "vvp" not in self._cache:
            if not (os.access(IVERILOG, os.X_OK) and os.access(VVP, os.X_OK)):
                self.skipTest("needs iverilog/vvp at %s" % IVERILOG)
            d = os.path.join(self._root, "vvp")
            self._write(d, {"tb.sv": self.TB, "models.v": self.MODEL})
            r = subprocess.run([IVERILOG, "-g2012", "-o", "tb.vvp", "tb.sv", "models.v"], cwd=d,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, errors="replace", timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout)
            r = subprocess.run([VVP, "-n", "tb.vvp"], cwd=d, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True,
                               errors="replace", timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout)
            self._cache["vvp"] = r.stdout
        return self._cache["vvp"]

    def check_like_vvp(self, engine: str):
        if engine not in engines_available():
            self.skipTest("%s is not installed" % engine)
        comp, sim = self.ams(engine)
        self.assertEqual(comp.returncode, 0, comp.stdout)
        self.assertIsNotNone(sim, comp.stdout)
        self.assertEqual(sim.returncode, 0, sim.stdout)
        want = tagged(self.vvp())
        self.assertTrue(want)
        self.assertEqual(tagged(sim.stdout), want,
                         "\n--- simv:\n%s\n--- vvp:\n%s" % (sim.stdout, self.vvp()))


@needs_ams
class TestCaseOnReleasedPad(_CaseOnPad):
    """A released inout pad held at 1 by the cell's own 10k pull-up (weak A2D 1)."""

    SP, MODEL, TB = PULLUP_SP, PULLUP_MODEL, PULLUP_TB

    def test_like_vvp_vacask(self):
        self.check_like_vvp("vacask")

    def test_like_vvp_xyce(self):
        self.check_like_vvp("xyce")

    def test_weak_one_matches(self):
        eng = engines_available()[0]
        comp, sim = self.ams(eng)
        self.assertIsNotNone(sim, comp.stdout)
        got = tagged(sim.stdout)
        for want in ("@ 2 case 1", "@ 2 casez 1", "@ 2 case(1) hit", "@ 2 case r 1",
                     "@ 2 case o 1"):
            self.assertIn(want, got)


@needs_ams
class TestCaseOnAnalogDrivenPad(_CaseOnPad):
    """A pad the analog side drives through 1k while the digital side is released."""

    SP, MODEL, TB = PULSE_SP, PULSE_MODEL, PULSE_TB

    def test_like_vvp_vacask(self):
        self.check_like_vvp("vacask")

    def test_like_vvp_xyce(self):
        self.check_like_vvp("xyce")


if __name__ == "__main__":
    unittest.main()
