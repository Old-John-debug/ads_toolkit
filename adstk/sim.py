"""Headless simulation: netlist text -> hpeesofsim -> dataset file."""
import os
import subprocess

SIM_REL = os.path.join("bin", "hpeesofsim.exe" if os.name == "nt" else "hpeesofsim")


def sim_exe(ads):
    return os.path.join(ads, SIM_REL)


def _sanitize_netlist(netlist_text):
    """Drop optional post-processing directives that break headless runs.

    ADS adds `#load "python","LinearCollapse"` and a `Component ...` line to
    S-parameter netlists; when the embedded netlist Python cannot import that
    module the whole run aborts even though the requested data does not depend
    on it (observed on ADS 2024 Update 2).
    """
    out = []
    for line in netlist_text.splitlines():
        s = line.strip()
        if s.startswith('#load "python","LinearCollapse"'):
            continue
        if s.startswith('Component Module="LinearCollapse"'):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def run_netlist(netlist_text, outdir, ads, name="nl.log", timeout=600):
    """Write the netlist into outdir and run hpeesofsim there.

    Returns (ok, ds_path_or_None, log_tail_str). The target dataset is removed
    before the run so a previous result can never be mistaken for a new one.
    ADS-internal DLL directories are prepended to PATH so that hpeesofsim can
    start on installations that keep helper DLLs outside <ADS>/bin.
    """
    from . import core

    netlist_text = _sanitize_netlist(netlist_text)
    top = _top_name(netlist_text)
    if not name or name in (".", "..") or "/" in name or "\\" in name or os.path.isabs(name):
        raise ValueError("netlist name must be a plain filename, not a path")
    outdir = os.path.abspath(outdir)
    ds_path = os.path.abspath(os.path.join(outdir, top + ".ds"))
    if os.path.commonpath([outdir, ds_path]) != outdir:
        raise ValueError("dataset path escapes the simulation output directory")
    os.makedirs(outdir, exist_ok=True)
    nl_path = os.path.join(outdir, name)
    with open(nl_path, "w", encoding="utf-8") as fh:
        fh.write(netlist_text)
    for path in (ds_path, os.path.join(outdir, "spectra.raw")):
        if os.path.exists(path):
            os.remove(path)
    env = dict(os.environ, HPEESOF_DIR=ads, COMPL_DIR=ads)
    extra = core.sim_path_dirs(ads)
    if extra:
        old = env.get("PATH")
        env["PATH"] = os.pathsep.join(extra + ([old] if old else []))
    try:
        r = subprocess.run([sim_exe(ads), name], cwd=outdir, capture_output=True,
                           text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, None, f"hpeesofsim timed out after {timeout} seconds"
    except OSError as e:
        return False, None, f"cannot run hpeesofsim: {e}"
    output = "\n".join(x for x in (r.stdout, r.stderr) if x)
    tail = "\n".join(output.strip().splitlines()[-12:])
    ok = r.returncode == 0 and "error detected" not in output.lower()
    ds = ds_path if ok and os.path.isfile(ds_path) and os.path.getsize(ds_path) > 0 else None
    return ok, ds, tail


def _top_name(netlist_text):
    for raw in netlist_text.splitlines():
        line = raw.strip()
        if line.startswith("; Top Design:") and '"' in line:
            quoted = line.split('"')[1]
            parts = quoted.split(":")
            if len(parts) >= 2 and parts[-2]:
                cell = parts[-2]
                if (cell in (".", "..") or "/" in cell or "\\" in cell
                        or os.path.isabs(cell) or os.path.splitdrive(cell)[0]):
                    raise ValueError(f"unsafe top design cell name: {cell!r}")
                return cell
    raise ValueError("netlist lacks a valid '; Top Design: \"lib:cell:view\"' header")


def netlist_from_design(spec):
    """Generate the netlist of the design as saved on disk (read-only use)."""
    from . import core

    core.setup_env(spec.get("ads"))
    import keysight.ads.de as de
    from keysight.ads.de.db import DesignMode
    core.open_libraries(de, spec)
    ws = de.Workspace(spec["design"]["workspace"])
    ws.open()
    try:
        d = ws.design((spec["design"]["library"], spec["design"]["cell"], "schematic"),
                      DesignMode.READ_ONLY)
        return d.generate_netlist()
    finally:
        ws.close()   # never saved here
