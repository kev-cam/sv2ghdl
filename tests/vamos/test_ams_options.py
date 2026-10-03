"""vcs-ams option parsing (docs/VAMOS_AMS_DESIGN.md §8, §9 option bullets).

    python3 -m unittest discover -s tests/vamos -p 'test_ams_options.py' -v
"""

import os
import unittest

from vamos_testlib import TempDir

from vamos.job import NOTED, UNKNOWN, UNSUPPORTED, Job  # noqa: E402
from vamos.optable import scan  # noqa: E402
from vamos.personalities import simv, vcs  # noqa: E402


def dispositions(job):
    return {u.option: u.disposition for u in job.unmapped}


class TestAdOptions(TempDir):
    def job(self, *args, personality="vcs"):
        return vcs.build_job(list(args), self.tmp, personality)

    def test_ad_forms_set_the_control_file(self):
        self.assertEqual(self.job("-ad", "tb.v").ams_control, "")
        self.assertEqual(self.job("+ad", "tb.v").ams_control, "")
        self.assertEqual(self.job("-ad=ctl.init", "tb.v").ams_control, os.path.join(self.tmp, "ctl.init"))
        self.assertEqual(self.job("+ad=/x/ctl.init", "tb.v").ams_control, "/x/ctl.init")

    def test_no_ad_is_digital(self):
        self.assertIsNone(self.job("tb.v").ams_control)

    def test_vcs_ams_personality_implies_ad(self):
        self.assertEqual(self.job("tb.v", personality="vcs-ams").ams_control, "")
        j = self.job("-ad=c.init", "tb.v", personality="vcs-ams")
        self.assertEqual(j.ams_control, os.path.join(self.tmp, "c.init"))
        self.assertEqual(j.personality, "vcs-ams")

    def test_neighbours_of_ad(self):
        j = self.job("-adopt", "x", "-ad_iereport", "tb.v")
        self.assertIsNone(j.ams_control)
        d = dispositions(j)
        self.assertEqual(d.get("-adopt x"), NOTED)
        self.assertEqual(d.get("-ad_iereport"), NOTED)
        self.assertEqual([os.path.basename(s.path) for s in j.sources], ["tb.v"])

    def test_ams_flow_options(self):
        j = self.job("-ams", "-sysc=ams", "-ams_discipline", "logic", "+verilogamsext+.vams",
                     "+bidir+tb.x", "a.vams", "r.va", "tb.v")
        d = dispositions(j)
        self.assertEqual(d.get("-ams"), UNSUPPORTED)
        self.assertEqual(d.get("-sysc=ams"), UNSUPPORTED)
        self.assertEqual(d.get("-ams_discipline logic"), NOTED)
        self.assertEqual(d.get("a.vams"), UNSUPPORTED)
        self.assertEqual(d.get("r.va"), UNSUPPORTED)
        self.assertNotIn("logic", [os.path.basename(s.path) for s in j.sources])
        self.assertEqual([os.path.basename(s.path) for s in j.sources], ["tb.v"])

    def test_override_timescale_is_mapped(self):
        j = self.job("-override_timescale=1ns/1ps", "tb.v")
        self.assertEqual(j.override_timescale, "1ns/1ps")
        self.assertNotIn("-override_timescale=1ns/1ps", dispositions(j))


class TestSimvOptions(TempDir):
    def rt(self, *args):
        rt = Job(personality="simv", argv=list(args), cwd=self.tmp)
        scan(simv.TABLE, list(args), rt, simv._positional, simv._unknown)
        return rt

    def test_ad_runopt_is_noted(self):
        self.assertEqual(dispositions(self.rt("-ad_runopt=+foo")).get("-ad_runopt=+foo"), NOTED)

    def test_vcs_finish(self):
        rt = self.rt("+vcs+finish+100", "+seed=3")
        self.assertEqual(rt.finish, "100")
        self.assertEqual(rt.plusargs, ["+seed=3"])
        compiled = Job(personality="vcs", precision="1ps")

        class Con:
            def err(self, _):
                pass
        self.assertEqual(simv.finish_fs(rt, compiled, Con()), 100 * 1000)
        self.assertIsNone(simv.finish_fs(rt, Job(personality="vcs"), Con()))

    def test_precision_parsing(self):
        self.assertEqual(simv.precision_fs("1ps"), 1000)
        self.assertEqual(simv.precision_fs("100 fs"), 100)
        self.assertEqual(simv.precision_fs("10ns"), 10 ** 7)
        self.assertIsNone(simv.precision_fs("3ns"))
        self.assertIsNone(simv.precision_fs(None))


if __name__ == "__main__":
    unittest.main()
