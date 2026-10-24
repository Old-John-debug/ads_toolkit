"""Generate examples/specs/rectA_spec.json + templates/spec_template.json from the
verified project data (build_v5_layout.py literals), so the example cannot drift.

Usage: python tools/make_example_spec.py <build_v5_layout.py> <toolkit_root> <workspace> [library] [cell]
"""
import ast
import json
import os
import sys

if len(sys.argv) < 4:
    raise SystemExit(__doc__)
SRC, ROOT = sys.argv[1], sys.argv[2]
WORKSPACE = os.path.abspath(sys.argv[3])
LIBRARY = sys.argv[4] if len(sys.argv) > 4 else "MyLibrary_lib"
CELL = sys.argv[5] if len(sys.argv) > 5 else "rectA"

with open(SRC, encoding="utf-8") as fh:
    tree = ast.parse(fh.read())

data = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        name = node.targets[0].id
        if name in ("LAYOUT", "NETS", "WIRES", "VALUES", "VARS", "MASTERS"):
            data[name] = ast.literal_eval(node.value)

LIBMAP = data["MASTERS"]
parts = []
for name, key, origin, angle in data["LAYOUT"]:
    lib, cell, view = LIBMAP[key]
    part = {"name": name, "lib": lib, "cell": cell,
            "origin": [float(origin[0]), float(origin[1])], "angle": int(angle)}
    params = {k: v for k, v in data["VALUES"].get(name, [])}
    if params:
        part["params"] = params
    parts.append(part)

nets = {n: [[a, b] for a, b in refs] for n, refs in data["NETS"].items()}
wires = [{"net": n, "points": [[float(x), float(y)] for x, y in pts]}
         for n, pts in data["WIRES"]]

spec = {
    "design": {"workspace": WORKSPACE, "library": LIBRARY, "cell": CELL},
    "parts": parts,
    "nets": nets,
    "wires": wires,
    "vars": {k: str(v) for k, v in data["VARS"].items()},
    "var_part": "VAR1",
    "tahb_off": True,
}
out = os.path.join(ROOT, "examples", "specs", "rectA_spec.json")
with open(out, "w", encoding="utf-8") as fh:
    json.dump(spec, fh, indent=1)
print("wrote", out, f"parts={len(parts)} nets={len(nets)} wires={len(wires)}")

template = {
    "ads": None,
    "libs": {},
    "design": {"workspace": "<abs path to *_wrk>", "library": "<lib>", "cell": "<cell>"},
    "parts": [{"name": "C1", "lib": "ads_rflib", "cell": "C",
               "origin": [0.0, 0.0], "angle": 0, "params": {"C": "100 pF"}}],
    "nets": {"n1": [["C1", "P__0"], ["C1", "P__1"]]},
    "wires": [{"net": "n1", "points": [[0.0, 0.0], [1.0, 0.0]]}],
    "vars": {},
    "var_part": None,
    "tahb_off": False,
}
tout = os.path.join(ROOT, "templates", "spec_template.json")
with open(tout, "w", encoding="utf-8") as fh:
    json.dump(template, fh, indent=1)
print("wrote", tout)
