# -*- coding: utf-8 -*-
"""Package an already-built N-port schematic into an ADS subcircuit symbol.

The schematic must already exist (e.g. built by `adstk build`). This tool:
  1. removes temporary `Term` instances used as ports,
  2. removes any existing hierarchical terms,
  3. adds one numbered term + pin per port at the given coordinates,
  4. rebuilds the symbol view from the schematic,
  5. prints the term -> symbol-pin mapping.

Usage (from the toolkit root):
    "<ADS>\\tools\\python\\python.exe" tools/package_subcircuit.py config.json

Config (see templates/port_config.json):
{
  "workspace": "F:/work/my_wrk",
  "cell": "my_lib:HSMS286C_pkg",
  "lead_len": 0.5,
  "lead_spacing": 1.0,
  "ports": [
    {"name": "COM", "num": 1, "anchor": "L3.P1", "loc": [-1, 0], "angle": 180},
    {"name": "A1",  "num": 2, "anchor": "L1.P1", "loc": [-1, 2], "angle": 90},
    {"name": "K2",  "num": 3, "anchor": "L2.P2", "loc": [5.5, -1], "angle": 270}
  ]
}

- `anchor` is an instance pin on the target net, exactly as ADS names it
  (e.g. `L3.P1`). The net is found by that pin.
- `loc` is the port pin location in user units.
- `angle` selects the symbol edge: 0=right, 90=top, 180=left, 270=bottom.
- `workspace` may be absolute or relative to the config file.
- `lead_len` / `lead_spacing` are passed to the ADS Symbol Generator.

The symbol generator uses `keysight.ads.de.experimental.generate_symbol`
(shipped with ADS 2024/2025). The generated symbol pins keep the term names;
the parent schematic instantiates the cell as a normal X device.

Close the ADS GUI on the target cell before running: `build --apply` style
locks also block symbol regeneration.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from adstk import core

ADS = core.ads_root()
core.setup_env({"ads": ADS})
for _d in reversed(core.sim_path_dirs(ADS)):
    os.environ['PATH'] = _d + os.pathsep + os.environ.get('PATH', '')

import keysight.ads.de as de
from keysight.ads.de.db import DesignMode, Transaction
from keysight.ads.de.db.db_types import TermType
from keysight.ads.de.experimental_uu import db as db_uu
from keysight.ads.de.experimental.generate_symbol import de_generate_symbol_ex


def main(cfg_path):
    cfg_path = os.path.abspath(cfg_path)
    cfg = json.load(open(cfg_path, encoding='utf-8'))
    ws_path = core.resolve_path(cfg['workspace'], os.path.dirname(cfg_path))
    lib_name, cell_name = cfg['cell'].split(':', 1)
    cell_ref = (lib_name, cell_name, 'schematic')

    ws = de.Workspace(ws_path); ws.open()
    lib = de.Library.open(lib_name, os.path.join(ws_path, lib_name), de.LibraryMode.SHARED)

    # 1a) delete temporary Term instances
    d = ws.design(cell_ref, DesignMode.APPEND)
    with Transaction(d, 'delete Term instances') as tx:
        for it in list(d.instances):
            if getattr(it, 'cell_name', '') == 'Term':
                print('delete Term instance', it.inst_name)
                it.delete_object()
        tx.commit()
    d.save_design()

    # 1b) delete existing hierarchical terms
    design = db_uu.open_design(cell_ref, DesignMode.APPEND)
    for t in list(design.get_term_iter()):
        print('delete term', t.name, t.number)
        t.delete_object()
    design.save_design()

    # 2) add one numbered term/pin per port
    design = db_uu.open_design(cell_ref, DesignMode.APPEND)

    def net_with(inst_pin_name):
        for net in design.get_net_iter():
            for ip in net.inst_pins:
                if ip.inst_pin_name == inst_pin_name:
                    return net
        raise RuntimeError(f'no net found for instance pin {inst_pin_name}')

    for p in cfg['ports']:
        net = net_with(p['anchor'])
        term = design.add_numbered_term(net, p['name'], int(p['num']), TermType.INPUT_OUTPUT)
        fig = design.add_pin_fig_for_term_type(TermType.INPUT_OUTPUT, tuple(p['loc']))
        design.add_pin(term, fig, angle=float(p.get('angle', 0)))
        print(f"term {p['name']} num {p['num']} on net {net.name} at {p['loc']}")
    design.save_design()

    # 3) rebuild symbol
    if lib.cell(cell_name).view_exists('symbol'):
        lib.cell(cell_name).delete_view('symbol')
    lib.cell(cell_name).create_view('symbol', 'schematicSymbol')
    src = db_uu.open_design(cell_ref, DesignMode.READ_ONLY)
    sym = db_uu.open_design((lib_name, cell_name, 'symbol'), DesignMode.APPEND)
    de_generate_symbol_ex(sym, src, float(cfg.get('lead_len', 0.5)),
                          float(cfg.get('lead_spacing', 1.0)), replace=True)
    sym.save_design()

    # 4) report term -> symbol pin mapping
    sym = db_uu.open_design((lib_name, cell_name, 'symbol'), DesignMode.READ_ONLY)
    for t in sym.get_term_iter():
        print('symbol pin:', t.name, t.number, [p.name for p in t.pins])
    ws.close()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    main(sys.argv[1])
