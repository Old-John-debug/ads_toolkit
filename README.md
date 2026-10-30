# ads_toolkit —— ADS 原理图建模与无界面仿真工具包

用 ADS 自带 Python 读回原理图、从 JSON/固定文本 spec 或简化布局计划重建、检查连接、生成网表、调用 `hpeesofsim`，再计算 PCE；也可以把多脚器件（二极管+封装等）封装成带 symbol 的子电路。

工具包只包含**跨论文复用的能力**；论文参数、实验数据、器件厂商数据和复现结论应留在各自项目目录。

- 完整操作手册：[docs/USAGE.md](docs/USAGE.md)
- 简化布局计划：[docs/LAYOUT_PLAN.md](docs/LAYOUT_PLAN.md)
- AI 使用提示词：[docs/AI_PROMPT.md](docs/AI_PROMPT.md)
- 通用 demo：[examples/specs/demo_layout.plan.txt](examples/specs/demo_layout.plan.txt)

**目前只在 ADS 2025 Update 2 + Windows 实测，其他版本和电脑尚未验证。**
本机副本（Windows + ADS 2024 Update 2）已做本地适配并端到端实测通过，改动与验证见 [docs/LOCAL_ADAPTATION.md](docs/LOCAL_ADAPTATION.md)。

## 文件分类

```text
ads_toolkit/
  README.md                 入口与速查
  docs/USAGE.md             完整使用手册
  docs/LAYOUT_PLAN.md       简化布局计划说明
  docs/AI_PROMPT.md         给其他 AI 的任务提示词
  adstk/                    内核：建模/审计/网表/仿真/PCE/文本 spec/布局
  tools/                    从已有设计生成 spec；把多脚器件封装成子电路
  templates/                骨架、通用引脚表、布局计划/端口配置、最小设计配置
  examples/specs/           合成 demo：plan 与生成后的 v1
  tests/                    单元测试与回归反例
  de_sim.cfg                ADS 环境配置
  hpeesofsim.cfg            ADS 仿真环境配置
```

论文专属的示例、证据、引脚表和结果不放进本仓库。需要时在项目目录用 `--pin-table` 提供项目专属器件数据。

## 五分钟上手

```bat
"<ADS>\tools\python\python.exe" -B -m unittest discover -s tests -v
"<ADS>\tools\python\python.exe" -B -m adstk --help
```

demo 需要把工作区/库/单元改成自己的环境后才可真正 build。

## 两条输入路线

### 已有 ADS 原理图

复制 `templates/design_only.json`，填写 `design` 三件套：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk readback my_design.json --netlist-out my.log
```

`readback` 只读，不保存设计。

### 从 spec 建图

```bat
:: 只检查，不保存
"<ADS>\tools\python\python.exe" -B -m adstk build my_spec.txt --reference expected.log --netlist-out built.log

:: 检查通过后保存（自动查锁、备份）
"<ADS>\tools\python\python.exe" -B -m adstk build my_spec.txt --apply --reference expected.log

:: 仿真 / 扫参
"<ADS>\tools\python\python.exe" -B -m adstk sim my_spec.txt --rl 550 --csv eta.csv
"<ADS>\tools\python\python.exe" -B -m adstk sweep my_spec.txt --part L2 --param L --values "22 nH,30 nH,36 nH" --rl 2000
"<ADS>\tools\python\python.exe" -B -m adstk load-sweep my_spec.txt --part R1 --param R --values "30,35,40,45,50,55,60,65,70" --pwr 21
```

未通过 dry 前不要加 `--apply`。负载扫描使用 `load-sweep`，不要用固定 `--rl` 的 `sweep` 扫负载。

## 简化布局计划

只写器件、网络和布局角色，不写坐标/连线：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk layout my_layout.plan.txt my_spec.txt
```

生成的是完整 v1 文本，再按正常流程 build dry。适合“源 → 串联主链 → 输出节点”加对地支路、探针和控制器的拓扑。

- 多脚器件会自动把同一网络的额外引脚绕到符号外侧并接。
- 移动器件不会带动已有连线；**不要靠 GUI 拖动来改布局**，改 plan/spec 后重新生成。
- 论文/项目专用器件用 `--pin-table` 提供项目引脚表。

## 子电路封装（多脚器件）

把“器件 + 封装寄生”做成一个 3 端口（或 N 端口）子电路，母图里只放一个 X 实例，而不是摊平成一堆 C/L/Diode：

1. 用普通 spec 建一个端口 cell，端口处先放临时 `Term` 组件；`build --apply` 保存。
2. 复制 `templates/port_config.json`，填写：
   - `workspace`、`cell = 库:单元`
   - 每个端口：`name`、`num`、`anchor`（该网络上的一个实例引脚，如 `L3.P1`）、`loc`、`angle`
3. 运行：

```bat
"<ADS>\tools\python\python.exe" tools/package_subcircuit.py my_model.json
```

工具会删除临时 `Term` 和旧 term，添加 numbered hierarchical term/pin，重建 symbol，并打印“端口名 → symbol 引脚”映射。

4. 母图 spec 里直接例化：

```text
X1 | my_lib | my_model | (x, y) | 0 |
```

并在 `[选项]` 里把该 cell 名加入 `器件类型`，否则网表连接核对会把 X 实例当未知器件。

要点：

- `angle` 决定 symbol 上的引脚边：0=右、90=上、180=左、270=下。
- 端口 pin 的坐标要落在该网络的连线端点上；anchor 引脚只用于找网络。
- 生成 symbol 用的是 ADS 自带 `keysight.ads.de.experimental.generate_symbol`，本机在 ADS 2024 Update 2 实测；symbol 外形是默认矩形，不可定制。
- 运行前关闭 ADS GUI 中对应 cell，否则会被锁拦住。
- 母图的参数扫描仍在展开后的网表上改子电路内部行，不需要重新画图。

## 保存门禁

`--apply` 只有在以下检查全部通过时才保存：

1. 引脚探测事务回滚，无临时实例残留。
2. 参数和 VAR/TAHB 设置成功。
3. 几何检查通过：漏/重引脚、单脚网络、短路、悬空、交叉、共线、零长度/非正交线、器件重叠、线穿本体、同网连通。
4. spec 引脚关联集与 ADS 实际生成网表一致（`master_pin.term_number - 1`）。
5. 如果提供 `--reference`，网表和控制器比较也通过。

## 固定文本与 AI

文本固定为 `版本 = 1` 和六段：`[工程] [变量] [器件] [网络] [连线] [选项]`；另可选 `[库路径]` 用于厂商/项目库。

厂商器件项目要同时配置：`[库路径] 库名 = 路径`，以及 `[选项] 器件类型 = 网表器件类型`。只给 pin table 只影响布局，不会让 build 打开模型库，也不会让保守网表比较识别该器件。

给 AI 时必须同时提供：

- 空白骨架：`templates/circuit_skeleton.txt`
- 通用引脚表：`templates/device_pin_table.json`
- 合成示例：`examples/specs/demo_circuit.txt`
- 布局计划骨架：`templates/layout_plan_skeleton.txt`
- 子电路端口配置：`templates/port_config.json`

推荐两轮：先写工程/器件/网络，再补布局；每次用 `build` dry 的带行号错误迭代。JSON 与文本可互转：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk convert input.json output.txt
"<ADS>\tools\python\python.exe" -B -m adstk convert input.txt output.json
```

## PCE 口径

```text
Vdc = V_UP - V_DN      双轨
Vdc = V_OUT - 0        单端
PCE = 100 × (Vdc² / RL) / Pin_W
Pin_W = 10^(pwr_dBm/10) / 1000
```

`V_UP/V_DN` 必须成对；功率轴默认名 `pwr`（spec `power_axis` 或 `--power-axis` 均可指定）；DC 必须满足 `freq == 0`。`--rl` 是负载电阻，不是匹配阻抗。探针实例名不是 `V_UP`/`V_OUT`/`V_DN` 时，用 `--up`/`--dn` 指定数据列。

**注意：`sweep` 的 `--rl` 对所有扫点固定。扫负载电阻请用 `load-sweep`**，它会让每个负载点用自身阻值计算 PCE；`sweep` 只用于匹配网络等非负载参数。

## 安全边界

- 写设计前关闭 ADS GUI；工具查 `.cdslck` 并自动备份 `sch.oa`。
- dry 不保存设计，但 ADS 仍可能更新工作区元数据；要求物理零写入时用副本。
- `sim`/`sweep` 会写输出目录，并清理本次顶层单元的旧 `.ds`。
- 无 reference 时，“无 problems”只代表结构和连接符合 spec。

## 验证状态

- **71 项**单元测试通过。
- 通用布局、六脚器件外侧并接、审计、网表比较、PCE 和失败门禁均有回归测试；另含针对独立评审发现的回归：接地岛网表兜底、AUTO_PLAN 精确碰撞、load-sweep 逐点 RL、多余器件引脚拒绝、CLI 错误路径。
- 文本和 plan 支持可选 `[库路径]`，厂商库无需退回 JSON。
- 网表解析支持 ADS 的 `"MODEL":INST node node ...` 行（Diode 绑定 Diode_Model），连接核对不会再把这类器件当成单脚网络。
- `tools/package_subcircuit.py` 已在本机 ADS 2024 Update 2 上端到端验证：建 3 端口 cell → 生成 symbol → 母图 X 实例 → HB 结果与摊平版逐位一致。
- 真实 ADS 写入/仿真需在具体项目工作区中验证；本仓库不附带项目数据和论文结果。

已知限制：仅 0/90/180/270 旋转；不支持镜像和任意拓扑自动布线；未知器件在网表比较中保守处理；PCE 自动探针名默认 `V_UP`/`V_OUT`/`V_DN`（其他名称用 `--up`/`--dn`）；子电路 symbol 生成依赖 ADS experimental API，symbol 外形为默认矩形；其他 ADS 版本和电脑未验证。
