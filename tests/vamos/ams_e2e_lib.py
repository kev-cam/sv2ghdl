"""Helpers for the vcs-ams end-to-end tests (docs/VAMOS_AMS_DESIGN.md §9).

They need the whole stack (nvc, iverilog, VACASK and/or Xyce): Linux/WSL.

    class TestRc(AmsCase):
        def test_rc(self):
            for engine in self.engines():
                with self.subTest(engine=engine):
                    d = self.case("rc_" + engine, {"tb.sv": TB, "rc.sp": SP, "vcsAD.init": INIT})
                    self.compile(d, "-sverilog", "tb.sv", engine=engine)
                    out = self.simv(d)
                    raw = self.raw(d)
                    self.assertAlmostEqual(raw.at("n_u1_out", 60e-9), 2.0, delta=0.1)
"""

import os
import subprocess
import unittest
from typing import Dict, List, Optional

from vamos_testlib import ROOT, TempDir, have_stack, have_vacask, have_xyce

SHIMS = os.path.join(ROOT, "shims")
TIMEOUT = 1200


def engines_available() -> List[str]:
    return [e for e, ok in (("vacask", have_vacask()), ("xyce", have_xyce())) if ok]


needs_ams = unittest.skipUnless(have_stack() and engines_available(),
                                "needs nvc + iverilog + an analog engine (Linux/WSL)")


class AmsCase(TempDir):
    """A scratch directory per test plus vcs-ams / simv runners."""

    def engines(self) -> List[str]:
        return engines_available()

    def case(self, name: str, files: Dict[str, str]) -> str:
        """Create <tmp>/<name>/ with the given files (relative paths, sub-dirs allowed)."""
        d = os.path.join(self.tmp, name)
        for rel, text in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        os.makedirs(d, exist_ok=True)
        return d

    def child_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        # (not "_env": TempDir keeps the saved environment in self._env)
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("VAMOS_ANALOG", None)
        if extra:
            env.update(extra)
        return env

    def run_cmd(self, cmd: List[str], cwd: str, env: Optional[Dict[str, str]] = None,
                timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=cwd, env=self.child_env(env), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                              timeout=timeout)

    def compile(self, d: str, *args: str, engine: Optional[str] = None, tool: str = "vcs-ams",
                expect_rc: Optional[int] = 0, env: Optional[Dict[str, str]] = None
                ) -> subprocess.CompletedProcess:
        """Run vcs-ams (or `tool`) in d; asserts the exit status unless expect_rc is None."""
        cmd = [tool] + list(args)
        if engine:
            cmd.append("--vamos-analog=" + engine)
        r = self.run_cmd(cmd, d, env)
        if expect_rc is not None:
            self.assertEqual(r.returncode, expect_rc, "%s\n%s" % (" ".join(cmd), r.stdout))
        return r

    def simv(self, d: str, *args: str, exe: str = "./simv", expect_rc: Optional[int] = 0,
             cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        r = self.run_cmd([exe] + list(args), cwd or d)
        if expect_rc is not None:
            self.assertEqual(r.returncode, expect_rc, r.stdout)
        return r

    def raw(self, d: str, name: str = "vamos_ams.raw"):
        from vamos.netlist import rawfile
        return rawfile.read(os.path.join(d, name))

    def report(self, d: str, exe: str = "simv") -> str:
        # report.py writes UTF-8 whatever the locale (the direction comments hold arrows)
        with open(os.path.join(d, exe + ".msv", "interface_element.rpt"), encoding="utf-8") as fh:
            return fh.read()

    def lines_with(self, text: str, needle: str) -> List[str]:
        return [ln for ln in text.splitlines() if needle in ln]

    def display_values(self, out: str, tag: str) -> List[str]:
        """Values printed by `$display("%0t <tag>=%b", ...)` lines, in order: ['0', '1', ...]."""
        vals = []
        for ln in out.splitlines():
            parts = ln.split()
            for p in parts:
                if p.startswith(tag + "="):
                    vals.append(p[len(tag) + 1:])
        return vals

    def display_events(self, out: str, tag: str) -> List[tuple]:
        """(time, value) pairs from `$display("%0t <tag>=%b", $time, x)` lines."""
        ev = []
        for ln in out.splitlines():
            parts = ln.split()
            if len(parts) >= 2 and parts[1].startswith(tag + "=") and parts[0].isdigit():
                ev.append((int(parts[0]), parts[1][len(tag) + 1:]))
        return ev
