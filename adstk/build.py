"""Spec -> schematic builder. Audit-first; saves only after every check passes."""
import json
import os

from . import core
from . import audit as auditmod


def _resolve_path(value, base):
    value = os.path.expandvars(os.path.expanduser(str(value)))
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(base, value))


def _read_spec_file(path):
    with open(path, encoding="utf-8") as fh:
        if path.lower().endswith(".json"):
            return json.load(fh)
        from . import textspec
        return textspec.parse(fh.read())


def _resolve_design_paths(spec, path):
    for key in ("workspace", "library", "cell"):
        if key not in spec["design"]:
            raise ValueError(f"spec.design missing required key '{key}'")
    base = os.path.dirname(path)
    spec["design"]["workspace"] = _resolve_path(spec["design"]["workspace"], base)
    if spec.get("ads"):
        spec["ads"] = _resolve_path(spec["ads"], base)
    for lib, libpath in spec.get("libs", {}).items():
        if libpath:
            spec["libs"][lib] = _resolve_path(libpath, base)
    return spec


def load_design(path):
    """Load only design paths for readback or design-to-spec extraction."""
    path = os.path.abspath(path)
    spec = _read_spec_file(path)
    if "design" not in spec:
        raise ValueError("spec missing required key 'design'")
    return _resolve_design_paths(spec, path)


def load_spec(path):
    path = os.path.abspath(path)
    spec = _read_spec_file(path)
    for key in ("design", "parts", "nets", "wires"):
        if key not in spec:
            raise ValueError(f"spec missing required key '{key}'")
    spec = _resolve_design_paths(spec, path)

    if not spec["parts"]:
        raise ValueError("spec.parts must not be empty")
    names = [p["name"] for p in spec["parts"]]
    if len(names) != len(set(names)):
        raise ValueError("duplicate part names in spec")
    known = set(names)
    for p in spec["parts"]:
        for key in ("lib", "cell", "origin"):
            if key not in p:
                raise ValueError(f"part {p['name']} missing required key '{key}'")
        if len(p["origin"]) != 2:
            raise ValueError(f"part {p['name']} origin must contain two coordinates")
        p["origin"] = [float(v) for v in p["origin"]]
        angle = int(p.get("angle", 0))
        if angle not in (0, 90, 180, 270):
            raise ValueError(f"part {p['name']} has unsupported angle {angle}; use 0/90/180/270")
        p["angle"] = angle
        p.setdefault("params", {})
        p["master"] = (p["lib"], p["cell"], p.get("view", "symbol"))

    for net, refs in spec["nets"].items():
        for ref in refs:
            if len(ref) != 2:
                raise ValueError(f"net '{net}' has invalid pin reference {ref}")
            if ref[0] not in known:
                raise ValueError(f"net '{net}' references unknown part '{ref[0]}'")
    spec["nets"] = {n: [tuple(r) for r in refs] for n, refs in spec["nets"].items()}
    wnets = {w["net"] for w in spec["wires"]}
    if wnets - set(spec["nets"]):
        raise ValueError(f"wires reference undefined nets: {sorted(wnets - set(spec['nets']))}")
    for w in spec["wires"]:
        for pt in w["points"]:
            if len(pt) != 2:
                raise ValueError(f"wire of net '{w['net']}' has invalid point {pt}")
        w["points"] = [[float(v) for v in pt] for pt in w["points"]]
    return spec


def masters_of(spec):
    seen = []
    for p in spec["parts"]:
        if p["master"] not in seen:
            seen.append(p["master"])
    return seen


def _place(d, spec, P, offsets):
    """clear + place + verify pins; returns problem list (empty = ok)."""
    d.clear_design()
    for p in spec["parts"]:
        d.add_instance(p["master"], tuple(p["origin"]), name=p["name"],
                       angle=int(p.get("angle", 0)))
    for p in spec["parts"]:
        got = {q.master_pin.name: core.pt_of(q.snap_point)
               for q in d.get_instance(p["name"]).get_inst_pin_iter()}
        want = {pin: (round(P[(p["name"], pin)][0], 4), round(P[(p["name"], pin)][1], 4))
                for pin in offsets[p["master"]]}
        if got != want:
            return [f"pin mismatch {p['name']}: {got} vs {want}"]
    return []


def _params(d, spec):
    """Write string params, VAR values and TAHB off; returns problem list."""
    problems = []
    for p in spec["parts"]:
        if not p["params"]:
            continue
        m = core.smap(d.get_instance(p["name"]))
        for pname, pval in p["params"].items():
            if not pname or not str(pval).strip():
                continue
            pval = str(pval)
            if pname in core.QUOTE_PARAMS and not pval.startswith('"'):
                pval = f'"{pval}"'
            if pname in m:
                m[pname].string_value = pval
            else:
                problems.append(f"param {p['name']}.{pname} not found")
    var_candidates = [p["name"] for p in spec["parts"] if p["cell"] == "VAR"]
    var_name = None
    explicit_var = spec.get("var_part")
    if explicit_var:
        if explicit_var not in var_candidates:
            problems.append(f"var_part {explicit_var} is not a VAR instance")
        else:
            var_name = explicit_var
    elif len(var_candidates) == 1:
        var_name = var_candidates[0]
    elif len(var_candidates) > 1 and spec.get("vars"):
        problems.append("multiple VAR instances; specify var_part")
    if spec.get("vars") and not var_name and not explicit_var and len(var_candidates) <= 1:
        problems.append("vars specified but no VAR instance is available")
    if var_name and spec.get("vars"):
        v = d.get_instance(var_name).vars
        for k, val in spec.get("vars", {}).items():
            v[k] = str(val)
        for k in list(v):
            if k not in spec.get("vars", {}):
                del v[k]
    if spec.get("tahb_off"):
        hb_parts = [p for p in spec["parts"] if p["cell"] == "HarmonicBalance"]
        if not hb_parts:
            problems.append("tahb_off requested but no HarmonicBalance instance is available")
        for p in hb_parts:
            if not core.set_tahb_off(d.get_instance(p["name"])):
                problems.append(f"cannot disable TAHB on {p['name']}")
    return problems


def _overlap(d, spec, wires, P):
    """Body overlap and wire-through-body problems among placed parts."""
    boxes = {}
    problems = []
    for p in spec["parts"]:
        try:
            boxes[p["name"]] = core.box_of(d.get_instance(p["name"]), "bbox")
        except Exception:  # noqa: BLE001
            boxes[p["name"]] = None
        if boxes[p["name"]] is None:
            problems.append(f"bbox unavailable for {p['name']}")
    names = [n for n in boxes if boxes[n]]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if core.boxes_overlap(boxes[a], boxes[b]):
                problems.append(f"body overlap: {a} / {b}")
    problems.extend(auditmod.wire_body_crossings(wires, boxes, P))
    return problems


def build(spec, dry=True, reference=None, verbose=True):
    """Place + wire + parameterise in memory. dry=True never calls save_design.

    Returns dict(ok, saved, netlist, problems, notes).
    Opening a workspace can still update ADS workspace metadata files.
    """
    core.setup_env(spec.get("ads"))
    import keysight.ads.de as de
    from keysight.ads.de.db import DesignMode, Transaction

    design = spec["design"]
    core.assert_no_lock(design["workspace"], design["library"], design["cell"])
    for lib, path in core.lib_paths(spec).items():
        try:
            de.Library.open(lib, path, de.LibraryMode.READ_ONLY)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print("  lib open note:", lib, repr(e))

    ws = de.Workspace(design["workspace"])
    ws.open()
    result = {"ok": False, "saved": False, "netlist": None, "problems": [], "notes": []}
    try:
        d = ws.design((design["library"], design["cell"], "schematic"), DesignMode.APPEND)
        pin_indices = {}
        with Transaction(d, "adstk pin discovery") as tx:
            offsets = core.discover_offsets(d, masters_of(spec), pin_indices)
            tx.rollback()
        P = core.predict_pins(spec["parts"], offsets)
        wires = [(w["net"], [tuple(pt) for pt in w["points"]]) for w in spec["wires"]]
        ground_refs = {(p["name"], pin) for p in spec["parts"] if p["cell"] == "GROUND"
                       for pin in offsets[p["master"]]}
        problems, notes = auditmod.audit(spec["nets"], wires, P, ground_refs=ground_refs)
        result["problems"], result["notes"] = problems, notes
        if verbose:
            for n in notes:
                print("  audit:", n)
            for p in problems:
                print("   !", p)
        if problems:
            return result

        if not dry:
            sch = core.sch_path(design["workspace"], design["library"], design["cell"])
            if os.path.isfile(sch):
                saved = core.backup(sch)
                result["notes"].append(f"backup: {saved}")
                if verbose:
                    print("  backup ->", saved)

        with Transaction(d, "adstk build") as tx:
            result["problems"] += _place(d, spec, P, offsets)
            if not result["problems"]:
                result["problems"] += _params(d, spec)
            if not result["problems"]:
                result["problems"] += _overlap(d, spec, wires, P)
            if result["problems"]:
                tx.rollback()
            else:
                for net, poly in wires:
                    d.add_wire(poly)
                tx.commit()
        if result["problems"]:
            return result

        result["netlist"] = d.generate_netlist()
        from . import netlistx
        if not netlistx.compare_spec_connectivity(
                spec, pin_indices, result["netlist"], device_types=spec.get("device_types")):
            result["problems"].append("generated netlist connectivity differs from spec")
            return result
        if reference is not None:
            with open(reference, encoding="utf-8") as fh:
                old = fh.read()
            eq_dev = netlistx.compare_netlists(
                old, result["netlist"], device_types=spec.get("device_types"))
            eq_ctl = netlistx.compare_controllers(old, result["netlist"])
            result["ok"] = bool(eq_dev and eq_ctl)
        else:
            result["ok"] = True
        if not dry and result["ok"] and not result["problems"]:
            d.save_design()
            result["saved"] = True
        return result
    finally:
        ws.close()
