"""Geometric audits run before a design is saved."""
from .core import TOL


def segs(poly):
    return [tuple(poly[i:i + 2]) for i in range(len(poly) - 1)]


def seg_kind(s):
    (x1, y1), (x2, y2) = s
    if abs(y1 - y2) < TOL:
        return "H", min(x1, x2), max(x1, x2), y1
    if abs(x1 - x2) < TOL:
        return "V", min(y1, y2), max(y1, y2), x1
    raise ValueError(f"non-orthogonal segment {s} (wires must be axis-aligned)")


def on_seg(pt, s, tol=TOL):
    x, y = pt
    k, a, b, c = seg_kind(s)
    if k == "H":
        return abs(y - c) < tol and a - tol <= x <= b + tol
    return abs(x - c) < tol and a - tol <= y <= b + tol


def same_pt(p, q, tol=TOL):
    return abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol


def _connectivity_problems(nets, allsegs, P, ground_refs=None):
    """Require connectivity, allowing grounded islands with their own GROUND."""
    ground_refs = ground_refs or set()
    parent = {}

    def find(key):
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, (net, _s) in enumerate(allsegs):
        find(("seg", net, i))
    for net, refs in nets.items():
        for ref in refs:
            if ref in P:
                find(("pin", net, ref))
    for i, (n1, s1) in enumerate(allsegs):
        for j in range(i + 1, len(allsegs)):
            n2, s2 = allsegs[j]
            if n1 != n2:
                continue
            if any(on_seg(pt, s2) for pt in s1) or any(on_seg(pt, s1) for pt in s2):
                union(("seg", n1, i), ("seg", n2, j))
    for net, refs in nets.items():
        for ref in refs:
            if ref not in P:
                continue
            for i, (n2, segment) in enumerate(allsegs):
                if n2 == net and on_seg(P[ref], segment):
                    union(("pin", net, ref), ("seg", net, i))

    problems = []
    for net, refs in nets.items():
        if len(refs) < 2:
            problems.append(f"net '{net}' has fewer than two pins")
        keys = [("seg", net, i) for i, (n, _s) in enumerate(allsegs) if n == net]
        keys += [("pin", net, ref) for ref in refs if ref in P]
        roots = {find(key) for key in keys}
        if len(roots) > 1:
            grounded_roots = {find(("pin", net, ref)) for ref in refs
                              if ref in P and ref in ground_refs}
            ungrounded = roots - grounded_roots
            if not grounded_roots:
                problems.append(f"net '{net}' is split into {len(roots)} disconnected groups")
            elif ungrounded:
                problems.append(f"net '{net}' has {len(ungrounded)} disconnected group(s) "
                                "without a GROUND symbol or wire connection")
    return problems


def audit(nets, wires, P, ground_refs=None):
    """Check pin coverage, connectivity, dangling ends and foreign-net contact."""
    problems, notes = [], []
    seen = set()

    def problem(message):
        if message not in seen:
            seen.add(message)
            problems.append(message)

    allsegs = []
    for net, poly in wires:
        if len(poly) < 2:
            problem(f"wire of net '{net}' needs at least two points")
            continue
        for s in segs(poly):
            (x1, y1), (x2, y2) = s
            if same_pt((x1, y1), (x2, y2)):
                problem(f"zero-length wire segment on net '{net}': {s}")
            elif abs(x1 - x2) >= TOL and abs(y1 - y2) >= TOL:
                problem(f"non-orthogonal segment on net '{net}': {s}")
            else:
                allsegs.append((net, s))
    notes.append(f"segments={len(allsegs)} nets={len(nets)}")

    listed = {}
    for net, pinlist in nets.items():
        for ref in pinlist:
            listed[ref] = listed.get(ref, 0) + 1
            if ref not in P:
                problem(f"net '{net}' references unknown pin {ref}")
                continue
            pt = P[ref]
            if not any(on_seg(pt, s) for n2, s in allsegs if n2 == net):
                problem(f"pin {ref} net '{net}' at {pt} touches none of its own wires")
            for n2, s in allsegs:
                if n2 != net and on_seg(pt, s):
                    problem(f"pin {ref} (net {net}) at {pt} SHORTS onto wire of '{n2}': {s}")
    for ref in P:
        if ref not in listed:
            problem(f"pin {ref} is missing from spec nets")
    for ref, count in listed.items():
        if count > 1:
            problem(f"pin {ref} appears {count} times in spec nets")

    for net, poly in wires:
        if len(poly) < 2:
            continue
        own = segs(poly)
        for end in (poly[0], poly[-1]):
            is_pin = any(same_pt(P[ref], end) for ref in nets.get(net, []) if ref in P)
            on_other = any(on_seg(end, s) for n2, s in allsegs if n2 == net and s not in own)
            if not (is_pin or on_other):
                problem(f"wire of net '{net}' dangling at {end}")
        for vertex in poly:
            for n2, s in allsegs:
                if n2 != net and on_seg(vertex, s):
                    problem(f"wire of net '{net}' touches foreign net '{n2}' at {vertex}")

    for i in range(len(allsegs)):
        for j in range(i + 1, len(allsegs)):
            n1, s1 = allsegs[i]
            n2, s2 = allsegs[j]
            if n1 == n2:
                continue
            k1, a1, b1, c1 = seg_kind(s1)
            k2, a2, b2, c2 = seg_kind(s2)
            if k1 != k2:
                h, v = ((a1, b1, c1), (a2, b2, c2)) if k1 == "H" else ((a2, b2, c2), (a1, b1, c1))
                hx1, hx2, hy = h
                vy1, vy2, vx = v
                if hx1 < vx < hx2 and vy1 < hy < vy2:
                    problem(f"CROSSING '{n1}' {s1} x '{n2}' {s2}")
            elif k1 == "H" and abs(c1 - c2) < TOL and not (b1 < a2 or b2 < a1):
                problem(f"COLLINEAR OVERLAP y={c1}: '{n1}' {s1} / '{n2}' {s2}")
            elif k1 == "V" and abs(c1 - c2) < TOL and not (b1 < a2 or b2 < a1):
                problem(f"COLLINEAR OVERLAP x={c1}: '{n1}' {s1} / '{n2}' {s2}")
    for message in _connectivity_problems(nets, allsegs, P, ground_refs):
        problem(message)
    return problems, notes


def _segment_crosses_box(s, box):
    (x1, y1), (x2, y2) = s
    (bx1, by1), (bx2, by2) = box
    xlo, xhi = min(bx1, bx2), max(bx1, bx2)
    ylo, yhi = min(by1, by2), max(by1, by2)
    if abs(y1 - y2) < TOL and abs(x1 - x2) >= TOL:
        return (ylo + TOL < y1 < yhi - TOL
                and max(min(x1, x2), xlo + TOL) < min(max(x1, x2), xhi - TOL))
    if abs(x1 - x2) < TOL and abs(y1 - y2) >= TOL:
        return (xlo + TOL < x1 < xhi - TOL
                and max(min(y1, y2), ylo + TOL) < min(max(y1, y2), yhi - TOL))
    return False


def wire_body_crossings(wires, boxes, P):
    """Find wires entering a symbol body, excluding wires ending on its pins."""
    part_pins = {}
    for (part, _pin), pt in P.items():
        part_pins.setdefault(part, []).append(pt)
    problems = []
    for net, poly in wires:
        for s in segs(poly):
            for name, box in boxes.items():
                if box is None or len(part_pins.get(name, [])) < 2:
                    continue
                if any(same_pt(end, pin) for end in s for pin in part_pins.get(name, [])):
                    continue
                if _segment_crosses_box(s, box):
                    problems.append(f"wire of net '{net}' crosses body of '{name}': {s}")
    return problems
