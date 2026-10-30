"""Simplified layout-plan text parser. Produces spec data without geometry."""
import os

from .core import resolve_path
from .textspec import DESIGN_KEYS, OPTION_KEYS, _assignment, _boolean, _pin_refs

SECTIONS = ("工程", "库路径", "变量", "器件", "网络", "布局", "选项")
REQUIRED_SECTIONS = ("工程", "器件", "网络", "布局")
LAYOUT_KEYS = ("主链", "对地", "探针", "控制器", "地网络")


def _names(value, line_no):
    if value == "无":
        return []
    names = [item.strip() for item in value.split(",")]
    if any(not name for name in names):
        raise ValueError(f"第 {line_no} 行: 列表中有空名称")
    return names


def parse(text, base_dir=None):
    """-> (spec, layout). Paths are resolved relative to base_dir."""
    spec = {"design": {}, "libs": {}, "parts": [], "nets": {}, "vars": {},
            "var_part": None, "tahb_off": False}
    layout = {"主链": [], "对地": [], "探针": [], "控制器": [], "地网络": None}
    section = None
    seen_sections = set()
    seen_keys = set()
    part_names = set()
    net_lines = {}
    option_lines = {}
    version = False

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section not in SECTIONS:
                raise ValueError(f"第 {line_no} 行: 未知区段 [{section}]")
            if section in seen_sections:
                raise ValueError(f"第 {line_no} 行: 区段 [{section}] 重复")
            seen_sections.add(section)
            seen_keys = set()
            continue
        if section is None:
            key, value = _assignment(line, line_no)
            if key != "版本" or value != "1" or version:
                raise ValueError(f"第 {line_no} 行: 文件头必须是唯一的 版本 = 1")
            version = True
            continue

        if section == "器件":
            fields = [field.strip() for field in line.split("|")]
            if len(fields) != 4:
                raise ValueError(f"第 {line_no} 行: 布局计划器件行必须为 名称 | 库 | 单元 | 参数")
            name, lib, cell, param_text = fields
            if not name or not lib or not cell:
                raise ValueError(f"第 {line_no} 行: 器件名称、库和单元不能为空")
            if name in part_names:
                raise ValueError(f"第 {line_no} 行: 器件 {name} 重复")
            part_names.add(name)
            params = {}
            if param_text:
                for item in param_text.split(";"):
                    key, value = _assignment(item, line_no)
                    if key in params:
                        raise ValueError(f"第 {line_no} 行: 参数 {key} 重复")
                    params[key] = value
            spec["parts"].append({"name": name, "lib": lib, "cell": cell, "params": params})
            continue

        key, value = _assignment(line, line_no)
        if key in seen_keys:
            raise ValueError(f"第 {line_no} 行: 字段 {key} 重复")
        if section == "工程":
            if key == "ADS路径":
                spec["ads"] = value
            elif key in DESIGN_KEYS:
                spec["design"][DESIGN_KEYS[key]] = value
            else:
                raise ValueError(f"第 {line_no} 行: 未知工程字段 {key}")
        elif section == "库路径":
            spec["libs"][key] = value
        elif section == "变量":
            spec["vars"][key] = value
        elif section == "网络":
            spec["nets"][key] = _pin_refs(value, line_no)
            net_lines[key] = line_no
        elif section == "布局":
            if key not in LAYOUT_KEYS:
                raise ValueError(f"第 {line_no} 行: 未知布局字段 {key}")
            if key == "地网络":
                layout[key] = value
            else:
                layout[key] = _names(value, line_no)
        elif section == "选项":
            if key not in OPTION_KEYS:
                raise ValueError(f"第 {line_no} 行: 未知选项 {key}")
            if key == "tahb_off":
                spec["tahb_off"] = _boolean(value, line_no)
            elif key == "var_part":
                spec["var_part"] = value
            elif key == "varblock":
                spec["varblock"] = value
            elif key == "power_axis":
                spec["power_axis"] = value
            else:
                spec["device_types"] = [x.strip() for x in value.split(",") if x.strip()]
            option_lines[key] = line_no
        seen_keys.add(key)

    if not version:
        raise ValueError("缺少文件头: 版本 = 1")
    missing = [name for name in REQUIRED_SECTIONS if name not in seen_sections]
    if missing:
        raise ValueError(f"缺少区段: {', '.join(missing)}")
    for key in ("workspace", "library", "cell"):
        if key not in spec["design"]:
            raise ValueError(f"[工程] 缺少字段: {key}")
    if not spec["parts"]:
        raise ValueError("[器件] 不能为空")
    if not spec["nets"]:
        raise ValueError("[网络] 不能为空")
    if not layout["主链"]:
        raise ValueError("[布局] 主链 不能为空")
    for net, refs in spec["nets"].items():
        for part, _pin in refs:
            if part not in part_names:
                raise ValueError(f"第 {net_lines[net]} 行: 网络 {net} 引用了不存在的器件 {part}")
    if spec.get("var_part"):
        var_names = {p["name"] for p in spec["parts"] if p["cell"] == "VAR"}
        if spec["var_part"] not in var_names:
            raise ValueError(f"第 {option_lines['var_part']} 行: var_part 不是 VAR 实例")

    base = os.path.abspath(base_dir or os.getcwd())
    spec["design"]["workspace"] = resolve_path(spec["design"]["workspace"], base)
    if spec.get("ads"):
        spec["ads"] = resolve_path(spec["ads"], base)
    for name, path in spec["libs"].items():
        spec["libs"][name] = resolve_path(path, base)
    return spec, layout
