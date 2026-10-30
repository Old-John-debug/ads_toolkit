"""Rule-based layout for common rectifier chains, shunts, probes and controllers."""
import json
import os
from collections import defaultdict

from . import audit, core

ANGLES = (0, 90, 180, 270)
GRID = 0.25
# Global spacing scale. 1.5 gives the layout ~1.5x the original clearance
# (branches 2.25 apart, first branch 1.25 from the node, probes/ground bus
# 1.25 away, controller row 3.0 below the bus, controller columns 7.5 apart).
SPREAD = 1.5


def _q(value):
    return round(round(float(value) / GRID) * GRID, 6)


BRANCH_OFFSET = _q(0.75 * SPREAD)
BRANCH_PITCH = _q(1.5 * SPREAD)
PROBE_DROP = _q(0.75 * SPREAD)
BUS_DROP = _q(0.75 * SPREAD)
CTRL_DY = _q(2.0 * SPREAD)
CTRL_DX = _q(5.0 * SPREAD)
SIDE_MARGIN = _q(0.5 * SPREAD)


def default_pin_table():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "device_pin_table.json")


def load_pin_table(path=None):
    with open(path or default_pin_table(), encoding="utf-8") as fh:
        data = json.load(fh)
    table = {}
    for item in data["devices"]:
        table[(item["library"], item["cell"])] = {
            pin: (float(xy[0]), float(xy[1])) for pin, xy in item["r0_offsets"].items()
        }
    return table


def _rotated(pins, angle):
    return {pin: core.rot(offset, angle) for pin, offset in pins.items()}


def _unique_transition(nets, previous):
    remaining = [net for net in nets if net != previous]
    if len(remaining) != 1:
        raise ValueError(f"device transition {sorted(nets)} from {previous} is ambiguous")
    return remaining[0]


def generate(spec, layout, pin_table):
    """Return (spec with geometry, problems, notes)."""
    parts = {part["name"]: part for part in spec["parts"]}
    names = list(parts)
    if len(names) != len(set(names)):
        raise ValueError("duplicate part names")

    def pins_of(name):
        part = parts[name]
        master = (part["lib"], part["cell"])
        if master not in pin_table:
            raise ValueError(f"pin table has no entry for {master[0]}:{master[1]}")
        return pin_table[master]

    pin_nets = defaultdict(dict)
    for net, refs in spec["nets"].items():
        for name, pin in refs:
            if name not in parts:
                raise ValueError(f"net {net} references unknown part {name}")
            if pin in pin_nets[name]:
                raise ValueError(f"pin {name}.{pin} appears more than once")
            pin_nets[name][pin] = net
    for name in names:
        unknown = set(pin_nets[name]) - set(pins_of(name))
        if unknown:
            raise ValueError(f"pin table for {name} lacks {sorted(unknown)}")

    ground_parts = [part for part in spec["parts"] if part["cell"] == "GROUND"]
    if not ground_parts:
        raise ValueError("layout needs at least one GROUND part")
    ground_candidates = set()
    for part in ground_parts:
        pins = pins_of(part["name"])
        if not pins:
            raise ValueError(f"GROUND part {part['name']} has no pin")
        for pin in pins:
            if pin not in pin_nets[part["name"]]:
                raise ValueError(f"GROUND pin {part['name']}.{pin} is missing from nets")
            ground_candidates.add(pin_nets[part["name"]][pin])
    if layout.get("地网络"):
        ground_net = layout["地网络"]
        if any(pin_nets[p["name"]][pin] != ground_net for p in ground_parts for pin in pins_of(p["name"])):
            raise ValueError("地网络 does not match all GROUND pins")
    elif len(ground_candidates) == 1:
        ground_net = next(iter(ground_candidates))
    else:
        raise ValueError(f"GROUND pins touch multiple nets: {sorted(ground_candidates)}")
    if ground_net not in spec["nets"]:
        raise ValueError(f"ground net {ground_net} is not declared")

    categories = ("主链", "对地", "探针", "控制器")
    assigned = []
    for category in categories:
        assigned.extend(layout[category])
    if len(assigned) != len(set(assigned)):
        raise ValueError("part appears in more than one layout category")
    ground_names = {p["name"] for p in ground_parts}
    for name in assigned:
        if name not in parts:
            raise ValueError(f"layout references unknown part {name}")
        if name in ground_names:
            raise ValueError(f"GROUND part {name} must not be listed in layout categories")
    for part in spec["parts"]:
        if part["name"] not in ground_names and part["name"] not in assigned:
            raise ValueError(f"part {part['name']} is not assigned to a layout category")

    def non_ground_nets(name):
        return sorted({net for net in pin_nets[name].values() if net != ground_net})

    series = layout["主链"]
    if not series:
        raise ValueError("主链 must not be empty")
    if len(non_ground_nets(series[0])) != 1:
        raise ValueError(f"first main-chain part {series[0]} must touch exactly one signal net")
    nodes = [non_ground_nets(series[0])[0]]
    for name in series[1:]:
        nets = non_ground_nets(name)
        if len(nets) != 2:
            raise ValueError(f"main-chain part {name} must touch exactly two signal nets")
        if nodes[-1] not in nets:
            raise ValueError(f"main-chain part {name} does not connect to net {nodes[-1]}")
        nodes.append(_unique_transition(nets, nodes[-1]))
    if len(nodes) != len(set(nodes)):
        raise ValueError("main-chain nets form a loop or repeat")

    branches = defaultdict(list)
    for name in layout["对地"]:
        nets = non_ground_nets(name)
        if len(nets) != 1 or nets[0] not in nodes:
            raise ValueError(f"shunt part {name} must touch exactly one main-chain signal net")
        branches[nets[0]].append(("shunt", name))
    for name in layout["探针"]:
        nets = non_ground_nets(name)
        if len(nets) != 1 or nets[0] not in nodes:
            raise ValueError(f"probe {name} must touch exactly one main-chain signal net")
        if len(pins_of(name)) != 1:
            raise ValueError(f"probe {name} must have exactly one pin in the pin table")
        branches[nets[0]].append(("probe", name))
    for name in layout["控制器"]:
        if pins_of(name):
            raise ValueError(f"controller {name} unexpectedly has pins")

    extra_signal_nets = {net for net in spec["nets"] if net != ground_net} - set(nodes)
    if extra_signal_nets:
        raise ValueError(f"signal nets are not on the main chain: {sorted(extra_signal_nets)}")

    P = {}
    wires = defaultdict(list)

    def add_wire(net, points):
        clean = []
        for x, y in points:
            point = (round(float(x), 6), round(float(y), 6))
            if not clean or clean[-1] != point:
                clean.append(point)
        if len(clean) >= 2:
            wires[net].append(clean)

    def route(net, start, end):
        if start == end:
            return
        if abs(start[0] - end[0]) < 1e-9 or abs(start[1] - end[1]) < 1e-9:
            add_wire(net, [start, end])
        else:
            add_wire(net, [start, (end[0], start[1]), end])

    def put(name, angle, origin):
        part = parts[name]
        part["origin"] = [round(float(origin[0]), 6), round(float(origin[1]), 6)]
        part["angle"] = int(angle)
        for pin, offset in _rotated(pins_of(name), angle).items():
            P[(name, pin)] = (round(float(origin[0]) + offset[0], 6),
                              round(float(origin[1]) + offset[1], 6))

    def connect_extra(name, net, target, skip=()):
        for pin in pin_nets[name]:
            if pin in skip or pin_nets[name][pin] != net:
                continue
            route(net, P[(name, pin)], target)

    def connect_extra_side(name, net, target, skip=(), side="left", margin=SIDE_MARGIN):
        """Tie extra pins outside the symbol so the parallel connection is visible."""
        outside_x = target[0] - margin if side == "left" else target[0] + margin
        for pin in pin_nets[name]:
            if pin in skip or pin_nets[name][pin] != net:
                continue
            point = P[(name, pin)]
            if abs(point[1] - target[1]) < 1e-9 and abs(point[0] - target[0]) < 1e-9:
                continue
            if abs(point[1] - target[1]) < 1e-9:
                add_wire(net, [point, target])
            else:
                add_wire(net, [point, (outside_x, point[1]),
                               (outside_x, target[1]), target])

    source = series[0]
    source_nets = pin_nets[source]
    signal_pins = [pin for pin, net in source_nets.items() if net == nodes[0]]
    if len(signal_pins) != 1:
        raise ValueError(f"source {source} must have exactly one pin on {nodes[0]}")
    ground_pins = [pin for pin, net in source_nets.items() if net == ground_net]
    if not ground_pins:
        raise ValueError(f"source {source} needs a GROUND pin")
    best = None
    for angle in ANGLES:
        offsets = _rotated(pins_of(source), angle)
        origin = (-offsets[signal_pins[0]][0], -offsets[signal_pins[0]][1])
        ground = (origin[0] + offsets[ground_pins[0]][0],
                  origin[1] + offsets[ground_pins[0]][1])
        if abs(ground[0]) < 0.05 and abs(ground[1]) < 0.05:
            continue
        score = (0 if (ground[0] < -0.05 or ground[1] < -0.05) else 10)
        score += abs(ground[0]) * 0.5 + abs(ground[1]) * 0.5
        if best is None or score < best[0]:
            best = (score, angle, origin)
    if best is None:
        raise ValueError(f"cannot orient source {source}; use the v1 layout")
    put(source, best[1], best[2])
    connect_extra(source, nodes[0], (0.0, 0.0), skip={signal_pins[0]})
    ground_points = [P[(source, pin)] for pin in ground_pins]

    def place_series(name, previous_net, next_net, x):
        candidates = []
        offsets_by_angle = {angle: _rotated(pins_of(name), angle) for angle in ANGLES}
        for angle, offsets in offsets_by_angle.items():
            ins = [pin for pin, net in pin_nets[name].items() if net == previous_net]
            outs = [pin for pin, net in pin_nets[name].items() if net == next_net]
            for pin_in in ins:
                for pin_out in outs:
                    if pin_in == pin_out:
                        continue
                    dx = offsets[pin_out][0] - offsets[pin_in][0]
                    dy = offsets[pin_out][1] - offsets[pin_in][1]
                    if dx < 0.2:
                        continue
                    score = abs(dy) * 4 - dx * 0.1
                    origin = (x - offsets[pin_in][0], -offsets[pin_in][1])
                    candidates.append((score, angle, origin, pin_in, pin_out))
        if not candidates:
            raise ValueError(f"no usable orientation for series part {name}")
        _, angle, origin, pin_in, pin_out = min(candidates, key=lambda item: item[0])
        put(name, angle, origin)
        output = P[(name, pin_out)]
        if output[0] <= x + 0.2:
            raise ValueError(f"series part {name} has no positive horizontal span")
        connect_extra_side(name, previous_net, (x, 0.0), skip={pin_in}, side="left")
        connect_extra_side(name, next_net, (output[0], 0.0), skip={pin_out}, side="right")
        if abs(output[1]) > 1e-9:
            route(next_net, output, (output[0], 0.0))
        return output[0]

    def place_shunt(name, net, x):
        candidates = []
        offsets_by_angle = {angle: _rotated(pins_of(name), angle) for angle in ANGLES}
        signal_pins = [pin for pin, n in pin_nets[name].items() if n == net]
        ground_pins = [pin for pin, n in pin_nets[name].items() if n == ground_net]
        if len(signal_pins) != 1 or not ground_pins:
            raise ValueError(f"shunt part {name} needs one signal pin and at least one GROUND pin")
        for angle, offsets in offsets_by_angle.items():
            origin = (x - offsets[signal_pins[0]][0], -offsets[signal_pins[0]][1])
            ground = (origin[0] + offsets[ground_pins[0]][0],
                      origin[1] + offsets[ground_pins[0]][1])
            if ground[1] > -0.2:
                continue
            score = abs(ground[0] - x) * 4 + abs(ground[1]) * 0.1
            candidates.append((score, angle, origin))
        if not candidates:
            raise ValueError(f"no downward orientation for shunt part {name}")
        _, angle, origin = min(candidates, key=lambda item: item[0])
        put(name, angle, origin)
        connect_extra(name, net, (x, 0.0), skip={signal_pins[0]})
        return [P[(name, pin)] for pin in ground_pins]

    def place_probe(name, net, x):
        pin = next(iter(pins_of(name)))
        offset = pins_of(name)[pin]
        target = (x, -PROBE_DROP)
        put(name, 0, (target[0] - offset[0], target[1] - offset[1]))
        route(net, (x, 0.0), target)

    cursor = 0.0
    for index in range(len(series) - 1):
        node = nodes[index]
        node_branches = branches.get(node, [])
        if node_branches:
            xs = [_q(cursor + BRANCH_OFFSET + i * BRANCH_PITCH) for i in range(len(node_branches))]
            node_end = _q(xs[-1] + BRANCH_OFFSET)
        else:
            xs = []
            node_end = _q(cursor + 0.75)
        add_wire(node, [(cursor, 0.0), (node_end, 0.0)])
        for (kind, name), x in zip(node_branches, xs):
            if kind == "shunt":
                ground_points.extend(place_shunt(name, node, x))
            else:
                place_probe(name, node, x)
        cursor = place_series(series[index + 1], node, nodes[index + 1], node_end)

    last_node = nodes[-1]
    last_branches = branches.get(last_node, [])
    if last_branches:
        xs = [_q(cursor + BRANCH_OFFSET + i * BRANCH_PITCH) for i in range(len(last_branches))]
        node_end = xs[-1]
        add_wire(last_node, [(cursor, 0.0), (node_end, 0.0)])
        for (kind, name), x in zip(last_branches, xs):
            if kind == "shunt":
                ground_points.extend(place_shunt(name, last_node, x))
            else:
                place_probe(name, last_node, x)

    if not ground_points:
        raise ValueError("no ground pin coordinates were generated")
    bus_y = _q(min(point[1] for point in ground_points) - BUS_DROP)
    device_left = min(point[0] for point in ground_points)
    device_right = max(point[0] for point in ground_points)
    count = len(ground_parts)
    if count == 1:
        ground_xs = [_q(device_left)]
    else:
        span = max(device_right - device_left, 1.0 * (count - 1))
        ground_xs = [_q(device_left + i * span / (count - 1)) for i in range(count)]
    bus_left = min([device_left] + ground_xs)
    bus_right = max([device_right] + ground_xs)
    for part, x in zip(ground_parts, ground_xs):
        pin = next(iter(pins_of(part["name"])))
        offset = pins_of(part["name"])[pin]
        put(part["name"], 0, (x - offset[0], bus_y - offset[1]))
    for point in ground_points:
        route(ground_net, point, (point[0], bus_y))
    add_wire(ground_net, [(bus_left, bus_y), (bus_right, bus_y)])

    controller_y = _q(bus_y - CTRL_DY)
    for index, name in enumerate(layout["控制器"]):
        put(name, 0, (index * CTRL_DX, controller_y))

    for part in spec["parts"]:
        for pin in pins_of(part["name"]):
            if (part["name"], pin) not in P:
                raise ValueError(f"layout did not place pin {part['name']}.{pin}")

    wire_list = []
    for net in spec["nets"]:
        for points in wires.get(net, []):
            wire_list.append({"net": net, "points": [list(point) for point in points]})
    for part in spec["parts"]:
        if "origin" not in part:
            raise ValueError(f"layout did not place part {part['name']}")
    spec["wires"] = wire_list

    audit_nets = {net: [tuple(ref) for ref in refs] for net, refs in spec["nets"].items()}
    problem_list, notes = audit.audit(
        audit_nets, [(w["net"], [tuple(p) for p in w["points"]]) for w in wire_list],
        P, ground_refs={(p["name"], pin) for p in ground_parts for pin in pins_of(p["name"])})
    return spec, problem_list, notes
