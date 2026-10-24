"""Fixed Chinese text spec v1, convertible to the JSON spec dictionary."""
import os

SECTIONS = ("工程", "变量", "器件", "网络", "连线", "选项")
DESIGN_KEYS = {"工作区": "workspace", "库": "library", "单元": "cell"}
OPTION_KEYS = {"tahb_off", "var_part", "varblock", "power_axis", "器件类型"}


def _fail(line_no, message):
    raise ValueError(f"第 {line_no} 行: {message}")


def _assignment(text, line_no):
    if "=" not in text:
        _fail(line_no, "应为 '名称 = 值'")
    key, value = (part.strip() for part in text.split("=", 1))
    if not key or not value:
        _fail(line_no, "等号两侧都不能为空")
    return key, value


def _point(text, line_no):
    text = text.strip()
    if not (text.startswith("(") and text.endswith(")")):
        _fail(line_no, f"坐标应为 (x, y)，得到 {text!r}")
    fields = text[1:-1].split(",")
    if len(fields) != 2:
        _fail(line_no, f"坐标必须恰好有两个数，得到 {text!r}")
    try:
        return [float(fields[0]), float(fields[1])]
    except ValueError:
        _fail(line_no, f"坐标包含非数字内容: {text!r}")


def _boolean(text, line_no):
    if text == "true":
        return True
    if text == "false":
        return False
    _fail(line_no, "布尔值只能写 true 或 false")


def parse(text):
    """Parse text spec v1. Errors include the source line number."""
    spec = {"design": {}, "parts": [], "nets": {}, "wires": [],
            "vars": {}, "var_part": None, "tahb_off": False}
    section = None
    seen_sections = set()
    seen_keys = set()
    part_names = set()
    net_lines = {}
    wire_lines = []
    option_lines = {}
    version = False

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section not in SECTIONS:
                _fail(line_no, f"未知区段 [{section}]")
            if section in seen_sections:
                _fail(line_no, f"区段 [{section}] 重复")
            seen_sections.add(section)
            seen_keys = set()
            continue
        if section is None:
            key, value = _assignment(line, line_no)
            if key != "版本":
                _fail(line_no, "文件开头只能写 版本 = 1")
            if version:
                _fail(line_no, "版本重复")
            if value != "1":
                _fail(line_no, f"不支持的版本 {value!r}，目前仅支持 1")
            version = True
            continue

        if section == "器件":
            fields = [field.strip() for field in line.split("|")]
            if len(fields) != 6:
                _fail(line_no, "器件行必须为: 名称 | 库 | 单元 | (x,y) | 角度 | 参数")
            name, lib, cell, origin_text, angle_text, param_text = fields
            if not name or not lib or not cell:
                _fail(line_no, "器件名称、库和单元都不能为空")
            if name in part_names:
                _fail(line_no, f"器件 {name} 重复")
            part_names.add(name)
            origin = _point(origin_text, line_no)
            try:
                angle = int(angle_text)
            except ValueError:
                _fail(line_no, f"角度不是整数: {angle_text!r}")
            if angle not in (0, 90, 180, 270):
                _fail(line_no, "角度只能是 0/90/180/270")
            params = {}
            if param_text:
                for item in param_text.split(";"):
                    key, value = _assignment(item, line_no)
                    if key in params:
                        _fail(line_no, f"器件 {name} 的参数 {key} 重复")
                    params[key] = value
            spec["parts"].append({"name": name, "lib": lib, "cell": cell,
                                  "origin": origin, "angle": angle, "params": params})
            continue

        key, value = _assignment(line, line_no)
        if section == "工程":
            if key == "ADS路径":
                if "ads" in spec:
                    _fail(line_no, "ADS路径 重复")
                spec["ads"] = value
            elif key in DESIGN_KEYS:
                if key in seen_keys:
                    _fail(line_no, f"工程字段 {key} 重复")
                spec["design"][DESIGN_KEYS[key]] = value
            else:
                _fail(line_no, f"未知工程字段 {key}")
        elif section == "变量":
            if key in spec["vars"]:
                _fail(line_no, f"变量 {key} 重复")
            spec["vars"][key] = value
        elif section == "网络":
            if key in spec["nets"]:
                _fail(line_no, f"网络 {key} 重复")
            refs = []
            for item in value.split(","):
                ref = item.strip()
                if "." not in ref:
                    _fail(line_no, f"引脚引用应为 器件.引脚，得到 {ref!r}")
                part, pin = (part.strip() for part in ref.split(".", 1))
                if not part or not pin:
                    _fail(line_no, f"无效引脚引用 {ref!r}")
                refs.append([part, pin])
            if not refs:
                _fail(line_no, f"网络 {key} 没有引脚")
            spec["nets"][key] = refs
            net_lines[key] = line_no
        elif section == "连线":
            points = [_point(item, line_no) for item in value.split("->")]
            if len(points) < 2:
                _fail(line_no, "每条连线至少需要两个坐标")
            spec["wires"].append({"net": key, "points": points})
            wire_lines.append(line_no)
        elif section == "选项":
            if key not in OPTION_KEYS:
                _fail(line_no, f"未知选项 {key}")
            if key in seen_keys:
                _fail(line_no, f"选项 {key} 重复")
            if key == "tahb_off":
                spec["tahb_off"] = _boolean(value, line_no)
            elif key == "var_part":
                spec["var_part"] = value
            elif key == "varblock":
                spec["varblock"] = value
            elif key == "power_axis":
                spec["power_axis"] = value
            else:
                values = [item.strip() for item in value.split(",") if item.strip()]
                if not values:
                    _fail(line_no, "器件类型不能为空")
                spec["device_types"] = values
            option_lines[key] = line_no
        seen_keys.add(key)

    if not version:
        raise ValueError("缺少文件头: 版本 = 1")
    missing = [name for name in SECTIONS if name not in seen_sections]
    if missing:
        raise ValueError(f"缺少区段: {', '.join(missing)}")
    for key in ("workspace", "library", "cell"):
        if key not in spec["design"]:
            raise ValueError(f"[工程] 缺少字段: {key}")
    if not spec["parts"]:
        raise ValueError("[器件] 不能为空")
    if not spec["nets"]:
        raise ValueError("[网络] 不能为空")
    if not spec["wires"]:
        raise ValueError("[连线] 不能为空")
    for net, refs in spec["nets"].items():
        for part, _pin in refs:
            if part not in part_names:
                _fail(net_lines[net], f"网络 {net} 引用了不存在的器件 {part}")
    for i, wire in enumerate(spec["wires"]):
        if wire["net"] not in spec["nets"]:
            _fail(wire_lines[i], f"连线引用了未定义网络 {wire['net']}")
    if spec.get("var_part"):
        var_names = {p["name"] for p in spec["parts"] if p["cell"] == "VAR"}
        if spec["var_part"] not in var_names:
            _fail(option_lines["var_part"], f"var_part {spec['var_part']} 不是 VAR 实例")
    return spec


def _number(value):
    return repr(float(value))


def _point_text(point):
    return f"({_number(point[0])}, {_number(point[1])})"


def _portable_path(path, base):
    if base and os.path.isabs(path):
        try:
            return os.path.relpath(path, base).replace(os.sep, "/")
        except ValueError:
            pass
    return path


def _canonical(spec):
    parts = []
    for part in spec["parts"]:
        params = {str(k): str(v) for k, v in part.get("params", {}).items()
                  if k and str(v).strip()}
        parts.append({"name": part["name"], "lib": part["lib"], "cell": part["cell"],
                      "origin": [float(v) for v in part["origin"]],
                      "angle": int(part.get("angle", 0)), "params": params})
    return {
        "library": spec["design"]["library"], "cell": spec["design"]["cell"],
        "parts": parts,
        "nets": {net: [list(ref) for ref in refs] for net, refs in spec["nets"].items()},
        "wires": [{"net": w["net"], "points": [[float(v) for v in pt] for pt in w["points"]]}
                  for w in spec["wires"]],
        "vars": {str(k): str(v) for k, v in spec.get("vars", {}).items()},
        "var_part": spec.get("var_part"),
        "tahb_off": bool(spec.get("tahb_off")),
        "varblock": spec.get("varblock"),
        "power_axis": spec.get("power_axis"),
        "device_types": list(spec["device_types"]) if spec.get("device_types") else None,
    }


def dumps(spec, base=None):
    """Convert a spec dictionary to text v1; base makes paths relative."""
    if spec.get("libs"):
        raise ValueError("文本 v1 暂不支持 libs；请使用 JSON spec 或先移入库路径配置")
    lines = ["版本 = 1", "", "[工程]"]
    design = spec["design"]
    lines.append(f"工作区 = {_portable_path(design['workspace'], base)}")
    lines.append(f"库 = {design['library']}")
    lines.append(f"单元 = {design['cell']}")
    if spec.get("ads"):
        lines.append(f"ADS路径 = {_portable_path(spec['ads'], base)}")
    lines.extend(["", "[变量]"])
    for key, value in spec.get("vars", {}).items():
        lines.append(f"{key} = {value}")
    lines.extend(["", "[器件]"])
    for part in spec["parts"]:
        if part.get("view", "symbol") != "symbol":
            raise ValueError(f"文本 v1 仅支持 symbol view: {part['name']}")
        params = "; ".join(f"{key}={value}" for key, value in part.get("params", {}).items()
                           if key and str(value).strip())
        lines.append(" | ".join([part["name"], part["lib"], part["cell"],
                                  _point_text(part["origin"]), str(part.get("angle", 0)), params]))
    lines.extend(["", "[网络]"])
    for net, refs in spec["nets"].items():
        lines.append(f"{net} = " + ", ".join(f"{part}.{pin}" for part, pin in refs))
    lines.extend(["", "[连线]"])
    for wire in spec["wires"]:
        points = " -> ".join(_point_text(point) for point in wire["points"])
        lines.append(f"{wire['net']} = {points}")
    lines.extend(["", "[选项]"])
    lines.append(f"tahb_off = {'true' if spec.get('tahb_off') else 'false'}")
    if spec.get("var_part"):
        lines.append(f"var_part = {spec['var_part']}")
    if spec.get("varblock"):
        lines.append(f"varblock = {spec['varblock']}")
    if spec.get("power_axis"):
        lines.append(f"power_axis = {spec['power_axis']}")
    if spec.get("device_types"):
        lines.append("器件类型 = " + ", ".join(spec["device_types"]))
    text = "\n".join(lines) + "\n"
    reparsed = parse(text)
    if _canonical(spec) != _canonical(reparsed):
        raise ValueError("文本 v1 无法无损表示该 spec；请检查 ;、|、换行等保留字符")
    return text
