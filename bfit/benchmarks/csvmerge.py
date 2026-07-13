#!/usr/bin/env python3
"""csvmerge.py -- merge one benchmark row's fields into open.csv, keyed by
model. Lets the ng/xy and vc lanes run independently (either can be skipped)
without clobbering the other lane's columns.

usage: csvmerge.py <open.csv> <model> field=value [field=value ...]
"""
import sys, os, csv

COLS = ["model", "ng_base", "xy_base",
        "ng_bal", "ng_bal_acc", "ng_fast", "ng_fast_acc",
        "xy_bal", "xy_bal_acc", "xy_fast", "xy_fast_acc",
        "mpi_best", "mpi_np",
        "vc_base", "vc_bal", "vc_bal_acc", "vc_fast", "vc_fast_acc"]

path, model = sys.argv[1], sys.argv[2]
rows, order = {}, []
if os.path.exists(path):
    for r in csv.DictReader(open(path)):
        rows[r["model"]] = r
        order.append(r["model"])
if model not in rows:
    rows[model] = {"model": model}
    order.append(model)
for kv in sys.argv[3:]:
    k, v = kv.split("=", 1)
    rows[model][k] = v
with open(path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
    w.writeheader()
    for m in order:
        w.writerow({c: rows[m].get(c, "") for c in COLS})
