"""Read back what is stored in a schematic design (read-only, never saves).

Used for the "user drew it in the ADS GUI" input path: instances, pins, wire
segments with their nets, plus an electrical-completeness report derived from
the generated netlist (floating pins, single-pin nets).
"""
from .core import box_of, pt_of


def read_instances(d):
    """-> {name: {cell, origin, orient, box, anno, pins:{pin:(x,y)}}}"""
    out = {}
    for it in d.instances:
        nm = it.inst_name
        pins = {p.master_pin.name: pt_of(p.snap_point) for p in it.get_inst_pin_iter()}
        try:
            box = box_of(it, "bbox")
        except Exception:  # noqa: BLE001
            box = None
        try:
            anno = box_of(it, "bbox_with_annotation")
        except Exception:  # noqa: BLE001
            anno = None
        out[nm] = {
            "cell": it.cell_name,
            "library": it.library_name,
            "origin": pt_of(it.origin),
            "orient": str(it.orient),
            "box": box,
            "anno": anno,
            "pins": pins,
        }
    return out


def read_wires(d):
    """-> [{"net", "points": [(x, y), ...]}] one entry per Line (polyline) in the DB.

    Uses get_outline().points so L-shaped wires keep their vertices (a bbox
    alone would turn an L into a bogus diagonal).
    """
    out = []
    for sh in d.shapes:
        if type(sh).__name__ != "Line":
            continue
        net = getattr(sh, "net", None)
        try:
            pts = [(round(float(p.x), 4), round(float(p.y), 4))
                   for p in sh.get_outline().points]
        except Exception:  # noqa: BLE001
            b = sh.bbox
            pts = [pt_of(b.lower_left), pt_of(b.upper_right)]
        out.append({"net": getattr(net, "name", str(net)) if net is not None else None,
                    "points": pts})
    return out


def segments_of(wires):
    """-> [(net, (p1, p2))] straight segments expanded from polylines."""
    out = []
    for w in wires:
        pts = w["points"]
        for i in range(len(pts) - 1):
            out.append((w["net"], (pts[i], pts[i + 1])))
    return out


def completeness(d, netlist_text, device_types=None):
    """Floating-pin report from the netlist: nets touched by a single device pin."""
    from .netlistx import parse_netlist
    _, inc, _ = parse_netlist(netlist_text, device_types)
    floating = {n: sorted(v) for n, v in inc.items() if len(v) < 2}
    return floating


def report(d, netlist_text=None, device_types=None):
    """Human-readable read-back report (printed, returned as text)."""
    lines = []
    insts = read_instances(d)
    wires = read_wires(d)
    segs = segments_of(wires)
    lines.append(f"instances: {len(insts)}   wire polylines: {len(wires)}   segments: {len(segs)}")
    for nm in sorted(insts):
        info = insts[nm]
        lines.append(f"  {nm:10s} {info['library']}:{info['cell']:28s} "
                     f"origin={info['origin']} {info['orient']:16s} pins={info['pins']}")
    bynet = {}
    for net, s in segs:
        bynet.setdefault(net, []).append(s)
    lines.append("wire segments per net:")
    for n in sorted(bynet, key=lambda x: (x is None, x)):
        lines.append(f"  {n}: {len(bynet[n])} seg(s)")
    if netlist_text is not None:
        from .netlistx import unknown_device_lines
        unknown = unknown_device_lines(netlist_text, device_types)
        fl = completeness(d, netlist_text, device_types)
        if fl:
            lines.append("FLOATING (single-pin) nets:")
            for n, v in sorted(fl.items()):
                lines.append(f"  {n}: {v}")
        elif unknown:
            lines.append("recognized nets have >=2 pins; unknown devices are NOT checked")
        else:
            lines.append("no floating pins: every net has >=2 device pins")
        if unknown:
            lines.append("WARNING: unrecognized device lines (not included in completeness):")
            for line in unknown:
                lines.append(f"  {line}")
    text = "\n".join(lines)
    print(text)
    return text
