"""Dataset post-processing via keysight.edatoolbox."""
import csv
import math


def _dataframe(ds_path, varblock=None):
    from keysight.edatoolbox.dataset import Dataset
    ds = Dataset(ds_path)
    blocks = list(ds.varblocks)
    if not blocks:
        raise ValueError(f"dataset has no varblocks: {ds_path}")
    if varblock is None:
        hb = [b for b in blocks if str(b).upper() == "HB" or str(b).upper().endswith(".HB")]
        if len(blocks) == 1:
            varblock = blocks[0]
        elif len(hb) == 1:
            varblock = hb[0]
        else:
            raise ValueError(f"multiple varblocks {blocks}; specify varblock explicitly")
    if varblock not in blocks:
        raise ValueError(f"varblock '{varblock}' not in dataset; available: {blocks}")
    return ds.to_dataframe(varblock)


def eta_curve(ds_path, rl, up=None, dn=None, varblock=None, power_axis="pwr"):
    """-> list of (pwr_dBm, vdc, eta_pct); None when no output probe is present."""
    rl = float(rl)
    if not math.isfinite(rl) or rl <= 0:
        raise ValueError("load resistance rl must be finite and positive")
    df = _dataframe(ds_path, varblock)
    cols = set(df.columns)
    if up is None:
        if "V_UP.net" in cols:
            up = "V_UP.net"
        elif "V_OUT.net" in cols:
            up = "V_OUT.net"
        else:
            return None
    if dn is None and up == "V_UP.net":
        if "V_DN.net" not in cols:
            raise ValueError("dual-rail probe V_UP.net requires V_DN.net")
        dn = "V_DN.net"
    if up not in cols:
        raise ValueError(f"probe column '{up}' not in dataset")
    if dn is not None and dn not in cols:
        raise ValueError(f"probe column '{dn}' not in dataset")

    names = [str(n).lower() if n is not None else "" for n in df.index.names]
    if "freq" not in names:
        raise ValueError(f"dataset index has no freq axis: {df.index.names}")
    axis = power_axis.lower()
    if axis not in names:
        raise ValueError(f"dataset index has no requested power axis '{power_axis}': {df.index.names}")
    power_levels = [i for i, name in enumerate(names) if name != "freq"]
    if len(power_levels) != 1 or names[power_levels[0]] != axis:
        raise ValueError(f"dataset needs exactly one swept axis named '{power_axis}', got {df.index.names}")
    power_level = power_levels[0]
    freq_name = df.index.names[names.index("freq")]

    rows = []
    for p in sorted(df.index.get_level_values(power_level).unique()):
        p_value = float(p)
        if not math.isfinite(p_value):
            raise ValueError(f"non-finite power value: {p}")
        sub = df.xs(p, level=power_level)
        freqs = [float(f) for f in sub.index.get_level_values(freq_name)]
        dc_positions = [i for i, f in enumerate(freqs) if f == 0.0]
        if len(dc_positions) != 1:
            raise ValueError(f"power {p} has {len(dc_positions)} DC rows; expected exactly one")
        row = sub.iloc[dc_positions[0]]
        vup = complex(row[up]).real
        vdn = complex(row[dn]).real if dn else 0.0
        vdc = vup - vdn
        if not math.isfinite(vdc):
            raise ValueError(f"power {p} has non-finite DC voltage")
        pin_w = 10 ** (p_value / 10.0) / 1000.0
        rows.append((p_value, vdc, 100.0 * (vdc * vdc / rl) / pin_w))
    return rows


def export_csv(rows, path, header=("pwr_dBm", "vdc", "eta_pct")):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([f"{x:.6g}" for x in r])
    return path


def plot(rows, path, title="PCE vs input power"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p = [r[0] for r in rows]
    e = [r[2] for r in rows]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(p, e, marker="o", lw=1)
    ax.set_xlabel("input power (dBm)")
    ax.set_ylabel("PCE (%)")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def summarize(rows):
    if not rows:
        return None
    best = max(rows, key=lambda r: r[2])
    return {"points": len(rows),
            "peak_eta_pct": round(best[2], 4),
            "at_pwr_dBm": best[0],
            "vdc_at_peak": round(abs(best[1]), 4)}
