import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adstk import (audit, build, core, layout, netlistx, plan, readback,
                   results, sim, sweep, textspec)
from adstk.__main__ import main

try:
    import pandas as pd
except ImportError:
    pd = None


BASE_NETLIST = """; Top Design: \"L:cell:schematic\"
Options TopDesignName=\"L:cell:schematic\"
OutputPlan:HB1_Output Type=\"Output\" NodeName[1]=\"V_OUT\"
HB:HB1 MaxOrder=12 Freq[1]=2.45 GHz Order[1]=12 OutputPlan=\"HB1_Output\"
L:L1 N__1 N__2 L=10 nH
L:L2 N__2 0 L=10 nH
R:R1 N__2 0 R=50 Ohm
Port:P1 0 N__1 Num=1 Z=50 Ohm
"""


class SpecTests(unittest.TestCase):
    def test_relative_paths_and_angle_validation(self):
        spec = {"design": {"workspace": "ws", "library": "lib", "cell": "cell"},
                "libs": {"extra": "vendor"},
                "parts": [{"name": "R1", "lib": "l", "cell": "R", "origin": [0, 0]}],
                "nets": {}, "wires": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spec.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)
            loaded = build.load_spec(path)
            self.assertEqual(loaded["design"]["workspace"], os.path.join(tmp, "ws"))
            self.assertEqual(loaded["libs"]["extra"], os.path.join(tmp, "vendor"))
            spec["parts"][0]["angle"] = 45
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)
            with self.assertRaises(ValueError):
                build.load_spec(path)


class LayoutPlanTests(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PLAN = os.path.join(ROOT, "examples", "specs", "demo_layout.plan.txt")

    def _generate(self):
        with open(self.PLAN, encoding="utf-8") as fh:
            spec, hint = plan.parse(fh.read(), base_dir=os.path.dirname(self.PLAN))
        return layout.generate(spec, hint, layout.load_pin_table())

    def test_plan_libs_paths_resolve(self):
        with open(self.PLAN, encoding="utf-8") as fh:
            text = fh.read().replace("[变量]", "[库路径]\nVendorKit = vendor\n\n[变量]", 1)
        with tempfile.TemporaryDirectory() as tmp:
            spec, _hint = plan.parse(text, base_dir=tmp)
        self.assertEqual(spec["libs"]["VendorKit"], os.path.join(tmp, "vendor"))

    def test_plan_generates_audit_clean_v1(self):
        spec, problems, _ = self._generate()
        self.assertEqual(problems, [])
        self.assertEqual(len(spec["parts"]), 10)
        self.assertEqual(len(spec["wires"]), 8)
        for part in spec["parts"]:
            self.assertAlmostEqual(part["origin"][0] / layout.GRID,
                                   round(part["origin"][0] / layout.GRID))
            self.assertAlmostEqual(part["origin"][1] / layout.GRID,
                                   round(part["origin"][1] / layout.GRID))
        text = textspec.dumps(spec, base=os.path.dirname(self.PLAN))
        reparsed = textspec.parse(text)
        self.assertEqual(len(reparsed["parts"]), 10)
        self.assertEqual(len(reparsed["wires"]), 8)

    def test_layout_cli_writes_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "demo_layout.txt")
            self.assertEqual(main(["layout", self.PLAN, output]), 0)
            with open(output, encoding="utf-8") as fh:
                spec = textspec.parse(fh.read())
        self.assertEqual(len(spec["parts"]), 10)

    def test_multipin_device_ties_extra_pins_outside(self):
        table = {
            ("demo", "SRC"): {"P__0": (0, 0), "P__1": (1, 0)},
            ("demo", "GROUND"): {"P__0": (0, 0)},
            ("demo", "D6"): {"P__0": (0, 0), "P__1": (0, -0.25), "P__5": (0, -0.5),
                             "P__4": (1, 0), "P__3": (1, -0.25), "P__2": (1, -0.5)},
            ("demo", "C"): {"P__0": (1, 0), "P__1": (0, 0)},
            ("demo", "R"): {"P__0": (1, 0), "P__1": (0, 0)},
            ("demo", "VP"): {"P__0": (0, 0)},
            ("demo", "CTRL"): {},
        }
        parts = [
            {"name": "S1", "lib": "demo", "cell": "SRC", "params": {}, "master": ("demo", "SRC", "symbol")},
            {"name": "GND1", "lib": "demo", "cell": "GROUND", "params": {}, "master": ("demo", "GROUND", "symbol")},
            {"name": "D1", "lib": "demo", "cell": "D6", "params": {}, "master": ("demo", "D6", "symbol")},
            {"name": "C1", "lib": "demo", "cell": "C", "params": {}, "master": ("demo", "C", "symbol")},
            {"name": "R1", "lib": "demo", "cell": "R", "params": {}, "master": ("demo", "R", "symbol")},
            {"name": "V1", "lib": "demo", "cell": "VP", "params": {}, "master": ("demo", "VP", "symbol")},
            {"name": "CTRL", "lib": "demo", "cell": "CTRL", "params": {}, "master": ("demo", "CTRL", "symbol")},
        ]
        spec = {"design": {"workspace": "ws", "library": "lib", "cell": "cell"},
                "parts": parts,
                "nets": {"gnd": [["S1", "P__0"], ["GND1", "P__0"], ["C1", "P__0"], ["R1", "P__0"]],
                         "in": [["S1", "P__1"], ["D1", "P__0"], ["D1", "P__1"], ["D1", "P__5"], ["V1", "P__0"]],
                         "out": [["D1", "P__2"], ["D1", "P__3"], ["D1", "P__4"],
                                 ["C1", "P__1"], ["R1", "P__1"]]},
                "vars": {}, "tahb_off": False}
        hint = {"主链": ["S1", "D1"], "对地": ["C1", "R1"], "探针": ["V1"],
                "控制器": ["CTRL"], "地网络": "gnd"}
        generated, problems, _ = layout.generate(spec, hint, table)
        self.assertEqual(problems, [])
        input_wires = [w for w in generated["wires"] if w["net"] == "in"]
        output_wires = [w for w in generated["wires"] if w["net"] == "out"]
        diode_origin = next(p["origin"] for p in generated["parts"] if p["name"] == "D1")
        input_outside = diode_origin[0] - 0.5
        output_outside = diode_origin[0] + 1.5
        self.assertTrue(any(input_outside in [p[0] for p in w["points"]] for w in input_wires))
        self.assertTrue(any(output_outside in [p[0] for p in w["points"]] for w in output_wires))

    def test_unassigned_part_is_rejected(self):
        with open(self.PLAN, encoding="utf-8") as fh:
            spec, hint = plan.parse(fh.read(), base_dir=os.path.dirname(self.PLAN))
        hint["探针"] = [name for name in hint["探针"] if name != "V_OUT"]
        with self.assertRaisesRegex(ValueError, "not assigned"):
            layout.generate(spec, hint, layout.load_pin_table())


class DesignOnlySpecTests(unittest.TestCase):
    def test_load_design_accepts_design_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "design.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"design": {"workspace": "ws", "library": "lib", "cell": "cell"}}, fh)
            loaded = build.load_design(path)
            self.assertEqual(loaded["design"]["workspace"], os.path.join(tmp, "ws"))
            with self.assertRaises(ValueError):
                build.load_spec(path)


class NetlistTests(unittest.TestCase):
    def test_node_names_may_change(self):
        new = BASE_NETLIST.replace("N__2", "N__9")
        self.assertTrue(netlistx.equivalent(BASE_NETLIST, new, log=lambda *a: None))

    def test_mutual_coupling_change_is_rejected(self):
        self.assertFalse(netlistx.equivalent(
            BASE_NETLIST + "K:K1 L1 L2 0.9\n", BASE_NETLIST, log=lambda *a: None))

    def test_model_change_is_rejected(self):
        old = BASE_NETLIST + "model M1 D1 Is=1e-12 N=1.05\n"
        new = BASE_NETLIST + "model M1 D1 Is=1e-7 N=2.0\n"
        self.assertFalse(netlistx.equivalent(old, new, log=lambda *a: None))

    def test_output_plan_change_is_rejected(self):
        old = BASE_NETLIST.replace('NodeName[1]="V_OUT"', 'NodeName[1]="V_IN"')
        self.assertFalse(netlistx.equivalent(old, BASE_NETLIST, log=lambda *a: None))

    def test_project_device_types_can_be_added(self):
        diode = "X_DIODE:D1 N__2 N__3 N__3\n"
        old = BASE_NETLIST + diode
        new = old.replace("N__3", "N__8")
        self.assertTrue(netlistx.equivalent(
            old, new, log=lambda *a: None, device_types=["X_DIODE"]))

    def test_unknown_type_is_compared_not_dropped(self):
        old = BASE_NETLIST + "MLIN:ML1 N__1 N__2 W=1 mm L=2 mm\n"
        new = BASE_NETLIST + "MLIN:ML1 N__1 N__2 W=1 mm L=3 mm\n"
        self.assertFalse(netlistx.equivalent(old, new, log=lambda *a: None))


class NetlistEdgeTests(unittest.TestCase):
    BASE = '; Top Design: "L:c:schematic"\nOptions Note="a  b"\nR:R1 N__1 0 R=50 Ohm\nPort:P1 0 N__1 Num=1\n'

    def test_quoted_whitespace_change_is_rejected(self):
        self.assertFalse(netlistx.equivalent(
            self.BASE, self.BASE.replace("a  b", "a b"), log=lambda *a: None))
        self.assertTrue(netlistx.equivalent(self.BASE, self.BASE, log=lambda *a: None))

    def test_unknown_hash_directive_is_not_discarded(self):
        self.assertFalse(netlistx.equivalent(
            self.BASE + '#include "a"\n', self.BASE + '#include "b"\n', log=lambda *a: None))


class AuditTests(unittest.TestCase):
    def test_same_net_t_junction_is_allowed(self):
        nets = {"A": [("P1", "p"), ("P2", "p"), ("P3", "p")]}
        wires = [("A", [(0, 0), (2, 0)]), ("A", [(1, 0), (1, 1)])]
        pins = {("P1", "p"): (0, 0), ("P2", "p"): (2, 0), ("P3", "p"): (1, 1)}
        problems, _ = audit.audit(nets, wires, pins)
        self.assertEqual(problems, [])

    def test_pin_missing_from_nets_is_rejected(self):
        nets = {"A": [("P1", "p"), ("P2", "p")]}
        wires = [("A", [(0, 0), (2, 0)])]
        pins = {("P1", "p"): (0, 0), ("P2", "p"): (2, 0), ("P3", "p"): (1, 1)}
        problems, _ = audit.audit(nets, wires, pins)
        self.assertTrue(any("missing from spec nets" in p for p in problems))

    def test_zero_length_wire_is_rejected(self):
        nets = {"A": [("P1", "p"), ("P2", "p")]}
        wires = [("A", [(0, 0), (0, 0), (2, 0)])]
        pins = {("P1", "p"): (0, 0), ("P2", "p"): (2, 0)}
        problems, _ = audit.audit(nets, wires, pins)
        self.assertTrue(any("zero-length" in p for p in problems))

    def test_non_orthogonal_wire_is_a_problem_not_an_exception(self):
        nets = {"A": [("P1", "p"), ("P2", "p")]}
        wires = [("A", [(0, 0), (1, 1), (2, 0)])]
        pins = {("P1", "p"): (0, 0), ("P2", "p"): (2, 0)}
        problems, _ = audit.audit(nets, wires, pins)
        self.assertTrue(any("non-orthogonal" in p for p in problems))

    def test_intermediate_vertex_on_foreign_net_is_rejected(self):
        nets = {"A": [("P1", "p"), ("P2", "p")], "B": [("P3", "p"), ("P4", "p")]}
        wires = [("A", [(0, 0), (2, 0)]), ("B", [(1, 1), (1, 0), (1, -1)])]
        pins = {("P1", "p"): (0, 0), ("P2", "p"): (2, 0),
                ("P3", "p"): (1, 1), ("P4", "p"): (1, -1)}
        problems, _ = audit.audit(nets, wires, pins)
        self.assertTrue(any("foreign net" in p for p in problems))

    def test_wire_through_body_is_rejected_but_pin_connection_is_allowed(self):
        boxes = {"R1": ((-0.5, -0.5), (0.5, 0.5))}
        pins = {("R1", "P__0"): (0.5, 0), ("R1", "P__1"): (-0.5, 0)}
        crossing = [("A", [(-2, 0), (2, 0)])]
        connected = [("A", [(0.5, 0), (2, 0)])]
        self.assertTrue(audit.wire_body_crossings(crossing, boxes, pins))
        self.assertEqual(audit.wire_body_crossings(connected, boxes, pins), [])

    def test_single_pin_probe_body_is_not_a_device_short(self):
        boxes = {"V1": ((-0.5, -0.5), (0.5, 0.5))}
        pins = {("V1", "P__0"): (-0.5, 0)}
        self.assertEqual(audit.wire_body_crossings([("A", [(-2, 0), (2, 0)])], boxes, pins), [])


class ConnectivityTests(unittest.TestCase):
    def test_disconnected_same_net_is_rejected(self):
        nets = {"A": [("P1", "p"), ("P2", "p"), ("P3", "p"), ("P4", "p")]}
        wires = [("A", [(0, 0), (1, 0)]), ("A", [(10, 0), (11, 0)])]
        pins = {("P1", "p"): (0, 0), ("P2", "p"): (1, 0),
                ("P3", "p"): (10, 0), ("P4", "p"): (11, 0)}
        problems, _ = audit.audit(nets, wires, pins)
        self.assertTrue(any("disconnected groups" in p for p in problems))

    def test_single_pin_net_is_rejected(self):
        problems, _ = audit.audit({"A": [("P1", "p")]}, [("A", [(0, 0), (1, 0)])],
                                  {("P1", "p"): (0, 0)})
        self.assertTrue(any("fewer than two pins" in p for p in problems))

    def test_grounded_islands_require_ground_symbol_per_island(self):
        nets = {"gnd": [("R1", "p"), ("G1", "p"), ("R2", "p"), ("G2", "p")]}
        wires = [("gnd", [(0, 0), (1, 0)]), ("gnd", [(10, 0), (11, 0)])]
        pins = {("R1", "p"): (0, 0), ("G1", "p"): (1, 0),
                ("R2", "p"): (10, 0), ("G2", "p"): (11, 0)}
        grounds = {("G1", "p"), ("G2", "p")}
        problems, _ = audit.audit(nets, wires, pins, ground_refs=grounds)
        self.assertEqual(problems, [])
        nets["gnd"] += [("R3", "p"), ("R4", "p")]
        wires.append(("gnd", [(20, 0), (21, 0)]))
        pins[("R3", "p")] = (20, 0)
        pins[("R4", "p")] = (21, 0)
        problems, _ = audit.audit(nets, wires, pins, ground_refs=grounds)
        self.assertTrue(any("without a GROUND" in p for p in problems))

    def test_generated_netlist_must_match_spec_pin_groups(self):
        r_master = ("ads_rflib", "R", "symbol")
        g_master = ("ads_rflib", "GROUND", "symbol")
        spec = {"parts": [
            {"name": "R1", "cell": "R", "master": r_master},
            {"name": "R2", "cell": "R", "master": r_master},
            {"name": "GND1", "cell": "GROUND", "master": g_master}],
            "nets": {"in": [["R1", "P__1"], ["R2", "P__0"]],
                     "gnd": [["R1", "P__0"], ["R2", "P__1"], ["GND1", "P__0"]]}}
        pin_indices = {r_master: {"P__0": 1, "P__1": 0}, g_master: {"P__0": 0}}
        good = "R:R1 0 N__1 R=50 Ohm\nR:R2 N__1 0 R=50 Ohm\n"
        bad = "R:R1 0 N__1 R=50 Ohm\nR:R2 N__2 0 R=50 Ohm\n"
        self.assertTrue(netlistx.compare_spec_connectivity(spec, pin_indices, good, log=lambda *a: None))
        self.assertFalse(netlistx.compare_spec_connectivity(spec, pin_indices, bad, log=lambda *a: None))


class ParameterTests(unittest.TestCase):
    def test_vars_without_var_instance_is_a_problem(self):
        spec = {"parts": [{"name": "R1", "cell": "R", "params": {}}],
                "vars": {"rl": "550"}, "var_part": None, "tahb_off": False}
        self.assertTrue(build._params(None, spec))

    def test_failed_tahb_setting_is_a_problem(self):
        spec = {"parts": [{"name": "HB1", "cell": "HarmonicBalance", "params": {}}],
                "vars": {}, "tahb_off": True}
        design = types.SimpleNamespace(get_instance=lambda _name: object())
        with mock.patch("adstk.core.set_tahb_off", return_value=False):
            self.assertTrue(build._params(design, spec))

    def test_multiple_var_instances_require_explicit_choice(self):
        spec = {"parts": [{"name": "V1", "cell": "VAR", "params": {}},
                          {"name": "V2", "cell": "VAR", "params": {}}],
                "vars": {"pwr": "3"}, "var_part": None, "tahb_off": False}
        self.assertTrue(any("multiple VAR" in p for p in build._params(None, spec)))

    def test_valid_vars_are_written(self):
        var = types.SimpleNamespace(vars={"old": "1", "pwr": "0"})
        design = types.SimpleNamespace(get_instance=lambda _name: var)
        spec = {"parts": [{"name": "VAR1", "cell": "VAR", "params": {}}],
                "vars": {"pwr": "3"}, "var_part": "VAR1", "tahb_off": False}
        self.assertEqual(build._params(design, spec), [])
        self.assertEqual(var.vars, {"pwr": "3"})


class SimTests(unittest.TestCase):
    def test_top_name_has_no_project_fallback(self):
        self.assertEqual(sim._top_name('; Top Design: "L:demo:schematic"\n'), "demo")
        with self.assertRaises(ValueError):
            sim._top_name("R:R1 0 1 R=50 Ohm\n")

    def test_stale_dataset_is_removed_before_run(self):
        netlist = '; Top Design: "L:demo:schematic"\n'
        with tempfile.TemporaryDirectory() as tmp:
            ds_path = os.path.join(tmp, "demo.ds")
            with open(ds_path, "w", encoding="utf-8") as fh:
                fh.write("old")

            def fake_run(_cmd, **_kwargs):
                self.assertFalse(os.path.exists(ds_path))
                with open(ds_path, "w", encoding="utf-8") as fh:
                    fh.write("new")
                return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(sim.subprocess, "run", side_effect=fake_run):
                ok, ds, _ = sim.run_netlist(netlist, tmp, "ADS")
            self.assertTrue(ok)
            with open(ds, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "new")

    def test_stderr_error_and_missing_dataset_fail(self):
        netlist = '; Top Design: "L:demo:schematic"\n'
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "demo.ds"), "w", encoding="utf-8") as fh:
                fh.write("stale")
            returned = types.SimpleNamespace(returncode=0, stdout="", stderr="Error detected by test")
            with mock.patch.object(sim.subprocess, "run", return_value=returned):
                ok, ds, _ = sim.run_netlist(netlist, tmp, "ADS")
            self.assertFalse(ok)
            self.assertIsNone(ds)
            self.assertFalse(os.path.exists(os.path.join(tmp, "demo.ds")))


class SimEdgeTests(unittest.TestCase):
    def test_unsafe_top_cell_is_rejected(self):
        for cell in ("../outside", "..\\outside"):
            with self.subTest(cell=cell):
                with self.assertRaises(ValueError):
                    sim._top_name(f'; Top Design: "lib:{cell}:schematic"\n')

    def test_netlist_name_cannot_escape_outdir(self):
        netlist = '; Top Design: "lib:cell:schematic"\n'
        with self.assertRaises(ValueError):
            sim.run_netlist(netlist, "unused", "ADS", name="../x.log")


@unittest.skipIf(pd is None, "pandas is required for dataset post-processing tests")
class ResultsTests(unittest.TestCase):
    def test_dc_row_and_power_axis_are_selected_explicitly(self):
        index = pd.MultiIndex.from_product([[0.0, 10.0], [0.0, 1e9]], names=["pwr", "freq"])
        df = pd.DataFrame({"V_OUT.net": [1 + 0j, 100 + 0j, 2 + 0j, 100 + 0j]}, index=index)
        with mock.patch.object(results, "_dataframe", return_value=df):
            rows = results.eta_curve("unused", rl=10000)
        self.assertEqual([r[1] for r in rows], [1.0, 2.0])
        self.assertAlmostEqual(rows[0][2], 10.0)
        self.assertAlmostEqual(rows[1][2], 4.0)

    def test_explicit_single_ended_probe_does_not_auto_select_vdn(self):
        index = pd.MultiIndex.from_product([[0.0], [0.0, 1e9]], names=["pwr", "freq"])
        df = pd.DataFrame({"V_UP.net": [3 + 0j, 0 + 0j],
                           "V_DN.net": [1 + 0j, 0 + 0j],
                           "V_OUT.net": [5 + 0j, 0 + 0j]}, index=index)
        with mock.patch.object(results, "_dataframe", return_value=df):
            dual = results.eta_curve("unused", rl=100)
            single = results.eta_curve("unused", rl=100, up="V_OUT.net")
        self.assertEqual(dual[0][1], 2.0)
        self.assertEqual(single[0][1], 5.0)

    def test_frequency_only_dataset_is_rejected(self):
        df = pd.DataFrame({"V_OUT.net": [1 + 0j]}, index=pd.Index([0.0], name="freq"))
        with mock.patch.object(results, "_dataframe", return_value=df):
            with self.assertRaises(ValueError):
                results.eta_curve("unused", rl=100)


@unittest.skipIf(pd is None, "pandas is required for PCE edge tests")
class ResultsEdgeTests(unittest.TestCase):
    def test_missing_vdn_is_rejected(self):
        index = pd.MultiIndex.from_product([[0.0], [0.0, 1e9]], names=["pwr", "freq"])
        df = pd.DataFrame({"V_UP.net": [3 + 0j, 0 + 0j]}, index=index)
        with mock.patch.object(results, "_dataframe", return_value=df):
            with self.assertRaisesRegex(ValueError, "requires V_DN"):
                results.eta_curve("unused", 100)

    def test_non_power_axis_is_rejected_by_default(self):
        index = pd.MultiIndex.from_product([[25.0], [0.0]], names=["temp", "freq"])
        df = pd.DataFrame({"V_OUT.net": [1 + 0j]}, index=index)
        with mock.patch.object(results, "_dataframe", return_value=df):
            with self.assertRaisesRegex(ValueError, "power axis"):
                results.eta_curve("unused", 100)
            rows = results.eta_curve("unused", 100, power_axis="temp")
        self.assertEqual(len(rows), 1)

    def test_non_finite_load_is_rejected(self):
        with self.assertRaises(ValueError):
            results.eta_curve("unused", float("nan"))


class SweepTests(unittest.TestCase):
    def test_value_tags_are_windows_safe_and_unique(self):
        first = sweep._value_tag("1/2")
        second = sweep._value_tag("1-2")
        self.assertNotEqual(first, second)
        self.assertNotRegex(first, r"[\\/:*?\"<>| ]")

    def test_build_problem_is_reported_as_failed_combination(self):
        spec = {"parts": [{"name": "L1", "params": {}}]}
        errors = []
        with mock.patch("adstk.build.build", return_value={"problems": ["bad param"], "netlist": "x"}):
            rows = sweep.sweep(spec, "L1", "L", ["1"], 100, "out", errors=errors)
        self.assertEqual(rows, [("1", None, None)])
        self.assertEqual(errors, [("1", "bad param")])

    def test_empty_values_fail(self):
        with self.assertRaises(ValueError):
            sweep.sweep({"parts": []}, "L1", "L", [], 100, "out")

    def test_similar_values_do_not_share_output_directory(self):
        spec = {"ads": "ADS", "parts": [{"name": "L1", "params": {}}]}
        outdirs = []

        def fake_sim(_netlist, outdir, _ads, timeout=600):
            outdirs.append(outdir)
            return False, None, "stop"

        with mock.patch("adstk.build.build", return_value={"problems": [], "netlist": "x"}), \
                mock.patch("adstk.sim.run_netlist", side_effect=fake_sim), \
                mock.patch("adstk.core.ads_root", return_value="ADS"):
            sweep.sweep(spec, "L1", "L", ["1/2", "1-2"], 100, "out")
        self.assertEqual(len(outdirs), 2)
        self.assertNotEqual(outdirs[0], outdirs[1])


class _FakeTransaction:
    instances = []

    def __init__(self, _design, _name):
        self.state = "IN_PROGRESS"
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _tb):
        if exc_type is not None and self.state == "IN_PROGRESS":
            self.rollback()

    def commit(self):
        self.state = "COMMITTED"

    def rollback(self):
        self.state = "ROLLED_BACK"


class _FakeDesign:
    def __init__(self):
        self.saved = 0

    def generate_netlist(self):
        return "new netlist"

    def save_design(self):
        self.saved += 1


class _FakeWorkspace:
    def __init__(self, design):
        self._design = design
        self.closed = 0

    def open(self):
        pass

    def design(self, _name, _mode):
        return self._design

    def close(self):
        self.closed += 1


class ReadbackEdgeTests(unittest.TestCase):
    def test_unknown_devices_are_explicitly_not_checked(self):
        netlist = 'OutputPlan:HB1_Output Type="Output"\nMLIN:ML1 N__1 0 W=1 mm L=2 mm\n'
        design = types.SimpleNamespace(instances=[], shapes=[])
        text = readback.report(design, netlist)
        self.assertIn("unknown devices are NOT checked", text)
        self.assertIn("MLIN:ML1", text)


class BuildTests(unittest.TestCase):
    def setUp(self):
        _FakeTransaction.instances = []

    def _run_build(self, dry, param_problems=None, equivalent=True, spec_connectivity=True):
        design = _FakeDesign()
        workspace = _FakeWorkspace(design)
        de = types.ModuleType("keysight.ads.de")
        de.LibraryMode = types.SimpleNamespace(READ_ONLY="read_only")
        de.Library = types.SimpleNamespace(open=lambda *a: None)
        de.Workspace = lambda _path: workspace
        db = types.ModuleType("keysight.ads.de.db")
        db.DesignMode = types.SimpleNamespace(APPEND="append")
        db.Transaction = _FakeTransaction
        keysight = types.ModuleType("keysight")
        ads = types.ModuleType("keysight.ads")
        keysight.ads = ads
        ads.de = de
        de.db = db
        modules = {"keysight": keysight, "keysight.ads": ads,
                   "keysight.ads.de": de, "keysight.ads.de.db": db}
        spec = {"design": {"workspace": "ws", "library": "lib", "cell": "cell"},
                "ads": "ADS",
                "parts": [{"name": "R1", "lib": "l", "cell": "R", "origin": [0, 0],
                           "angle": 0, "params": {}, "master": ("l", "R", "symbol")}],
                "nets": {}, "wires": []}
        with tempfile.TemporaryDirectory() as tmp:
            reference = os.path.join(tmp, "reference.log")
            with open(reference, "w", encoding="utf-8") as fh:
                fh.write("old netlist")
            with mock.patch.dict(sys.modules, modules), \
                    mock.patch("adstk.core.setup_env"), \
                    mock.patch("adstk.core.lib_paths", return_value={}), \
                    mock.patch("adstk.core.assert_no_lock"), \
                    mock.patch("adstk.core.sch_path", return_value=os.path.join(tmp, "missing.oa")), \
                    mock.patch("adstk.core.discover_offsets", return_value={("l", "R", "symbol"): {}}), \
                    mock.patch("adstk.core.predict_pins", return_value={}), \
                    mock.patch("adstk.build.auditmod.audit", return_value=([], [])), \
                    mock.patch("adstk.build._place", return_value=[]), \
                    mock.patch("adstk.build._params", return_value=param_problems or []), \
                    mock.patch("adstk.build._overlap", return_value=[]), \
                    mock.patch("adstk.netlistx.compare_spec_connectivity", return_value=spec_connectivity), \
                    mock.patch("adstk.netlistx.compare_netlists", return_value=equivalent), \
                    mock.patch("adstk.netlistx.compare_controllers", return_value=True):
                result = build.build(spec, dry=dry, reference=reference, verbose=False)
        return result, design, workspace

    def test_apply_reference_mismatch_does_not_save(self):
        result, design, workspace = self._run_build(dry=False, equivalent=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["saved"])
        self.assertEqual(design.saved, 0)
        self.assertEqual(workspace.closed, 1)

    def test_apply_spec_connectivity_mismatch_does_not_save(self):
        result, design, _ = self._run_build(dry=False, spec_connectivity=False)
        self.assertFalse(result["saved"])
        self.assertEqual(design.saved, 0)
        self.assertIn("generated netlist connectivity differs from spec", result["problems"])

    def test_apply_param_problem_rolls_back_and_does_not_save(self):
        result, design, _ = self._run_build(dry=False, param_problems=["bad param"])
        self.assertEqual(result["problems"], ["bad param"])
        self.assertEqual(design.saved, 0)
        self.assertEqual([t.state for t in _FakeTransaction.instances],
                         ["ROLLED_BACK", "ROLLED_BACK"])

    def test_apply_success_saves_once_after_commit(self):
        result, design, _ = self._run_build(dry=False, equivalent=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["saved"])
        self.assertEqual(design.saved, 1)
        self.assertEqual([t.state for t in _FakeTransaction.instances],
                         ["ROLLED_BACK", "COMMITTED"])

    def test_dry_success_never_saves(self):
        result, design, _ = self._run_build(dry=True, equivalent=True)
        self.assertTrue(result["ok"])
        self.assertEqual(design.saved, 0)


class ReviewRegressionTests(unittest.TestCase):
    """Regressions for the 2026-10 independent review findings."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_ground_island_split_signal_net_backstop(self):
        # Intent: P1 -> R1 -> R2 -> RL, but n_out (between R1 and R2) is drawn
        # as two islands, each with its own GROUND. The geometric audit allows
        # grounded islands, so it passes; the spec/netlist connectivity gate
        # must reject it (all pins merge onto node 0 in the real netlist).
        nets = {
            "ngnd":  [("P1", "P__0"), ("RL", "P__1"), ("GND3", "P__0")],
            "nsrc":  [("P1", "P__1"), ("R1", "P__0")],
            "n_out": [("R1", "P__1"), ("R2", "P__0"), ("GND1", "P__0"), ("GND2", "P__0")],
            "nload": [("R2", "P__1"), ("RL", "P__0")],
        }
        wires = [("ngnd", [(0, 0), (1, 0)]), ("nsrc", [(2, 0), (3, 0)]),
                 ("n_out", [(4, 0), (5, 0)]), ("n_out", [(8, 0), (9, 0)]),
                 ("nload", [(10, 0), (11, 0)])]
        pins = {("P1", "P__0"): (0, 0), ("RL", "P__1"): (1, 0), ("GND3", "P__0"): (0.5, 0),
                ("P1", "P__1"): (2, 0), ("R1", "P__0"): (3, 0),
                ("R1", "P__1"): (4, 0), ("GND1", "P__0"): (5, 0),
                ("R2", "P__0"): (9, 0), ("GND2", "P__0"): (8, 0),
                ("R2", "P__1"): (10, 0), ("RL", "P__0"): (11, 0)}
        grounds = {("GND1", "P__0"), ("GND2", "P__0"), ("GND3", "P__0")}
        problems, _ = audit.audit(nets, wires, pins, ground_refs=grounds)
        self.assertEqual(problems, [])

        p_master = ("ads_sources", "P_1Tone", "symbol")
        r_master = ("ads_rflib", "R", "symbol")
        g_master = ("ads_rflib", "GROUND", "symbol")
        spec = {"parts": [
                {"name": "P1", "cell": "P_1Tone", "master": p_master},
                {"name": "R1", "cell": "R", "master": r_master},
                {"name": "R2", "cell": "R", "master": r_master},
                {"name": "RL", "cell": "R", "master": r_master},
                {"name": "GND1", "cell": "GROUND", "master": g_master},
                {"name": "GND2", "cell": "GROUND", "master": g_master},
                {"name": "GND3", "cell": "GROUND", "master": g_master}],
                "nets": {k: [list(r) for r in v] for k, v in nets.items()}}
        pin_indices = {p_master: {"P__0": 0, "P__1": 1}, r_master: {"P__0": 0, "P__1": 1}}
        # every grounded pin lands on node 0: R1.P__1, R2.P__0, RL.P__1, P1.P__0
        netlist = ("R:R1 1 0 R=50 Ohm\nR:R2 0 2 R=50 Ohm\nR:RL 2 0 R=50 Ohm\n"
                   "Port:P1 0 1 Num=1 Z=50\n")
        self.assertFalse(netlistx.compare_spec_connectivity(
            spec, pin_indices, netlist, log=lambda *a: None))

    def test_auto_plan_normalization_is_narrow(self):
        auto_plan = "SweepPlan: HB1_stim Start=1 Stop=10 Step=1\n"
        auto_hb = 'HB:HB1 Freq[1]=1 GHz SweepVar="" SweepPlan="HB1_stim"\n'
        real_plan_hb = 'HB:HB1 Freq[1]=1 GHz SweepVar="pwr" SweepPlan="HB1_stim"\n'
        real_plan = "SweepPlan:HB1_stim Start=1 Stop=20 Step=1\n"
        quiet = lambda *a: None  # noqa: E731
        # a purely auto-generated plan that disappears is normalised away
        self.assertTrue(netlistx.equivalent(auto_hb + auto_plan, auto_hb, log=quiet))
        # a real sweep plan disappearing is NOT equivalent
        self.assertFalse(netlistx.equivalent(real_plan_hb + real_plan, real_plan_hb, log=quiet))
        # exact collision with the auto-plan signature: a real plan named
        # HB1_stim Start=1 Stop=10 must still count when SweepVar is non-empty
        exact_real = "SweepPlan: HB1_stim Start=1 Stop=10 Step=1\n"
        self.assertFalse(netlistx.equivalent(real_plan_hb + exact_real, real_plan_hb, log=quiet))
        # a real SweepVar reference disappearing is NOT equivalent
        self.assertFalse(netlistx.equivalent(real_plan_hb + auto_plan, auto_hb + auto_plan,
                                             log=quiet))

    def test_sweep_uses_fixed_rl_for_every_point(self):
        spec = {"ads": "ADS", "parts": [{"name": "RL", "params": {}}]}
        seen_rl = []

        def fake_eta(_ds, rl, **_kw):
            seen_rl.append(rl)
            return [(0.0, 1.0, 10.0)]

        with mock.patch("adstk.build.build", return_value={"problems": [], "netlist": "x"}), \
                mock.patch("adstk.sim.run_netlist", return_value=(True, "ds", "")), \
                mock.patch("adstk.core.ads_root", return_value="ADS"), \
                mock.patch("adstk.results.eta_curve", side_effect=fake_eta):
            rows = sweep.sweep(spec, "RL", "R", ["200", "1000"], 550, "out")
        # documented semantics: one fixed rl for all points, so sweeping the
        # load resistance itself would silently give wrong PCE values
        self.assertEqual(seen_rl, [550, 550])
        self.assertEqual([r[0] for r in rows], ["200", "1000"])

    def test_load_sweep_uses_each_load_as_pce_rl(self):
        spec = {"ads": "ADS", "parts": [{"name": "R1", "params": {}}]}
        seen = []

        def fake_eta(_ds, rl, **_kw):
            seen.append(rl)
            return [(21.0, 2.0, 70.0)]

        with mock.patch("adstk.build.build", return_value={"problems": [], "netlist": "x"}), \
                mock.patch("adstk.sim.run_netlist", return_value=(True, "ds", "")), \
                mock.patch("adstk.core.ads_root", return_value="ADS"), \
                mock.patch("adstk.results.eta_curve", side_effect=fake_eta):
            rows = sweep.load_sweep(spec, "R1", "R", ["50", "75"], "out", pwr=21)
        self.assertEqual(seen, [50.0, 75.0])
        self.assertEqual([(r[0], r[2], r[3]) for r in rows],
                         [("50", 70.0, 21.0), ("75", 70.0, 21.0)])

    def test_extra_device_pin_fails_spec_connectivity(self):
        r_master = ("ads_rflib", "R", "symbol")
        spec = {"parts": [{"name": "R1", "cell": "R", "master": r_master},
                          {"name": "R2", "cell": "R", "master": r_master}],
                "nets": {"a": [["R1", "P__0"], ["R2", "P__0"]],
                         "b": [["R1", "P__1"], ["R2", "P__1"]]}}
        pin_indices = {r_master: {"P__0": 0, "P__1": 1}}
        good = "R:R1 N1 N2\nR:R2 N1 N2\n"
        self.assertTrue(netlistx.compare_spec_connectivity(
            spec, pin_indices, good, log=lambda *a: None))
        # an extra capacitor on R2's net adds pins the spec does not know
        bad = "R:R1 N1 N2\nR:R2 N1 N3\nC:C1 N3 0\n"
        self.assertFalse(netlistx.compare_spec_connectivity(
            spec, pin_indices, bad, log=lambda *a: None))

    def test_cli_reports_friendly_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = os.path.join(tmp, "spec.json")
            with open(spec_path, "w", encoding="utf-8") as fh:
                json.dump({"design": {"workspace": "ws", "library": "lib", "cell": "cell"},
                           "parts": [{"name": "R1", "lib": "l", "cell": "R", "origin": [0, 0]}],
                           "nets": {}, "wires": []}, fh)
            # a lock error (RuntimeError) must print ERROR, not a traceback
            with mock.patch("adstk.build.build", side_effect=RuntimeError("ADS GUI holds a lock")):
                self.assertEqual(main(["build", spec_path]), 2)
        # layout into a not-yet-existing output directory creates it instead of crashing
        plan = os.path.join(self.ROOT, "examples", "specs", "demo_layout.plan.txt")
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "new_dir", "out.txt")
            self.assertEqual(main(["layout", plan, output]), 0)
            self.assertTrue(os.path.isfile(output))


if __name__ == "__main__":
    unittest.main()
