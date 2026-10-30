"""ads_toolkit core: environment, session, parameters, symbol geometry.

Everything here is config-driven: paths come from a spec dict / environment,
never hard-coded, so the same code serves any ADS project on this machine.
"""
import os
import glob
import shutil
import sys
import time

# library name -> path under the ADS install root (machine-specific, not project-specific)
# vendor/project kits (e.g. HFDiode) are NOT listed here; pass them via spec["libs"].
LIB_SUBPATHS = {
    "ads_simulation": "oalibs/rf/ads_simulation",
    "ads_rflib": "oalibs/rf/ads_rflib",
    "ads_tlines": "oalibs/rf/ads_tlines",
    "ads_datacmps": "oalibs/rf/ads_datacmps",
    "ads_sources": "oalibs/rf/ads_sources",
}

# netlist string params that must be stored WITH quotes
QUOTE_PARAMS = {"SweepVar", "SimInstanceName", "NodeName"}

TOL = 1e-4


def ads_root(spec=None):
    """Resolve the ADS install root.

    Accepts a spec dict (uses spec["ads"]), a plain path string (setup_env and
    similar callers pass spec.get("ads") directly), or None (falls back to
    ADS_PATH/HPEESOF_DIR/COMPL_DIR, then infers from the running Python path).
    """
    if isinstance(spec, (str, os.PathLike)):
        return os.path.abspath(os.path.expandvars(os.path.expanduser(str(spec))))
    if spec and spec.get("ads"):
        return os.path.abspath(os.path.expandvars(os.path.expanduser(spec["ads"])))
    for var in ("ADS_PATH", "HPEESOF_DIR", "COMPL_DIR"):
        if os.environ.get(var):
            return os.path.abspath(os.path.expandvars(os.path.expanduser(os.environ[var])))
    current = os.path.dirname(os.path.abspath(sys.executable))
    sim_name = "hpeesofsim.exe" if os.name == "nt" else "hpeesofsim"
    for _ in range(6):
        if os.path.isfile(os.path.join(current, "bin", sim_name)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise RuntimeError("ADS installation not found: set spec['ads'] or ADS_PATH")


def setup_env(spec=None):
    ads = ads_root(spec)
    os.environ["HPEESOF_DIR"] = ads
    os.environ["COMPL_DIR"] = ads
    return ads


def lib_paths(spec=None):
    ads = ads_root(spec)
    paths = {name: os.path.join(ads, sub) for name, sub in LIB_SUBPATHS.items()}
    if spec:
        paths.update(spec.get("libs", {}))
    return paths


def resolve_path(value, base):
    """Expand env/user vars and make a spec path absolute relative to base."""
    value = os.path.expandvars(os.path.expanduser(str(value)))
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(base, value))


def sim_path_dirs(ads):
    """Directories to prepend to PATH when spawning hpeesofsim.

    Observed on ADS 2024 Update 2: hpeesofsim.exe aborts at startup with
    STATUS_DLL_NOT_FOUND (0xC0000135) unless helper DLLs that live outside
    <ADS>/bin (e.g. ptgem.dll, python310.dll) are reachable via PATH.
    """
    dirs = [os.path.join(ads, "bin"),
            os.path.join(ads, "adsptolemy", "lib.win32_64")]
    dirs += sorted(glob.glob(os.path.join(ads, "fem", "*", "win32_64", "bin")))
    return [d for d in dirs if os.path.isdir(d)]


def repeated_params(inst):
    """{name: param} for parameters whose value is a repeatable list.

    ADS 2024 defines some parameters (e.g. ParamSweep's SimInstanceName) as
    repeated parameters with no plain string_value; callers should set
    get_repeat(0).string_value instead.
    """
    m = {}
    for p in list(inst._impl.parameters):
        if hasattr(p, "number_of_repeats"):
            m.setdefault(getattr(p, "name", ""), p)
    return m


def open_libraries(de, spec=None, on_error=None):
    """Open every configured library read-only; report failures via callback."""
    for name, path in lib_paths(spec).items():
        try:
            de.Library.open(name, path, de.LibraryMode.READ_ONLY)
        except Exception as e:  # noqa: BLE001
            if on_error is not None:
                on_error(name, e)


def cell_dir(ws_path, lib, cell):
    enc = "".join(f"%{ch.upper()}" if ch.isupper() else ch for ch in cell)
    return os.path.join(ws_path, lib, enc, "schematic")


def sch_path(ws_path, lib, cell):
    return os.path.join(cell_dir(ws_path, lib, cell), "sch.oa")


def assert_no_lock(ws_path, lib, cell):
    lock = os.path.join(cell_dir(ws_path, lib, cell), "sch.oa.cdslck")
    if os.path.exists(lock):
        raise RuntimeError(f"ADS GUI holds a lock: {lock} - close ADS first")


def backup(path):
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000_000:09d}"
    dst = os.path.join(os.path.dirname(path), f"backup_{os.path.basename(path)}_{stamp}")
    shutil.copy2(path, dst)
    return dst


# ---------------------------------------------------------------- params ----
def walk(items):
    for p in items:
        yield p
        if hasattr(p, "num_repeats"):
            for i in range(p.num_repeats):
                yield from walk([p.get_repeat(i)])
        elif hasattr(p, "num_sub_params"):
            for i in range(p.num_sub_params):
                yield from walk([p.get_sub_param(i)])


def smap(inst):
    m = {}
    for p in walk(list(inst._impl.parameters)):
        if hasattr(p, "string_value"):
            m.setdefault(getattr(p, "name", ""), p)
    return m


def set_tahb_off(inst):
    p = next((p for p in list(inst._impl.parameters)
              if getattr(p, "name", "") == "TAHB_Enable"), None)
    if p is not None:
        p.form_name = "TAHBModeOff"
        return True
    return False


# -------------------------------------------------------------- geometry ----
def rot(p, angle):
    x, y = p
    return {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[angle % 360]


def discover_offsets(d, masters, pin_indices=None):
    """Measure R0 pin offsets and zero-based netlist terminal indices.

    The caller must roll the surrounding transaction back after this returns.
    """
    out = {}
    for i, master in enumerate(list(masters)):
        org = (2000.0 + 10.0 * i, 2000.0)
        inst = d.add_instance(master, org, name=f"TMP_OFF_{i}", angle=0)
        pins = list(inst.get_inst_pin_iter())
        out[master] = {p.master_pin.name: (round(float(p.snap_point.x) - org[0], 6),
                                           round(float(p.snap_point.y) - org[1], 6))
                       for p in pins}
        if pin_indices is not None:
            pin_indices[master] = {
                p.master_pin.name: int(p.master_pin.term_number) - 1 for p in pins
            }
        inst.delete_object()
    return out


def predict_pins(parts, offsets):
    """parts: list of dicts with name/master/origin/angle -> {(name, pin): (x, y)}."""
    P = {}
    for part in parts:
        org = part["origin"]
        ang = int(part.get("angle", 0))
        for pin, off in offsets[part["master"]].items():
            dx, dy = rot(off, ang)
            P[(part["name"], pin)] = (round(org[0] + dx, 6), round(org[1] + dy, 6))
    return P


def pt_of(obj):
    if hasattr(obj, "x") and hasattr(obj, "y"):
        return (round(float(obj.x), 4), round(float(obj.y), 4))
    return (round(float(obj[0]), 4), round(float(obj[1]), 4))


def box_of(inst, attr="bbox"):
    b = getattr(inst, attr)
    for lo_n, hi_n in (("lower_left", "upper_right"), ("ll", "ur"), ("low", "high"),
                       ("p1", "p2"), ("min", "max")):
        if hasattr(b, lo_n) and hasattr(b, hi_n):
            return pt_of(getattr(b, lo_n)), pt_of(getattr(b, hi_n))
    try:
        pts = list(b)
        if len(pts) == 2:
            return pt_of(pts[0]), pt_of(pts[1])
    except Exception:  # noqa: BLE001
        pass
    return None


def boxes_overlap(b1, b2, tol=TOL):
    (x1, y1), (x2, y2) = b1
    (u1, v1), (u2, v2) = b2
    return not (x2 <= u1 + tol or u2 <= x1 + tol or y2 <= v1 + tol or v2 <= y1 + tol)
