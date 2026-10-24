"""In-memory parameter sweep with isolated, collision-free sim directories."""
import copy
import hashlib
import math
import os
import re


def _value_tag(value):
    raw = str(value)
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip(" .")[:60] or "value"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"v_{readable}_{digest}"


def sweep(spec, part_name, param, values, rl, outroot, pwr=None, timeout=600, errors=None):
    """-> [(value, eta_pct, pwr_dBm)]; failed combinations have None metrics."""
    from . import build, sim, results, core

    values = list(values)
    if not values:
        raise ValueError("sweep values must not be empty")
    rl = float(rl)
    if not math.isfinite(rl) or rl <= 0:
        raise ValueError("load resistance rl must be finite and positive")
    rows = []

    def fail(value, message):
        rows.append((value, None, None))
        if errors is not None:
            errors.append((value, message))

    for v in values:
        s = copy.deepcopy(spec)
        hit = False
        for p in s["parts"]:
            if p["name"] == part_name:
                p.setdefault("params", {})[param] = str(v)
                hit = True
        if not hit:
            raise ValueError(f"part {part_name} not in spec")
        res = build.build(s, dry=True, verbose=False)
        if res["problems"] or not res.get("netlist"):
            fail(v, "; ".join(res["problems"]) or "build produced no netlist")
            continue
        outdir = os.path.join(outroot, f"{part_name}_{param}_{_value_tag(v)}")
        ads = core.ads_root(s)
        ok, ds, tail = sim.run_netlist(res["netlist"], outdir, ads, timeout=timeout)
        if not ok or not ds:
            fail(v, tail or "simulation produced no dataset")
            continue
        try:
            curve = results.eta_curve(ds, rl, varblock=s.get("varblock"),
                                      power_axis=s.get("power_axis", "pwr"))
        except ValueError as e:
            fail(v, str(e))
            continue
        if not curve:
            fail(v, "output probe or PCE curve missing")
            continue
        if pwr is not None:
            matches = [r for r in curve if abs(r[0] - pwr) < 1e-9]
            if not matches:
                fail(v, f"power {pwr} dBm not in sweep; available: {[r[0] for r in curve]}")
                continue
            pt = matches[0]
        else:
            pt = max(curve, key=lambda r: r[2])
        rows.append((v, pt[2], pt[0]))
    return rows
