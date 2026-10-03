"""daidir layout for the AMS flow (docs/VAMOS_AMS_DESIGN.md §1.1).

Nothing AMS writes goes under <daidir>/nvc/: iverilog-sv2ghdl deletes that
directory on every translation.  <daidir>/ams/ is cleared at the start of
every AMS compile.  Paths stored in the job's "ams" record are relative to
the daidir.
"""

from __future__ import annotations

import os
import shutil

AMS_DIR = "ams"
RECORD_VERSION = 1
ANALYSIS = "vamos_tran"
RAW = ANALYSIS + ".raw"
DEFAULT_PREFIX = "vamos_ams"
ABI = 2

DECK_NAME = {"vacask": "vamos.sim", "xyce": "vamos.cir"}


def ams_dir(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR)


def pp(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR, "pp.v")


def pp_orig(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR, "pp.orig.v")


def cut_vhd(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR, "cut.vhd")


def deck_dir(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR, "deck")


def deck(daidir: str, engine: str) -> str:
    return os.path.join(deck_dir(daidir), DECK_NAME[engine])


def va_dir(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR, "va")


def boundary(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR, "vamos.boundary")


def plan_json(daidir: str) -> str:
    return os.path.join(daidir, AMS_DIR, "ams.json")


def ie_report(exe: str) -> str:
    return exe + ".msv" + os.sep + "interface_element.rpt"


def plain_pp(daidir: str) -> str:
    """pp.v of a plain (digital) vcs compile that ran the preprocess step (-override_timescale)."""
    return os.path.join(daidir, "pp", "pp.v")


def nvc_dir(daidir: str) -> str:
    return os.path.join(daidir, "nvc")


def work_lib(daidir: str) -> str:
    return os.path.join(daidir, "nvc", "work")


def rel(daidir: str, path: str) -> str:
    return os.path.relpath(path, daidir)


def resolve(daidir: str, relpath: str) -> str:
    return os.path.normpath(os.path.join(daidir, relpath))


def reset(daidir: str) -> None:
    """Clear <daidir>/ams at the start of an AMS compile."""
    d = ams_dir(daidir)
    shutil.rmtree(d, ignore_errors=True)
    for sub in (d, deck_dir(daidir), va_dir(daidir)):
        os.makedirs(sub, exist_ok=True)
