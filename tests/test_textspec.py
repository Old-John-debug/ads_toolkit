import json
import os
import tempfile
import unittest

from adstk import build, textspec
from adstk.__main__ import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "specs", "demo_circuit.txt")

MINIMAL = """版本 = 1

[工程]
工作区 = ws
库 = lib
单元 = cell

[变量]
pwr = 3

[器件]
R1 | ads_rflib | R | (0, 0) | 0 | R=50 Ohm
GND1 | ads_rflib | GROUND | (1, 0) | 0 |

[网络]
n = R1.P__0, R1.P__1, GND1.P__0

[连线]
n = (0, 0) -> (1, 0)

[选项]
tahb_off = true
"""


class TextSpecTests(unittest.TestCase):
    def test_minimal_text_parses(self):
        spec = textspec.parse(MINIMAL)
        self.assertEqual(spec["design"], {"workspace": "ws", "library": "lib", "cell": "cell"})
        self.assertEqual(spec["parts"][0]["params"], {"R": "50 Ohm"})
        self.assertEqual(spec["nets"]["n"], [["R1", "P__0"], ["R1", "P__1"], ["GND1", "P__0"]])
        self.assertTrue(spec["tahb_off"])

    def test_complete_example_loads(self):
        spec = build.load_spec(EXAMPLE)
        self.assertEqual(len(spec["parts"]), 10)
        self.assertEqual(len(spec["nets"]), 3)
        self.assertEqual(len(spec["wires"]), 8)
        self.assertEqual(spec["vars"], {"pwr": "0"})
        self.assertEqual(spec["var_part"], "VAR1")
        self.assertTrue(os.path.isabs(spec["design"]["workspace"]))
        self.assertTrue(spec["design"]["workspace"].endswith(os.path.join("examples", "workspace_demo")))

    def test_complete_example_round_trip(self):
        loaded = build.load_spec(EXAMPLE)
        text = textspec.dumps(loaded, base=os.path.dirname(EXAMPLE))
        reparsed = textspec.parse(text)
        self.assertEqual(reparsed["design"]["workspace"], "../workspace_demo")
        self.assertEqual(reparsed["nets"], {net: [list(ref) for ref in refs]
                                             for net, refs in loaded["nets"].items()})
        self.assertEqual(reparsed["wires"], loaded["wires"])
        self.assertEqual(len(reparsed["parts"]), len(loaded["parts"]))

    def test_convert_command_text_to_json_omits_internal_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "spec.json")
            self.assertEqual(main(["convert", EXAMPLE, output]), 0)
            with open(output, encoding="utf-8") as fh:
                converted = json.load(fh)
            self.assertNotIn("master", converted["parts"][0])
            self.assertIsInstance(converted["nets"]["gnd"][0], list)
            self.assertEqual(build.load_spec(output)["design"]["cell"], "demo_network")

    def test_format_errors_have_line_numbers(self):
        cases = {
            "missing version": MINIMAL.replace("版本 = 1\n", ""),
            "bad angle": MINIMAL.replace("| 0 | R=50 Ohm", "| 45 | R=50 Ohm"),
            "bad coordinate": MINIMAL.replace("(0, 0) | 0 | R=50 Ohm", "(0; 0) | 0 | R=50 Ohm"),
            "unknown section": MINIMAL.replace("[变量]", "[未知]"),
            "bad boolean": MINIMAL.replace("tahb_off = true", "tahb_off = yes"),
            "duplicate part": MINIMAL.replace("[网络]", "R1 | ads_rflib | R | (0, 0) | 0 | R=50 Ohm\n\n[网络]"),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "版本|第 [0-9]+ 行"):
                    textspec.parse(text)

    def test_lossy_reserved_characters_are_rejected(self):
        spec = {"design": {"workspace": "ws", "library": "l", "cell": "c"},
                "parts": [{"name": "R1", "lib": "ads_rflib", "cell": "R",
                           "origin": [0, 0], "angle": 0, "params": {"Note": "1;X=2"}}],
                "nets": {"n": [["R1", "P__0"], ["R1", "P__1"]]},
                "wires": [{"net": "n", "points": [[0, 0], [1, 0]]}], "vars": {}}
        with self.assertRaisesRegex(ValueError, "无损"):
            textspec.dumps(spec)

    def test_unknown_part_has_source_line(self):
        with self.assertRaisesRegex(ValueError, "第 [0-9]+ 行.*不存在的器件"):
            textspec.parse(MINIMAL.replace("R1.P__0", "NO_SUCH.P__0"))

    def test_power_axis_round_trip(self):
        spec = textspec.parse(MINIMAL)
        spec["power_axis"] = "input_power"
        reparsed = textspec.parse(textspec.dumps(spec))
        self.assertEqual(reparsed["power_axis"], "input_power")

    def test_convert_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "spec.json")
            self.assertEqual(main(["convert", EXAMPLE, output]), 0)
            self.assertFalse([name for name in os.listdir(tmp) if name.startswith(".adstk_")])

    def test_empty_sweep_cli_fails(self):
        rc = main(["sweep", EXAMPLE, "--part", "L2", "--param", "L",
                   "--values", "", "--rl", "2000"])
        self.assertEqual(rc, 2)

    def test_pin_table_is_valid_and_covers_example_devices(self):
        path = os.path.join(ROOT, "templates", "device_pin_table.json")
        with open(path, encoding="utf-8") as fh:
            table = json.load(fh)
        masters = {(d["library"], d["cell"]) for d in table["devices"]}
        spec = build.load_spec(EXAMPLE)
        used = {(p["lib"], p["cell"]) for p in spec["parts"]}
        self.assertTrue(used.issubset(masters))


if __name__ == "__main__":
    unittest.main()
