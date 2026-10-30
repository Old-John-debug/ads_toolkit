"""Generate a spec JSON from a design as saved on disk (read-only, never saves).

Nets are derived geometrically: a device pin belongs to the net of any wire
segment it lies on (wire segments carry their net name in the database).
Wire polylines are merged from the straight segments per net.

Usage: python tools/make_spec_from_design.py <spec_with_design_paths> <out.json>
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

TOL = 1e-4
ORIENT_ANGLE = {"R0": 0, "R90": 90, "R180": 180, "R270": 270}


def same(a, b):
    return abs(a[0] - b[0]) < TOL and abs(a[1] - b[1]) < TOL


def on_seg(pt, s):
    (x1, y1), (x2, y2) = s
    x, y = pt
    if abs(y1 - y2) < TOL:
        return abs(y - y1) < TOL and min(x1, x2) - TOL <= x <= max(x1, x2) + TOL
    if abs(x1 - x2) < TOL:
        return abs(x - x1) < TOL and min(y1, y2) - TOL <= y <= max(y1, y2) + TOL
    return False


def merge(segs):
    segs = [list(s) for s in segs]
    polys = []
    while segs:
        poly = segs.pop(0)
        grew = True
        while grew:
            grew = False
            for s in list(segs):
                if same(s[0], poly[-1]):
                    poly.append(s[1]); segs.remove(s); grew = True
                elif same(s[1], poly[-1]):
                    poly.append(s[0]); segs.remove(s); grew = True
                elif same(s[0], poly[0]):
                    poly.insert(0, s[1]); segs.remove(s); grew = True
                elif same(s[1], poly[0]):
                    poly.insert(0, s[0]); segs.remove(s); grew = True
        polys.append(poly)
    return polys


def main():
    src_spec, out = sys.argv[1], sys.argv[2]
    from adstk import build as B, core, readback

    spec = B.load_design(src_spec)
    core.setup_env(spec.get("ads"))
    import keysight.ads.de as de
    from keysight.ads.de.db import DesignMode
    core.open_libraries(de, spec)
    ws = de.Workspace(spec["design"]["workspace"])
    ws.open()
    try:
        d = ws.design((spec["design"]["library"], spec["design"]["cell"], "schematic"),
                      DesignMode.READ_ONLY)
        insts = readback.read_instances(d)
        wires = readback.read_wires(d)
        segs = readback.segments_of(wires)

        bynet = {}
        for net, s in segs:
            if net is None:
                raise ValueError(f"wire has no net name: {s}")
            (x1, y1), (x2, y2) = s
            if abs(x1 - x2) >= TOL and abs(y1 - y2) >= TOL:
                raise ValueError(f"non-orthogonal wire is not supported: {s}")
            bynet.setdefault(net, []).append(s)

        nets = {}
        for nm, info in insts.items():
            for pin, pt in info["pins"].items():
                hits = [n for n, slist in bynet.items()
                        if any(on_seg(pt, s) for s in slist)]
                if len(hits) != 1:
                    raise ValueError(f"pin {nm}.{pin} at {pt} touches nets {hits}; expected exactly one")
                n = hits[0]
                nets.setdefault(n, [])
                if [nm, pin] not in nets[n]:
                    nets[n].append([nm, pin])

        wire_list = []
        for n in sorted(bynet, key=lambda x: (x is None, str(x))):
            for poly in merge(bynet[n]):
                wire_list.append({"net": n, "points": [list(p) for p in poly]})

        parts = []
        for nm in sorted(insts):
            info = insts[nm]
            tag = info["orient"].split(".")[-1]
            if tag not in ORIENT_ANGLE:
                raise ValueError(f"instance {nm} uses unsupported orientation {tag}; mirrored symbols are not supported")
            part = {"name": nm, "lib": info["library"], "cell": info["cell"],
                    "origin": list(info["origin"]),
                    "angle": ORIENT_ANGLE[tag]}
            params = {k: p.string_value for k, p in core.smap(d.get_instance(nm)).items()
                      if k and str(p.string_value).strip()}
            if params:
                part["params"] = params
            parts.append(part)

        out_spec = {"design": spec["design"], "parts": parts,
                    "nets": nets, "wires": wire_list}
        var_name = next((p["name"] for p in parts if p["cell"] == "VAR"), None)
        if var_name:
            out_spec["var_part"] = var_name
            out_spec["vars"] = {k: str(v) for k, v in d.get_instance(var_name).vars.items()}
        out_spec["tahb_off"] = True
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(out_spec, fh, indent=1)
        print(f"spec -> {out}  parts={len(parts)} nets={len(nets)} wires={len(wire_list)}")
    finally:
        ws.close()


if __name__ == "__main__":
    main()
