#!/usr/bin/env python3
"""Refresh the inline FRED BAA monthly series in index.html. Prints CHANGED or UNCHANGED."""
import csv, io, json, re, subprocess, sys
from pathlib import Path
p = Path(__file__).with_name("index.html")
url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAA"
raw = subprocess.run(["curl", "-sf", "--retry", "3", "--max-time", "60", url], capture_output=True, text=True, check=True).stdout
rows = [r for r in csv.reader(io.StringIO(raw))][1:]
data = [[d[:7], round(float(v), 2)] for d, v in rows if d >= "1996-01" and v not in (".", "")]
if len(data) < 300: sys.exit(f"ERROR: only {len(data)} rows from FRED")
html = p.read_text()
new = "BAA_MONTHLY = " + json.dumps(data, separators=(",", ":")) + ";"
out, n = re.subn(r"BAA_MONTHLY = \[\[.*?\]\];", lambda m: new, html, count=1, flags=re.S)
if n != 1: sys.exit("ERROR: BAA_MONTHLY block not found")
if out == html: print("UNCHANGED latest", data[-1]); sys.exit(0)
p.write_text(out); print("CHANGED latest", data[-1])
