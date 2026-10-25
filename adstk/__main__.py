"""CLI: python -m adstk {build|readback|sim|report|sweep|layout|convert} ..."""
import argparse
import json
import os
import sys
import tempfile


def cmd_build(a):
    from . import build as B
    spec = B.load_spec(a.spec)
    res = B.build(spec, dry=not a.apply, reference=a.reference)
    state = " (dry)" if not a.apply else (" (saved)" if res.get("saved") else " (not saved)")
    print("ok:", res["ok"], state)
    if res["problems"]:
        print("PROBLEMS:")
        for p in res["problems"]:
            print("  ", p)
        return 1
    if a.netlist_out and res["netlist"]:
        with open(a.netlist_out, "w", encoding="utf-8") as fh:
            fh.write(res["netlist"])
        print("netlist ->", a.netlist_out)
    return 0 if res["ok"] else 1


def cmd_readback(a):
    from . import build as B, core, readback

    spec = B.load_design(a.spec)
    core.setup_env(spec.get("ads"))
    import keysight.ads.de as de
    from keysight.ads.de.db import DesignMode
    for lib, path in core.lib_paths(spec).items():
        try:
            de.Library.open(lib, path, de.LibraryMode.READ_ONLY)
        except Exception:  # noqa: BLE001
            pass
    ws = de.Workspace(spec["design"]["workspace"])
    ws.open()
    try:
        d = ws.design((spec["design"]["library"], spec["design"]["cell"], "schematic"),
                      DesignMode.READ_ONLY)
        nl = d.generate_netlist()
        readback.report(d, nl, device_types=spec.get("device_types"))
        if a.netlist_out:
            with open(a.netlist_out, "w", encoding="utf-8") as fh:
                fh.write(nl)
            print("netlist ->", a.netlist_out)
    finally:
        ws.close()
    return 0


def cmd_sim(a):
    from . import build as B, sim, results, core

    spec = B.load_spec(a.spec)
    if a.netlist:
        with open(a.netlist, encoding="utf-8") as fh:
            nl = fh.read()
    else:
        nl = sim.netlist_from_design(spec)
    outdir = a.outdir or os.path.join(os.path.dirname(os.path.abspath(a.spec)), "sim_out")
    ads = core.ads_root(spec)
    ok, ds, tail = sim.run_netlist(nl, outdir, ads, timeout=a.timeout)
    print(tail)
    print("sim ok:", ok, "ds:", ds)
    if not ok or ds is None:
        return 1
    rl = a.rl if a.rl is not None else float(spec.get("vars", {}).get("rl", 0) or 0)
    if rl <= 0:
        print("need --rl (positive load resistance)")
        return 1
    rows = results.eta_curve(ds, rl, up=a.up, dn=a.dn,
                             varblock=a.varblock or spec.get("varblock"),
                             power_axis=a.power_axis or spec.get("power_axis") or "pwr")
    if rows is None:
        print("eta vectors missing in dataset")
        return 1
    print("summary:", results.summarize(rows))
    if a.csv:
        results.export_csv(rows, a.csv)
        print("csv ->", a.csv)
    if a.png:
        results.plot(rows, a.png)
        print("png ->", a.png)
    return 0


def cmd_report(a):
    from . import results
    rows = results.eta_curve(a.ds, a.rl, up=a.up, dn=a.dn,
                             varblock=a.varblock, power_axis=a.power_axis)
    if rows is None:
        print("eta vectors missing in dataset")
        return 1
    print("summary:", results.summarize(rows))
    if a.csv:
        results.export_csv(rows, a.csv)
        print("csv ->", a.csv)
    if a.png:
        results.plot(rows, a.png)
        print("png ->", a.png)
    return 0


def cmd_sweep(a):
    from . import build as B, sweep as S

    spec = B.load_spec(a.spec)
    values = [v.strip() for v in a.values.split(",") if v.strip()]
    outdir = a.outdir or os.path.join(os.path.dirname(os.path.abspath(a.spec)), "sweep_out")
    print(f"sweeping {a.part}.{a.param}; eta uses the fixed --rl {a.rl} for EVERY point "
          "(do not sweep the load resistor itself)")
    errors = []
    rows = S.sweep(spec, a.part, a.param, values, a.rl, outdir, pwr=a.pwr,
                   timeout=a.timeout, errors=errors, up=a.up, dn=a.dn)
    for v, eta, p in rows:
        if eta is None:
            print(f"  {v:>10s}  FAILED")
        else:
            print(f"  {v:>10s}  eta={eta:6.2f}% @ {p:.0f} dBm")
    for v, message in errors:
        print(f"  {v}: {message}", file=sys.stderr)
    best = max((r for r in rows if r[1] is not None), key=lambda r: r[1], default=None)
    if best:
        print("best:", best)
    return 1 if errors else 0


def cmd_load_sweep(a):
    from . import build as B, sweep as S
    import csv

    spec = B.load_spec(a.spec)
    values = [v.strip() for v in a.values.split(",") if v.strip()]
    outdir = a.outdir or os.path.join(os.path.dirname(os.path.abspath(a.spec)), "load_sweep_out")
    print(f"load sweep {a.part}.{a.param}; each point uses its own load as PCE RL")
    errors = []
    rows = S.load_sweep(spec, a.part, a.param, values, outdir, pwr=a.pwr,
                        timeout=a.timeout, errors=errors, up=a.up, dn=a.dn)
    for value, vdc, eta, power in rows:
        if eta is None:
            print(f"  RL={value:>10s}  FAILED")
        else:
            print(f"  RL={value:>10s}  Vdc={vdc:8.4f} V  eta={eta:6.2f}% @ {power:.0f} dBm")
    for value, message in errors:
        print(f"  {value}: {message}", file=sys.stderr)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(("RL", "vdc", "eta_pct", "pwr_dBm"))
            for row in rows:
                writer.writerow(["" if x is None else f"{x:.6g}" if isinstance(x, float) else x
                                 for x in row])
        print("csv ->", a.csv)
    return 1 if errors else 0


def cmd_layout(a):
    from . import layout, plan, textspec

    plan_path = os.path.abspath(a.plan)
    with open(plan_path, encoding="utf-8") as fh:
        spec, layout_hint = plan.parse(fh.read(), base_dir=os.path.dirname(plan_path))
    table = layout.load_pin_table(a.pin_table)
    spec, problems, notes = layout.generate(spec, layout_hint, table)
    for note in notes:
        print("layout:", note)
    if problems:
        print("LAYOUT PROBLEMS:")
        for problem in problems:
            print("  ", problem)
        return 1
    text = textspec.dumps(spec, base=os.path.dirname(os.path.abspath(a.output)))
    _atomic_write(a.output, text)
    print(f"layout spec -> {a.output}  parts={len(spec['parts'])} nets={len(spec['nets'])} wires={len(spec['wires'])}")
    print("next:", f'"<ADS>\\tools\\python\\python.exe" -B -m adstk build "{a.output}" '
                   '--reference <reference.log> --netlist-out <built.log>')
    return 0


def _atomic_write(path, text):
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".adstk_", dir=os.path.dirname(target), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def cmd_convert(a):
    from . import build as B, textspec

    spec = B.load_spec(a.input)
    if a.output.lower().endswith(".json"):
        output = dict(spec)
        output["parts"] = [{k: v for k, v in part.items() if k != "master"}
                           for part in spec["parts"]]
        output["nets"] = {net: [list(ref) for ref in refs]
                          for net, refs in spec["nets"].items()}
        text = json.dumps(output, ensure_ascii=False, indent=1) + "\n"
    else:
        text = textspec.dumps(spec, base=os.path.dirname(os.path.abspath(a.output)))
    _atomic_write(a.output, text)
    print("spec ->", a.output)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="adstk")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="build a schematic from JSON or fixed text spec (audit-first)")
    p.add_argument("spec")
    p.add_argument("--apply", action="store_true", help="commit + save (default: dry)")
    p.add_argument("--reference", help="netlist file to compare against")
    p.add_argument("--netlist-out", help="write the generated netlist here")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("readback", help="read back a design as saved (read-only)")
    p.add_argument("spec")
    p.add_argument("--netlist-out", help="write the generated netlist here")
    p.set_defaults(fn=cmd_readback)

    p = sub.add_parser("sim", help="headless hpeesofsim run + eta post-processing")
    p.add_argument("spec")
    p.add_argument("--netlist", help="use this netlist file instead of the saved design")
    p.add_argument("--outdir", help="simulation working directory")
    p.add_argument("--rl", type=float, help="load resistance for eta")
    p.add_argument("--varblock", help="dataset varblock when automatic selection is ambiguous")
    p.add_argument("--power-axis", default=None,
                   help="index name of the swept input power (default: spec power_axis or 'pwr')")
    p.add_argument("--up", help="voltage probe column for the top rail (default: V_UP.net or V_OUT.net)")
    p.add_argument("--dn", help="voltage probe column for the bottom rail (default: 0 for single-ended)")
    p.add_argument("--timeout", type=float, default=600, help="hpeesofsim timeout in seconds")
    p.add_argument("--csv")
    p.add_argument("--png")
    p.set_defaults(fn=cmd_sim)

    p = sub.add_parser("report", help="post-process an existing dataset")
    p.add_argument("--ds", required=True)
    p.add_argument("--rl", type=float, required=True)
    p.add_argument("--varblock", help="dataset varblock when automatic selection is ambiguous")
    p.add_argument("--power-axis", default="pwr", help="index name of the swept input power")
    p.add_argument("--up", help="voltage probe column for the top rail (default: V_UP.net or V_OUT.net)")
    p.add_argument("--dn", help="voltage probe column for the bottom rail (default: 0 for single-ended)")
    p.add_argument("--csv")
    p.add_argument("--png")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("sweep", help="in-memory param sweep (no design writes)")
    p.add_argument("spec")
    p.add_argument("--part", required=True)
    p.add_argument("--param", required=True)
    p.add_argument("--values", required=True, help="comma separated, e.g. 12,16,20")
    p.add_argument("--rl", type=float, required=True)
    p.add_argument("--pwr", type=float, help="evaluate eta at this exact input power")
    p.add_argument("--outdir", help="root dir for per-combo sim outputs")
    p.add_argument("--timeout", type=float, default=600, help="per-combo timeout in seconds")
    p.add_argument("--up", help="voltage probe column for the top rail (default: V_UP.net or V_OUT.net)")
    p.add_argument("--dn", help="voltage probe column for the bottom rail (default: 0 for single-ended)")
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("load-sweep", help="sweep a load and use each value as RL in PCE")
    p.add_argument("spec")
    p.add_argument("--part", required=True, help="load part name, e.g. R1")
    p.add_argument("--param", default="R", help="load parameter name (default: R)")
    p.add_argument("--values", required=True, help="comma separated positive load values")
    p.add_argument("--pwr", type=float, required=True, help="evaluate eta at this exact input power")
    p.add_argument("--outdir", help="root dir for per-load sim outputs")
    p.add_argument("--timeout", type=float, default=600, help="per-load timeout in seconds")
    p.add_argument("--up", help="voltage probe column for the top rail (default: V_UP.net or V_OUT.net)")
    p.add_argument("--dn", help="voltage probe column for the bottom rail (default: 0 for single-ended)")
    p.add_argument("--csv", help="write load sweep CSV")
    p.set_defaults(fn=cmd_load_sweep)

    p = sub.add_parser("layout", help="generate v1 geometry from a simplified layout plan")
    p.add_argument("plan", help="simplified .plan.txt file")
    p.add_argument("output", help="generated v1 .txt file")
    p.add_argument("--pin-table", help="override templates/device_pin_table.json")
    p.set_defaults(fn=cmd_layout)

    p = sub.add_parser("convert", help="convert between JSON and fixed Chinese text spec v1")
    p.add_argument("input", help="input .json or text spec")
    p.add_argument("output", help="output .json or text spec")
    p.set_defaults(fn=cmd_convert)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
