"""E131: vamos's PSF writer against the vendored psf-parser, a section sign in the design title.

    python3 psf_latin1.py <repo> <psf_parser dir> <out dir>

vamos/output/psf.py writes its files in latin-1 (the sign is the byte 0xa7); psf-parser decodes UTF-8.
"""
import os
import sys

repo, parser_dir, out = sys.argv[1:4]
sys.path[:0] = [repo, parser_dir]
from vamos.output import psf  # noqa: E402
from vamos.spectre.results import AnalysisResult, Signal  # noqa: E402

head = psf.PsfHead(version="vamos (E131)", date="5:46:18 PM, Tue Apr 15, 2025", design="E131 § 3.1 title")
header = {"tolerance.relative": 1e-3, "reltol": 1e-3, "abstol(V)": 1e-6, "abstol(I)": 1e-12, "temp": 27.0,
          "tnom": 27.0, "tempeffects": "all", "gmindc": 1e-12}
res = AnalysisResult("op-dc", "dc", "op.dc", description="DC Analysis `op'", data_type="scalar", header=header,
                     signals=[Signal("out", "V", "V", [0.5])])
os.makedirs(out, exist_ok=True)
psf.write_analysis(out, res, head)
path = os.path.join(out, "op.dc")
with open(path, "rb") as fh:
    data = fh.read()
print("the section sign as latin-1 0xa7: %s; as UTF-8: %s" % (b"\xa7" in data, "§".encode("utf-8") in data))
import psf_parser  # noqa: E402  (vendored, MIT; Python >= 3.10)
try:
    psf_parser.PsfFile(path)
except UnicodeDecodeError as e:
    print("psf-parser: UnicodeDecodeError: %s" % e)
else:
    print("psf-parser: parsed")
