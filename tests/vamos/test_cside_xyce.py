"""Xyce-side C tests of the co-simulation patches (docs/VAMOS_AMS_DESIGN.md §7 P1, P5, ABI).

The Xyce patches (the finish protocol in the transient loop, BindCB's NULL
callback, xyce_cosim_abi in the C interface), exercised through the runner in
tests/vamos/fixtures/ams/cside_xyce/run_cside_xyce.py (also usable standalone;
it has the case list and the details).  One test per case:

    python3 -m unittest discover -s tests/vamos -p 'test_cside_xyce.py' -v

nvc's side, on both engines, is test_cside_engine.py.  Linux/WSL only
(everything is skipped elsewhere).  Environment:
VAMOS_XYCE, VAMOS_XYCE_LIBS   the Xyce binary and library directories (as in
                              vamos_testlib)
VAMOS_CSIDE_NVCB              the nvc build tree (default /usr/local/src/nvc-build)
VAMOS_CSIDE_XYCE_OLD_LIB      a libxyce.so without the patches, and
VAMOS_CSIDE_XYCE_OLD_CI       its libxycecinterface.so, for the cases that compare
                              with or refuse a pre-patch Xyce (default: the
                              *.pre-e2 copies in the Xyce build tree, when present;
                              "" = skip those cases)
VAMOS_TEST_KEEP               keep the scratch directory
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

from vamos_testlib import LINUX, fixture, needs_xyce

RUNNER = fixture("ams", "cside_xyce", "run_cside_xyce.py")


def _load_runner():
    # No __pycache__ in the fixture directory
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location("vamos_run_cside_xyce", RUNNER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved
    return mod


cxy = _load_runner()


@needs_xyce
class TestXyceSide(unittest.TestCase):
    """Shares one scratch root and one runner Env (stubs, probe, E1 work libraries)."""

    env = None
    scratch = None

    @classmethod
    def setUpClass(cls):
        if not LINUX:
            raise unittest.SkipTest("needs Linux/WSL: Xyce and nvc are Linux binaries")
        cls.scratch = tempfile.mkdtemp(prefix="vamos-cside-xyce-")
        cls.env = cxy.make_env(cls.scratch, nvcb=os.environ.get("VAMOS_CSIDE_NVCB"))
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


def _make(spec):
    def test(self):
        status, detail, _ = cxy.run_one(self.env, spec, os.path.join(self.scratch, "runs"))
        if status == "SKIP":
            self.skipTest(detail)
        if status == "FAIL":
            self.fail(detail)
    test.__doc__ = "%s: %s" % (spec.name, spec.doc)
    return test


def _register():
    # In a function: a module-level loop variable bound to a TestCase class
    # would be collected by the loader a second time.
    for spec in cxy.CASES:
        setattr(TestXyceSide, "test_" + spec.name, _make(spec))


_register()


if __name__ == "__main__":
    unittest.main()
