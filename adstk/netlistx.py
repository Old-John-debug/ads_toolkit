"""Netlist parsing and conservative equivalence checks."""
import re
from collections import Counter

DEV_RE = re.compile(r"^([A-Za-z0-9_]+):([A-Za-z0-9_]+)\s*(.*)$")
DEVTYPES = (
    "V_Probe", "Short", "Port", "TLIN", "R", "C", "L",
    "di_hp_HSMS282B_20000301", "di_hp_HSMS2850_20000301",
)
CTRL_PREFIX = ("HB:", "ParamSweep:", "SweepPlan:")
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S+$")


def _split_tokens(text):
    """Split on unquoted whitespace while preserving quoted content exactly."""
    tokens, buf = [], []
    in_quote = False
    escaped = False
    for ch in text.strip():
        if ch.isspace() and not in_quote:
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
        if ch == '"' and not escaped:
            in_quote = not in_quote
        if ch == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    if buf:
        tokens.append("".join(buf))
    return tokens


def _logical_lines(text):
    out = []
    pending = ""
    for raw in text.splitlines():
        line = " ".join(_split_tokens(raw))
        if not line or line.startswith(";"):
            continue
        continuation = line.endswith("\\")
        if continuation:
            line = line[:-1].rstrip()
        pending = f"{pending} {line}".strip() if pending else line
        if not continuation:
            out.append(pending)
            pending = ""
    if pending:
        out.append(pending)
    return out


def parse_netlist(text, device_types=None):
    """-> (devices, incidence, opaque_lines)

    Known device types are parsed into nodes. Everything else is retained for
    exact comparison instead of being silently discarded.
    """
    types = set(DEVTYPES)
    if device_types:
        types.update(device_types)
    devs, inc, opaque = {}, {}, []
    in_def = False
    def_lines = []
    for line in _logical_lines(text):
        if in_def:
            def_lines.append(line)
            if line.startswith("end "):
                opaque.append("\n".join(def_lines))
                in_def = False
            continue
        if line.startswith("define "):
            in_def = True
            def_lines = [line]
            continue
        if line.startswith(CTRL_PREFIX) or VAR_RE.match(line):
            continue
        m = DEV_RE.match(line)
        if not m or m.group(1) not in types:
            opaque.append(line)
            continue
        dtype, dname, rest = m.groups()
        if dname in devs:
            raise ValueError(f"duplicate device name in netlist: {dname}")
        toks = _split_tokens(rest)
        nodes = []
        for t in toks:
            if "=" in t:
                break
            nodes.append(t)
        devs[dname] = (dtype, nodes, " ".join(toks[len(nodes):]))
        for i, n in enumerate(nodes):
            inc.setdefault(n, set()).add((dname, i))
    if in_def:
        opaque.append("\n".join(def_lines))
    return devs, {n: frozenset(v) for n, v in inc.items()}, opaque


# A new HB instance can carry a transient internal sweep plan which disappears
# after save/reload. Only this exact automatically named plan is normalised.
AUTO_PLAN_RE = re.compile(r"^SweepPlan: [A-Za-z0-9_]+_stim Start=1 Stop=10 Step=1$")
AUTO_PLAN_HB_RE = re.compile(r'\s*SweepVar=""\s+SweepPlan="[A-Za-z0-9_]+_stim"')


def controller_lines(text):
    out = Counter()
    for line in _logical_lines(text):
        if AUTO_PLAN_RE.match(line):
            continue
        line = AUTO_PLAN_HB_RE.sub("", line)
        if line.startswith(CTRL_PREFIX) or VAR_RE.match(line):
            out[line] += 1
    return out


def compare_netlists(old_text, new_text, log=print, device_types=None):
    """True only when known topology and all remaining netlist content match."""
    od, oi, oo = parse_netlist(old_text, device_types)
    nd, ni, no = parse_netlist(new_text, device_types)
    log(f"old: {len(od)} devices / {len(oi)} nets     new: {len(nd)} devices / {len(ni)} nets")
    old_opaque, new_opaque = Counter(oo), Counter(no)
    missing = sorted((old_opaque - new_opaque).elements())
    added = sorted((new_opaque - old_opaque).elements())
    if missing or added:
        log("NON-DEVICE NETLIST DIFFERENCES (Options/OutputPlan/model/unknown types):")
        for line in missing:
            log("   MISSING:", line)
        for line in added:
            log("   ADDED  :", line)
        return False
    if set(od) != set(nd):
        log("DEVICE NAME MISMATCH:", sorted(set(od) ^ set(nd)))
        return False
    mapping, bad = {}, []
    for onet, oinc in sorted(oi.items()):
        cands = [n for n, ninc in ni.items() if ninc == oinc]
        if len(cands) == 1:
            mapping[onet] = cands[0]
        else:
            bad.append((onet, sorted(oinc), cands))
    if bad:
        log("NET MAPPING NOT UNIQUE/FOUND:")
        for b in bad:
            log("   ", b)
        return False
    log("net bijection:", {k: mapping[k] for k in sorted(mapping)})
    diffs = []
    for dn in sorted(od):
        ot, onodes, otail = od[dn]
        nt, nnodes, ntail = nd[dn]
        if (ot, [mapping.get(n, n) for n in onodes]) != (nt, nnodes):
            diffs.append(f"{dn}: {ot} {[mapping.get(n, n) for n in onodes]} vs {nt} {nnodes}")
        if " ".join(_split_tokens(otail)) != " ".join(_split_tokens(ntail)):
            diffs.append(f"{dn} params: {otail!r} vs {ntail!r}")
    if diffs:
        log("DIFFERENCES:")
        for x in diffs:
            log("   ", x)
        return False
    log("known devices, topology, parameters and all remaining netlist content match")
    return True


def compare_controllers(old_text, new_text, log=print):
    old, new = controller_lines(old_text), controller_lines(new_text)
    missing = sorted((old - new).elements())
    added = sorted((new - old).elements())
    for line in missing:
        log("   MISSING:", line)
    for line in added:
        log("   ADDED  :", line)
    if not missing and not added:
        log(f"   controller/VAR lines identical ({sum(old.values())})")
    return not missing and not added


def equivalent(old_text, new_text, log=print, device_types=None):
    return (compare_netlists(old_text, new_text, log, device_types)
            and compare_controllers(old_text, new_text, log))


def compare_spec_connectivity(spec, pin_indices, netlist_text, log=print, device_types=None):
    """Compare spec pin groups using ADS term_number-derived netlist indices."""
    part_pins = {}
    ground_parts = set()
    for part in spec["parts"]:
        part_pins[part["name"]] = pin_indices.get(part["master"], {})
        if part["cell"] == "GROUND":
            ground_parts.add(part["name"])
    expected = []
    for net, refs in spec["nets"].items():
        incidence = set()
        for part, pin in refs:
            if part in ground_parts:
                continue
            if part not in part_pins or pin not in part_pins[part]:
                raise ValueError(f"cannot map spec pin {part}.{pin} to netlist pin index")
            incidence.add((part, part_pins[part][pin]))
        expected.append(frozenset(incidence))
    _, actual, _ = parse_netlist(netlist_text, device_types)
    old_counter = Counter(expected)
    new_counter = Counter(actual.values())
    log(f"spec: {len(expected)} nets     generated: {len(actual)} nets")
    if old_counter != new_counter:
        log("SPEC/GENERATED CONNECTIVITY MISMATCH:")
        for item in sorted((old_counter - new_counter).elements(), key=lambda x: sorted(x)):
            log("   SPEC ONLY:", sorted(item))
        for item in sorted((new_counter - old_counter).elements(), key=lambda x: sorted(x)):
            log("   GENERATED ONLY:", sorted(item))
        return False
    log("generated netlist connectivity matches spec pin groups")
    return True


def unknown_device_lines(text, device_types=None):
    """Return device-like lines excluded from the floating-pin analysis."""
    types = set(DEVTYPES)
    if device_types:
        types.update(device_types)
    _, _, opaque = parse_netlist(text, device_types)
    unknown = []
    for line in opaque:
        if "\n" in line or line.startswith("OutputPlan:"):
            continue
        match = DEV_RE.match(line)
        if match and match.group(1) not in types:
            unknown.append(line)
    return unknown
