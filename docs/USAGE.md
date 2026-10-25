# ads_toolkit 使用说明

本文是完整操作手册；项目概览和文件分类见根目录 `README.md`。

## 1. 适用环境

- 目前只在 **ADS 2025 Update 2 + Windows** 实测，其他 ADS 版本和其他电脑尚未验证。
- 使用 ADS 自带 Python：`<ADS>\tools\python\python.exe`。
- 在工具包根目录运行 `python -B -m adstk ...`，不要在 `adstk/` 子目录里运行。
- ADS 根目录默认从当前 Python 路径推断，也可以设置 `ADS_PATH`，或在 spec 里写 `ads`。
- PCE 读取依赖 ADS 自带 `keysight.edatoolbox`；只有导出 PNG 才需要 matplotlib。

先跑一次自检：

```bat
cd /d <toolkit 根目录>
"<ADS>\tools\python\python.exe" -B -m unittest discover -s tests -v
```

## 2. 文件分类

| 位置 | 用途 |
|---|---|
| `adstk/` | 可执行内核：建模、审计、网表、仿真、PCE、文本 spec、布局 |
| `tools/` | 从已有设计生成 spec 的辅助工具 |
| `templates/` | 文本骨架、通用引脚表、布局计划骨架、最小设计配置 |
| `examples/specs/` | 不含论文数值的合成 demo（plan 与 v1） |
| `tests/` | 单元测试和回归反例 |
| `docs/` | 使用手册、布局计划、AI 提示词 |

论文专属的示例、器件引脚表、证据和结果应放在各自项目目录，不进入本仓库。

## 3. 三个核心概念

### spec

spec 描述一个原理图：

- `design`：工作区、库、单元。
- `parts`：器件名称、库、单元、坐标、角度、参数。
- `nets`：网络连接了哪些“器件.引脚”。
- `wires`：每个网络的正交折线坐标。
- `vars` / `var_part`：VAR 变量。
- `tahb_off`：命令行 HB 常用。
- `varblock` / `power_axis`：数据集读取选项。

支持 JSON 和固定中文文本两种格式，详见第 6 节。

### dry 与 apply

- `build` 默认 **dry**：在内存中建模、审计、生成网表并对连接，不保存设计。
- `build --apply`：只有所有检查通过才保存；“参考网表”是可选的额外校验。
- dry 仍可能让 ADS 更新工作区元数据。要求物理零写入时，用工作区副本。

### reference

参考网表是“期望电路”的文本。重排或重建时，用 `--reference` 比较器件、节点顺序、参数、控制行以及其余网表内容。没有 reference 时，工具仍会检查几何和 spec 与生成网表的一致性，但不能替代人工确认电路原理。

## 4. 路线一：从已保存的原理图开始

### 4.1 准备最小设计配置

复制 `templates/design_only.json`，填写：

```json
{
 "ads": null,
 "libs": {},
 "design": {
  "workspace": "<工作区绝对路径>",
  "library": "<库名>",
  "cell": "<单元名>"
 }
}
```

这个最小文件只用于 `readback` 和 `tools/make_spec_from_design.py`。`build/sim/sweep` 仍需要完整 spec。

### 4.2 读回

先保存并关闭 ADS 设计，然后：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk readback my_design.json --netlist-out my_netlist.log
```

报告包含实例、引脚、连线、每网线段数和单脚网络。遇到未识别器件时会明确提示“未检查”，不会直接宣称无悬空。

### 4.3 仿真已保存设计

```bat
"<ADS>\tools\python\python.exe" -B -m adstk sim my_spec.json --rl 550 --csv eta.csv
```

不加 `--netlist` 时，会从保存的设计生成网表，再用 `hpeesofsim` 运行。输出目录默认在 spec 同级的 `sim_out/`。

## 5. 路线二：从 spec 建原理图

### 5.1 dry 检查

```bat
"<ADS>\tools\python\python.exe" -B -m adstk build my_spec.json --reference expected.log --netlist-out built.log
```

建议每次先带 `--netlist-out`，检查生成的网表。失败时先修正 problems，不要直接 `--apply`。

### 5.2 保存

确认 dry 通过后：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk build my_spec.json --apply --reference expected.log
```

工具会：

1. 检查 `.cdslck`。
2. 备份已有 `sch.oa`。
3. 在单个事务内清空、放置、写参数、连线。
4. 检查几何。
5. 生成网表并核对 spec 连接关系。
6. 可选比较参考网表。
7. 全部通过才保存。

失败会显示 `(not saved)` 并返回非零。

### 5.3 仿真与扫参

```bat
"<ADS>\tools\python\python.exe" -B -m adstk sim my_spec.json --rl 550 --csv eta.csv
"<ADS>\tools\python\python.exe" -B -m adstk sweep my_spec.json --part L2 --param L --values "22 nH,30 nH,36 nH" --rl 2000
"<ADS>\tools\python\python.exe" -B -m adstk load-sweep my_spec.json --part R1 --param R --values "30,35,40,45,50,55,60,65,70" --pwr 21
```

`sim` 会删除输出目录中本次顶层单元的旧 `.ds`，防止把上次结果当成本次结果。`sweep` 每个组合一个独立目录，失败组合显示原因并使命令返回非零。

**`sweep` 的 `--rl` 对所有扫点固定**：它只改变被扫器件参数，不改变 PCE 公式里的负载。扫负载电阻请用 `load-sweep`；它会让每个负载点用自身阻值作为 RL 计算 PCE。`sweep` 只用于匹配电感/电容等非负载参数。

### 5.4 不写坐标：简化布局计划

如果不想手算坐标和连线，可以使用 `.plan.txt`：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk layout my_layout.plan.txt my_spec.txt
```

布局计划与 v1 的区别：

- `[器件]` 不写坐标和角度，只写 `名称 | 库 | 单元 | 参数`。
- 不写 `[连线]`。
- 新增 `[布局]`，按 `主链 / 对地 / 探针 / 控制器` 给器件分工。
- GROUND 器件自动放到地总线，不列入布局分类。

生成结果是完整 v1 文本，仍要跑 `build` dry、连接核对和参考网表比较。完整规则与限制见 `docs/LAYOUT_PLAN.md`。

## 6. 固定中文文本与 AI 编写

### 6.1 格式

固定六段，另可选 `[库路径]`：

```text
版本 = 1
[工程] 工作区 / 库 / 单元，可选 ADS路径
[库路径] 可选；库名 = 路径，用于厂商/项目库
[变量] 变量 = 值
[器件] 名称 | 库 | 单元 | (x,y) | 角度 | 参数1=值; 参数2=值
[网络] 网络名 = 器件.引脚, 器件.引脚
[连线] 网络名 = (x1,y1) -> (x2,y2)
[选项] tahb_off / var_part / varblock / power_axis / 器件类型
```

布局计划也支持可选 `[库路径]`；这样厂商器件不需要退回 JSON。

合成示例见 `examples/specs/demo_circuit.txt`，空白骨架见 `templates/circuit_skeleton.txt`。示例中的工作区是占位路径，使用前改成自己的环境。

### 6.2 AI 两轮编写

可直接把 `AI_PROMPT.md` 中的系统提示词和首次需求模板交给 AI。坐标和连线困难时，优先让 AI 写布局计划，再由 `adstk layout` 生成 v1。

1. 第一轮：工程、变量、器件、参数、网络。先核对库名、引脚名、二极管方向。
2. 第二轮：根据 `templates/device_pin_table.json` 的 R0 偏移和旋转公式计算坐标，再写连线。
3. 运行 `build` dry，把带行号的错误返回给 AI 修正。
4. 同网必须连通；同网 T 接和线过引脚允许。
5. dry 通过后再考虑 apply。

不要把自由自然语言直接作为唯一输入。坐标、引脚和网络必须落成确定字段，否则同一段话可能生成不同电路。

### 6.3 常见易错点

- 二极管方向和三脚封装的引脚接法（哪两个脚同节点）必须按项目引脚表和模型核对，不要凭引脚名称猜测。
- `V_Probe` 和 `GROUND` 是单脚器件，不要按两端器件连线。
- `P_1Tone.Freq` 必须等于分析频率。
- HB、ParamSweep、VAR 也是 `[器件]`，需要明确列出。
- 每条连线必须非零、正交。
- 每个实际引脚在网络中必须出现且只能出现一次。

### 6.4 JSON/文本转换

```bat
"<ADS>\tools\python\python.exe" -B -m adstk convert input.json output.txt
"<ADS>\tools\python\python.exe" -B -m adstk convert input.txt output.json
```

导出后会立即回读比较语义；`;`、`|`、换行等无法无损表示的内容会拒绝，不会悄悄改变电路。转换使用原子替换，避免写坏目标文件。

## 7. 检查体系

1. **格式检查**：字段、角度、坐标、未知区段、引脚引用，尽可能带行号。
2. **几何检查**：
   - 漏引脚、重复引脚、单脚网络；
   - 引脚碰异网、悬空端点、折线顶点碰异网；
   - 交叉、共线重叠、零长度/非正交线；
   - 器件本体重叠、线穿多引脚器件本体；
   - 同网连通分量。分离接地岛允许，但每个孤立组必须有 GROUND。
3. **spec ↔ 生成网表**：用 `master_pin.term_number - 1` 作为真实网表引脚号，逐网络比较关联集。任何不符都失败。
4. **参考网表**：比较器件、节点双射、参数尾、控制行、OutputPlan、模型卡、未知指令等。
5. **PCE 数据检查**：探针配对、功率轴名称、DC 行、有限数值。

这些检查保证“结构和连接符合 spec”，不证明器件模型、论文参数或物理设计一定正确。

## 8. PCE 计算

```text
PCE = 100 × (Vdc² / RL) / Pin_W
Vdc = V_UP - V_DN      双轨
Vdc = V_OUT - 0        单端
Pin_W = 10^(pwr_dBm/10) / 1000
```

- `--rl` 是负载电阻，不是匹配阻抗。
- 功率轴名称：spec `power_axis` 或 `--power-axis`（CLI 优先），默认 `pwr`。
- 探针实例名不是 `V_UP`/`V_OUT`/`V_DN` 时，用 `--up` / `--dn` 指定数据集列名；`sweep`/`load-sweep` 也支持这两个参数。
- 数据集有多个 varblock 时用 `--varblock`。
- 缺少 `V_UP` 对应的 `V_DN`、缺少 DC 点、负载非有限值都会报错。
- 负载扫描用 `adstk load-sweep`，每个点用自身阻值作为 RL；不要用固定 `--rl` 的 `sweep` 扫负载。

只处理已有数据集：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk report --ds result.ds --rl 550 --csv eta.csv
```

## 9. 跨机器/跨工程

需要修改：

1. spec 的 `design.workspace/library/cell`。
2. 非标准库和厂商器件套件（如 HFDiode vendor kit）用 `libs`（JSON）或 `[库路径]`（文本/plan）指定路径。
3. 新器件类型加入 `device_types`（JSON）或 `[选项] 器件类型 = ...`（文本/plan），否则网表比较会保守地判不等价，readback 会提示未检查。
4. ADS 安装位置用 `ADS_PATH` 或 spec `ads`。
5. 工作区里 `lib.defs` 的厂商库引用。

相对路径以 spec 文件所在目录为基准。

## 10. 故障排查

| 现象 | 处理 |
|---|---|
| `.cdslck` 错误 | 关闭 ADS GUI，确认没有其他进程占用设计 |
| library not open | 检查 ADS 安装、`libs` 路径、`lib.defs` |
| 缺少 Top Design | 网表必须包含 `; Top Design: "lib:cell:view"` |
| apply 显示 not saved | 看 problems，通常参数、引脚、短路或连接核对未过 |
| 文本报“不存在的器件” | 网络引用了未在 `[器件]` 声明的名称 |
| vars 无 VAR | 添加 VAR 实例并在 `var_part` 指定 |
| TAHB 无法关闭 | 检查 HB 实例和 `TAHB_Enable` 参数 |
| 无 PCE | 确认探针列名、`V_UP/V_DN` 配对、DC 点和功率轴 |
| 数据集选错 | 用 `--varblock` 指定 |

## 11. 安全边界

- 写设计前关闭 ADS GUI；工具会查锁并自动备份。
- 没有 reference 时，不要仅凭“无 problems”判断电路原理正确。
- AI 生成的 spec 必须先 dry，不要直接 apply。
- 仿真和扫参会写输出目录，并在运行前清理目标 `.ds`。
- 其他电脑、其他 ADS 版本、镜像方向、任意拓扑自动布线都未验证。
