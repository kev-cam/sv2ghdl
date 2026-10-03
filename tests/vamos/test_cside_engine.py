"""C-side co-simulation tests (docs/VAMOS_AMS_DESIGN.md §7, §9 "C-side").

The engine patches P1-P7 in nvc (src/cosim.c), libcosim_bridge.so and the
VACASK/Xyce C interfaces, and §6's --stop-time femtosecond counts of 2^32 and
more (the test_stop_time_* cases), exercised through the runner in
tests/vamos/fixtures/ams/cside_engine/run_cside.py (also usable standalone; it
has the case list and the details).  One test per case and engine:

    python3 -m unittest discover -s tests/vamos -p 'test_cside_*.py' -v

Linux/WSL only (everything is skipped elsewhere).  Environment:
VAMOS_CSIDE_NVCB    the nvc build tree (default /usr/local/src/nvc-build)
VAMOS_VACASK_HOME   the VACASK build (default /opt/build.VACASK/Release)
VAMOS_XYCE_LIBS     the Xyce library directories (as in vamos_testlib)
VAMOS_CSIDE_XYCE_SHIM=1  run the Xyce cases through xyce_abi_shim.c (a Xyce
                    without the cosim patches); the finish cases are skipped
VAMOS_TEST_KEEP     keep the scratch directory
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

from vamos_testlib import LINUX, fixture, needs_vacask, needs_xyce

RUNNER = fixture("ams", "cside_engine", "run_cside.py")


def _load_runner():
    # No __pycache__ in the fixture directory
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location("vamos_run_cside", RUNNER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved
    return mod


cside = _load_runner()


class TestEndLineTimeText(unittest.TestCase):
    """The runner's fs_text, the exact femtosecond text of nvc's end lines (src/cosim.c
    fs_text): %.15g wherever that is exact, every digit otherwise.  Pure Python."""

    def test_matches_15g_when_exact(self):
        for n in (0, 1, 5, 120000000, 123456000, 99512345, 1000000005, 1108672366,
                  2000000006000, 1000000000000000, 1500000000000000, 3600000000000000000):
            self.assertEqual(cside.fs_text(n), "%.15g" % (n / 1e15), n)

    def test_all_digits_when_15g_rounds(self):
        # %.15g rounds these: 1.00000000000000 s, 3600 s, 2.00000000000001 s
        self.assertEqual(cside.fs_text(1000000000000001), "1.000000000000001")
        self.assertEqual(cside.fs_text(3600000000000000001), "3600.000000000000001")
        self.assertEqual(cside.fs_text(2000000000000006), "2.000000000000006")
        self.assertEqual(cside.fs_text(-1000000005), "-1.000000005e-06")


class _CSide(unittest.TestCase):
    """Shares one scratch root and one runner Env (compiled VHDL, stubs) per class."""

    env = None
    scratch = None

    @classmethod
    def setUpClass(cls):
        if not LINUX:
            raise unittest.SkipTest("needs Linux/WSL: nvc and the engines are Linux binaries")
        cls.scratch = tempfile.mkdtemp(prefix="vamos-cside-")
        xlibs = os.environ.get("VAMOS_XYCE_LIBS")
        cls.env = cside.Env(
            os.environ.get("VAMOS_CSIDE_NVCB", "/usr/local/src/nvc-build"),
            os.environ.get("VAMOS_VACASK_HOME", "/opt/build.VACASK/Release"),
            xlibs.split(os.pathsep) if xlibs else cside.default_xyce_libs(),
            cls.scratch,
            xyce_shim=os.environ.get("VAMOS_CSIDE_XYCE_SHIM") == "1")
        why = cls.env.suite_problem()
        if why:
            raise unittest.SkipTest(why)

    @classmethod
    def tearDownClass(cls):
        if cls.scratch is None:
            return
        if os.environ.get("VAMOS_TEST_KEEP"):
            sys.stderr.write("kept %s\n" % cls.scratch)
        else:
            shutil.rmtree(cls.scratch, ignore_errors=True)


@needs_vacask
class TestCSideVacask(_CSide):
    pass


@needs_xyce
class TestCSideXyce(_CSide):
    pass


def _make(spec, engine):
    def test(self):
        status, detail, _ = cside.run_one(self.env, spec, engine,
                                          os.path.join(self.scratch, "runs"))
        if status == "SKIP":
            self.skipTest(detail)
        if status == "FAIL":
            self.fail(detail)
    test.__doc__ = "%s [%s]: %s" % (spec.name, engine, spec.doc)
    return test


def _register():
    # In a function: a module-level loop variable bound to a TestCase class
    # would be collected by the loader a second time.
    for spec in cside.CASES:
        for engine, klass in (("vacask", TestCSideVacask), ("xyce", TestCSideXyce)):
            if engine in spec.engines:
                setattr(klass, "test_" + spec.name, _make(spec, engine))


_register()


if __name__ == "__main__":
    unittest.main()
