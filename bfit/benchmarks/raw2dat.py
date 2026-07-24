#!/usr/bin/env python3
"""raw2dat.py -- dump one node of a SPICE rawfile as whitespace 'time value'
lines (the ngspice-wrdata format accuracy.py consumes). Lets the VACASK lane
reuse the exact same rl2/THD accuracy pipeline as the ngspice/Xyce lanes.

usage: raw2dat.py <file.raw> <node> <out.dat>
"""
import sys, os

sys.path.insert(0, os.environ.get("VACASK_PYTHON", "/usr/local/src/VACASK/python"))
from rawfile import rawread

raw, node, out = sys.argv[1], sys.argv[2], sys.argv[3]
plot = rawread(raw).get()
t = plot["time"]
v = plot[node]
with open(out, "w") as f:
    for tt, vv in zip(t, v):
        f.write("%.10e %.10e\n" % (tt, vv))
