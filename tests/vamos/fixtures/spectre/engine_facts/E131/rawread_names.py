"""E131: VACASK's python/rawfile.py rawread on a binary rawfile of two plots whose second variable differs.

    python3 rawread_names.py <VACASK source tree> <out dir>

rawread keeps one header dict for every plot of a file, so each plot gets the last plot's names; the
arrays are each plot's own.
"""
import importlib.util
import os
import struct
import sys

src, out = sys.argv[1:3]
os.makedirs(out, exist_ok=True)
path = os.path.join(out, "two_plots.raw")


def plot(name, rows):
    head = ("Title: E131 two plots\nDate: Sat Oct 17 08:00:00 2026\nPlotname: Transient Analysis\nFlags: real\n"
            "No. Variables: 2\nNo. Points: %d\nVariables:\n\t0\ttime\ttime\n\t1\t%s\tvoltage\nBinary:\n"
            % (len(rows), name)).encode("ascii")
    return head + b"".join(struct.pack("<2d", *r) for r in rows)


with open(path, "wb") as fh:
    fh.write(plot("a", [(0.0, 1.0), (1.0, 2.0)]) + plot("b", [(0.0, 3.0), (1.0, 4.0)]))
spec = importlib.util.spec_from_file_location("vacask_python_rawfile", os.path.join(src, "python", "rawfile.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rd = mod.rawread(path)
for k in range(len(rd.arrs)):
    f = rd.get(k)
    print("plot %d names %s data %s" % (k, list(f.names), f.data.tolist()))
