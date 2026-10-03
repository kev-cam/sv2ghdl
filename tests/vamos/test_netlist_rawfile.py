"""SPICE rawfile reader and the point-count repair (docs/VAMOS_AMS_DESIGN.md §4.6, §6).

    python3 -m unittest discover -s tests/vamos -p 'test_netlist_rawfile*.py' -v

The raw_* fixtures were written by the engines themselves from the raw_*.sim
and raw_*.cir decks next to them (VACASK binary/ASCII transient, AC and
operating point; Xyce `.print ... format=raw` binary, `-a` ASCII, AC).  The
engine classes (WSL) run those decks again and require the fresh output to
read the same way.
"""

import os
import shutil
import unittest

from vamos_testlib import TempDir, fixture, needs_vacask, needs_xyce, run, vacask_bin, xyce_bin, \
    xyce_env

from vamos.netlist import rawfile  # noqa: E402
from vamos.netlist.rawfile import RawError, fix_points, read, read_all  # noqa: E402


def fx(name):
    return fixture("netlist", name)


def close(a, b, rel=1e-12, abs_=1e-15):
    return abs(a - b) <= max(abs_, rel * abs(b))


class TestReadFixtures(unittest.TestCase):
    def test_vacask_transient(self):
        for name in ("raw_vacask_tran.raw", "raw_vacask_tran_ascii.raw"):
            r = read(fx(name))
            self.assertEqual(r.title, "rc fixture")
            self.assertEqual(r.plotname, "Transient Analysis")
            self.assertEqual(r.flags, ["real"])
            self.assertFalse(r.complex)
            self.assertEqual(r.binary, name == "raw_vacask_tran.raw")
            self.assertEqual(r.variables, [("time", "notype"), ("a", "notype"), ("b", "notype"),
                                           ("v1:flow(br)", "notype"), ("x1:n", "notype")])
            self.assertEqual(r.declared_points, 60)
            self.assertEqual(len(r.points), 60)
            self.assertEqual(r.points[0], (0.0, 0.0, 0.0, 0.0, 0.0))
            self.assertEqual(r.last_time(), 1e-9)
            self.assertEqual(len(r.time()), 60)
            self.assertTrue(close(r.column("b")[-1], 0.4735949300147547, 1e-15))
            self.assertTrue(close(r.column("i(v1)")[1], -6.213887591790119e-05, 1e-15))

    def test_binary_and_ascii_agree(self):
        for b, a in (("raw_vacask_tran.raw", "raw_vacask_tran_ascii.raw"),
                     ("raw_xyce_tran.raw", "raw_xyce_tran_ascii.raw"),
                     ("raw_vacask_ac.raw", "raw_vacask_ac_ascii.raw")):
            rb, ra = read(fx(b)), read(fx(a))
            self.assertEqual(len(rb.points), len(ra.points))
            self.assertEqual(rb.names(), ra.names())
            for pb, pa in zip(rb.points, ra.points):
                for x, y in zip(pb, pa):
                    self.assertTrue(close(x, y, 1e-7, 1e-12), (b, x, y))   # Xyce ASCII: 9 digits

    def test_xyce_transient(self):
        r = read(fx("raw_xyce_tran.raw"))
        self.assertEqual(r.title, "* rc fixture")
        self.assertEqual(r.variables, [("TIME", "time"), ("V(A)", "voltage"), ("V(B)", "voltage"),
                                       ("I(V1)", "current"), ("V(X1:N)", "voltage")])
        self.assertEqual((r.declared_points, len(r.points)), (37, 37))
        self.assertEqual(r.last_time(), 1e-9)
        self.assertTrue(close(r.column("v(b)")[-1], 0.4736988977289292, 1e-15))

    def test_complex(self):
        for name, scale in (("raw_vacask_ac.raw", "frequency"), ("raw_vacask_ac_ascii.raw", "frequency"),
                            ("raw_xyce_ac.raw", "FREQUENCY")):
            r = read(fx(name))
            self.assertTrue(r.complex, name)
            self.assertEqual(r.names()[0], scale)
            self.assertEqual(len(r.points), 4)
            self.assertTrue(all(isinstance(v, complex) for p in r.points for v in p))
            vb = r.column("v(b)")[2]                    # 100 MHz into RC = 1k, 1p
            want = 1 / complex(1, 2 * 3.141592653589793 * 1e8 * 1e3 * 1e-12)
            self.assertTrue(abs(vb - want) < 1e-12, (name, vb, want))
            with self.assertRaises(RawError):
                r.time()

    def test_operating_point(self):
        r = read(fx("raw_vacask_op.raw"))
        self.assertEqual(r.plotname, "Operating Point")
        self.assertEqual(r.points, [(1.0, 0.5, -0.0005)])
        self.assertEqual(r.column("i(v1)"), [-0.0005])
        with self.assertRaises(RawError):
            r.last_time()

    def test_column_spellings_across_engines(self):
        x, v = read(fx("raw_xyce_tran.raw")), read(fx("raw_vacask_tran.raw"))
        for r, col in ((x, 2), (v, 2)):
            for q in ("v(b)", "V(B)", "b", "B", " v( b ) "):
                self.assertEqual(r.index(q), col, (r.path, q))
        self.assertEqual(x.index("i(v1)"), 3)
        self.assertEqual(v.index("I(V1)"), 3)
        self.assertEqual(v.index("v1:flow(br)"), 3)
        self.assertEqual(x.index("v(x1.n)"), 4)               # HSPICE '.' hierarchy
        self.assertEqual(v.index("v(x1.n)"), 4)
        self.assertEqual(v.index("X1:N"), 4)
        self.assertEqual(x.index("time"), 0)
        self.assertEqual(v.index("TIME"), 0)
        for r in (x, v):
            d = r.column("v(a,b)")
            self.assertTrue(all(close(p[1] - p[2], q) for p, q in zip(r.points, d)))
            self.assertEqual(r.column("v(b, 0)"), r.column("b"))
        with self.assertRaises(KeyError) as cm:
            x.column("v(nope)")
        self.assertIn("V(A)", str(cm.exception))                 # lists what there is

    def test_sampling(self):
        # v(a) ramps 0 -> 1 V in 0.1 ns from t = 0 on both engines
        for name in ("raw_xyce_tran.raw", "raw_vacask_tran.raw"):
            r = read(fx(name))
            self.assertTrue(close(r.at("v(a)", 5e-11), 0.5, 1e-9), name)
            self.assertEqual(r.at("v(a)", 2e-10), 1.0)
            self.assertEqual(r.at("v(a)", 0.0), 0.0)
            self.assertEqual(r.at("time", 1e-9), 1e-9)
            [t] = r.crossings("v(a)", 0.5)
            self.assertTrue(close(t, 5e-11, 1e-9), (name, t))
            self.assertEqual(r.crossings("v(a)", 0.5, -1), [])
            self.assertEqual(len(r.crossings("v(b)", 0.25, +1)), 1)
            for bad in (-1e-12, 2e-9):
                with self.assertRaises(RawError):
                    r.at("v(a)", bad)
        x, v = read(fx("raw_xyce_tran.raw")), read(fx("raw_vacask_tran.raw"))
        tx, tv = x.crossings("v(b)", 0.25)[0], v.crossings("v(b)", 0.25)[0]
        self.assertTrue(close(tx, tv, 0.01), (tx, tv))            # the engines agree within 1 %
        r = rawfile.Raw()
        r.variables = [("time", "time"), ("y", "voltage")]
        r.points = [(0.0, 0.0), (1.0, 1.0), (2.0, 1.0), (3.0, 0.0), (4.0, 1.0)]
        self.assertEqual(r.crossings("y", 1.0), [1.0, 4.0])       # each arrival at the level, once
        self.assertEqual(r.crossings("y", 0.5), [0.5, 2.5, 3.5])
        self.assertEqual(r.crossings("y", 0.5, -1), [2.5])

    def test_element_currents_the_emitters_save(self):
        """VACASK saves a resistor's current as p(r1, i), a column 'r1.i'; the Xyce emitter
        prints a behavioral element eb as BEB (xyce.printed).  i() of the IR name finds both."""
        v = rawfile.Raw()
        v.variables = [("time", "notype"), ("r1.i", "notype"), ("x1:rx.i", "notype"),
                       ("eb:flow(br)", "notype"), ("a.i", "notype")]
        v.points = [(0.0, 1.0, 2.0, 3.0, 4.0)]
        self.assertEqual(v.index("i(r1)"), 1)
        self.assertEqual(v.index("I(X1.RX)"), 2)
        self.assertEqual(v.index("i(eb)"), 3)
        self.assertEqual(v.index("v(a.i)"), 4)                 # still a node named a.i too
        self.assertEqual(v.index("i(a)"), 4)
        x = rawfile.Raw()
        x.variables = [("TIME", "time"), ("I(BEB)", "current"), ("I(X1:BG_N)", "current"),
                       ("I(R1)", "current")]
        x.points = [(0.0, 1.0, 2.0, 3.0)]
        self.assertEqual(x.index("i(eb)"), 1)
        self.assertEqual(x.index("i(beb)"), 1)
        self.assertEqual(x.index("i(x1.g_n)"), 2)
        self.assertEqual(x.index("i(r1)"), 3)
        with self.assertRaises(KeyError):
            x.index("i(1)")                                    # 'R1' is no B element

    def test_ambiguous_name(self):
        r = rawfile.Raw()
        r.variables = [("time", "time"), ("V(OUT)", "voltage"), ("out", "notype")]
        r.points = [(0.0, 1.0, 2.0)]
        with self.assertRaises(KeyError) as cm:
            r.index("v( out )")                                 # no exact match, two by node
        self.assertIn("several", str(cm.exception))
        self.assertEqual(r.index("out"), 2)                     # an exact spelling wins
        self.assertEqual(r.index("V(out)"), 1)


def blank(path, value=b""):
    """Rewrite the No. Points: value field (keeping its width) - Xyce's paused state."""
    with open(path, "rb") as fh:
        data = fh.read()
    start = data.index(b"No. Points: ") + len(b"No. Points: ")
    end = data.index(b"\n", start)
    with open(path, "wb") as fh:
        fh.write(data[:start] + value.ljust(end - start) + data[end:])


class TestPointCount(TempDir):
    def copy(self, name):
        p = os.path.join(self.tmp, name)
        shutil.copy(fx(name), p)
        return p

    def test_blank_count_xyce_binary(self):
        p = self.copy("raw_xyce_tran.raw")
        size = os.path.getsize(p)
        blank(p)
        r = read(p)
        self.assertIsNone(r.declared_points)
        self.assertEqual(len(r.points), 37)
        self.assertEqual(r.last_time(), 1e-9)
        self.assertTrue(fix_points(p))
        self.assertEqual(os.path.getsize(p), size)                # in place
        with open(p, "rb") as fh, open(fx("raw_xyce_tran.raw"), "rb") as orig:
            self.assertEqual(fh.read(), orig.read())              # exactly what Xyce writes
        self.assertFalse(fix_points(p))

    def test_blank_count_ascii(self):
        for name, n in (("raw_vacask_tran_ascii.raw", 60), ("raw_xyce_tran_ascii.raw", 37)):
            p = self.copy(name)
            blank(p)
            self.assertEqual(len(read(p).points), n)
            self.assertTrue(fix_points(p))
            self.assertEqual(read(p).declared_points, n)

    def test_wrong_count(self):
        p = self.copy("raw_vacask_tran.raw")
        blank(p, b"99")
        r = read(p)
        self.assertEqual((r.declared_points, len(r.points)), (99, 60))
        self.assertTrue(fix_points(p))
        self.assertEqual(read(p).declared_points, 60)
        p = self.copy("raw_xyce_tran.raw")
        blank(p, b"12")                                           # too small: data follows
        self.assertEqual(len(read(p).points), 37)

    def test_truncated_binary(self):
        p = self.copy("raw_xyce_tran.raw")
        with open(p, "rb") as fh:
            data = fh.read()
        with open(p, "wb") as fh:
            fh.write(data[:-12])                                  # a partial last point
        r = read(p)
        self.assertEqual((r.declared_points, len(r.points)), (37, 36))
        self.assertTrue(fix_points(p))
        self.assertEqual(read(p).declared_points, 36)

    def test_truncated_ascii(self):
        p = self.copy("raw_xyce_tran_ascii.raw")
        with open(p, "rb") as fh:
            data = fh.read()
        with open(p, "wb") as fh:
            fh.write(data[:data.rindex(b"\t")])                   # lose the last value
        self.assertEqual(len(read(p).points), 36)

    def test_count_wider_than_field(self):
        p = os.path.join(self.tmp, "narrow.raw")
        with open(fx("raw_xyce_tran_ascii.raw"), "rb") as fh:
            data = fh.read()
        start = data.index(b"No. Points:")
        end = data.index(b"\n", start)
        with open(p, "wb") as fh:
            fh.write(data[:start] + b"No. Points: 5" + data[end:])
        self.assertTrue(fix_points(p))
        with open(p, "rb") as fh:
            self.assertIn(b"\nNo. Points: 37\n", fh.read())
        self.assertEqual(read(p).declared_points, 37)
        self.assertFalse(os.path.exists(p + ".vamos-fix"))

    def test_missing_count_line(self):
        p = os.path.join(self.tmp, "nocount.raw")
        with open(fx("raw_vacask_tran.raw"), "rb") as fh:
            data = fh.read()
        start = data.index(b"No. Points:")
        end = data.index(b"\n", start) + 1
        with open(p, "wb") as fh:
            fh.write(data[:start] + data[end:])
        self.assertEqual(len(read(p).points), 60)
        self.assertTrue(fix_points(p))
        r = read(p)
        self.assertEqual((r.declared_points, len(r.points)), (60, 60))

    def test_several_plots(self):
        for name, n in (("raw_xyce_tran.raw", 37), ("raw_vacask_tran_ascii.raw", 60)):
            p = os.path.join(self.tmp, "two_" + name)
            with open(fx(name), "rb") as fh:
                data = fh.read()
            with open(p, "wb") as fh:
                fh.write(data + data.replace(b"Transient Analysis", b"Second Analysis"))
            plots = read_all(p)
            self.assertEqual([r.plotname for r in plots], ["Transient Analysis", "Second Analysis"])
            self.assertEqual([len(r.points) for r in plots], [n, n])
            self.assertEqual(read(p).plotname, "Transient Analysis")
            self.assertFalse(fix_points(p))

    def test_crlf_and_padded(self):
        p = os.path.join(self.tmp, "crlf.raw")
        with open(fx("raw_xyce_tran_ascii.raw"), "rb") as fh:
            data = fh.read().replace(b"\n", b"\r\n").replace(b"Flags: real", b"Flags: real padded")
        with open(p, "wb") as fh:
            fh.write(data)
        r = read(p)
        self.assertEqual((r.flags, len(r.points)), (["real", "padded"], 37))
        blank(p)
        self.assertTrue(fix_points(p))
        self.assertEqual(read(p).declared_points, 37)
        p = os.path.join(self.tmp, "padded.raw")                  # binary, ngspice-style flags
        with open(fx("raw_vacask_ac.raw"), "rb") as fh:
            data = fh.read().replace(b"Flags: complex", b"Flags: complex padded")
        with open(p, "wb") as fh:
            fh.write(data)
        self.assertEqual(read(p).points, read(fx("raw_vacask_ac.raw")).points)

    def test_not_a_rawfile(self):
        cases = {"empty.raw": b"", "text.raw": b"hello world\n",
                 "lt.raw": b"Title: x\nFlags: real forward\nNo. Variables: 1\nNo. Points: 0\n"
                           b"Variables:\n\t0\ttime\ttime\nBinary:\n",
                 "nodata.raw": b"Title: x\nNo. Variables: 1\nVariables:\n\t0\ttime\ttime\n",
                 "vars.raw": b"Title: x\nNo. Variables: 2\nVariables:\n\t0\ttime\ttime\nValues:\n",
                 "junk.raw": b"Title: x\nNo. Variables: 1\nVariables:\n\t0\ttime\ttime\nValues:\n"
                             b"0\t1.0\nbad\n"}
        for name, data in cases.items():
            p = os.path.join(self.tmp, name)
            with open(p, "wb") as fh:
                fh.write(data)
            with self.assertRaises(RawError, msg=name):
                read(p)


class _Regenerate(TempDir):
    """Run a fixture deck again and require its fresh output to read like the fixture."""

    def same(self, fresh, fixture_name):
        a, b = read(fresh), read(fx(fixture_name))
        self.assertEqual((a.names(), a.flags, a.plotname, a.binary),
                         (b.names(), b.flags, b.plotname, b.binary))
        self.assertEqual(a.declared_points, len(a.points))
        for x, y in zip(a.points[-1], b.points[-1]):
            self.assertTrue(close(x, y, 1e-6, 1e-12), (fresh, a.points[-1], b.points[-1]))


@needs_vacask
class TestVacaskOutput(_Regenerate):
    def test_decks(self):
        for deck, out, fixture_name in (("raw_vacask_tran.sim", "tran1.raw", "raw_vacask_tran.raw"),
                                        ("raw_vacask_tran_ascii.sim", "tran1.raw",
                                         "raw_vacask_tran_ascii.raw"),
                                        ("raw_vacask_ac.sim", "ac1.raw", "raw_vacask_ac.raw"),
                                        ("raw_vacask_ac_ascii.sim", "ac2.raw", "raw_vacask_ac_ascii.raw"),
                                        ("raw_vacask_op.sim", "op1.raw", "raw_vacask_op.raw")):
            d = os.path.join(self.tmp, deck)
            os.makedirs(d)
            shutil.copy(fx(deck), d)
            from vamos.ams import engines
            env = dict(os.environ)
            if engines.vacask_module_path():
                env["SIM_MODULE_PATH"] = engines.vacask_module_path()
            r = run([vacask_bin(), deck], cwd=d, env=env)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            self.same(os.path.join(d, out), fixture_name)


@needs_xyce
class TestXyceOutput(_Regenerate):
    def test_decks(self):
        for deck, flags, out, fixture_name in (("raw_xyce_tran.cir", [], "xtran.raw", "raw_xyce_tran.raw"),
                                               ("raw_xyce_tran.cir", ["-a"], "xtran.raw",
                                                "raw_xyce_tran_ascii.raw"),
                                               ("raw_xyce_ac.cir", [], "xac.raw", "raw_xyce_ac.raw")):
            d = os.path.join(self.tmp, fixture_name)
            os.makedirs(d)
            shutil.copy(fx(deck), d)
            r = run([xyce_bin()] + flags + [deck], cwd=d, env=xyce_env())
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            self.same(os.path.join(d, out), fixture_name)


if __name__ == "__main__":
    unittest.main()
