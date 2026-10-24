# ads_toolkit —— ADS 原理图建模与无界面仿真工具包

用 ADS 自带 Python 读回原理图、从 JSON/固定文本 spec 或简化布局计划重建、检查连接、生成网表、调用 `hpeesofsim`，再计算 PCE。

工具包只包含**跨论文复用的能力**；论文参数、实验数据、器件厂商数据和复现结论应留在各自项目目录。

- 完整操作手册：[docs/USAGE.md](docs/USAGE.md)
- 简化布局计划：[docs/LAYOUT_PLAN.md](docs/LAYOUT_PLAN.md)
- AI 使用提示词：[docs/AI_PROMPT.md](docs/AI_PROMPT.md)
- 通用 demo：[examples/specs/demo_layout.plan.txt](examples/specs/demo_layout.plan.txt)

**目前只在 ADS 2025 Update 2 + Windows 实测，其他版本和电脑尚未验证。**

## 文件分类

```text
ads_toolkit/
  README.md                 入口与速查
  docs/USAGE.md             完整使用手册
  docs/LAYOUT_PLAN.md       简化布局计划说明
  docs/AI_PROMPT.md         给其他 AI 的任务提示词
  adstk/                    内核：建模/审计/网表/仿真/PCE/文本 spec/布局
  tools/                    从已有设计生成 spec
  templates/                骨架、通用引脚表、最小设计配置
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
```

未通过 dry 前不要加 `--apply`。

## 简化布局计划

只写器件、网络和布局角色，不写坐标/连线：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk layout my_layout.plan.txt my_spec.txt
```

生成的是完整 v1 文本，再按正常流程 build dry。适合“源 → 串联主链 → 输出节点”加对地支路、探针和控制器的拓扑。

- 多脚器件会自动把同一网络的额外引脚绕到符号外侧并接。
- 移动器件不会带动已有连线；**不要靠 GUI 拖动来改布局**，改 plan/spec 后重新生成。
- 论文/项目专用器件用 `--pin-table` 提供项目引脚表。

## 保存门禁

`--apply` 只有在以下检查全部通过时才保存：

1. 引脚探测事务回滚，无临时实例残留。
2. 参数和 VAR/TAHB 设置成功。
3. 几何检查通过：漏/重引脚、单脚网络、短路、悬空、交叉、共线、零长度/非正交线、器件重叠、线穿本体、同网连通。
4. spec 引脚关联集与 ADS 实际生成网表一致（`master_pin.term_number - 1`）。
5. 如果提供 `--reference`，网表和控制器比较也通过。

## 固定文本与 AI

文本固定为 `版本 = 1` 和六段：`[工程] [变量] [器件] [网络] [连线] [选项]`。

给 AI 时必须同时提供：

- 空白骨架：`templates/circuit_skeleton.txt`
- 通用引脚表：`templates/device_pin_table.json`
- 合成示例：`examples/specs/demo_circuit.txt`
- 布局计划骨架：`templates/layout_plan_skeleton.txt`

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

`V_UP/V_DN` 必须成对；功率轴默认名 `pwr`；DC 必须满足 `freq == 0`。`--rl` 是负载电阻，不是匹配阻抗。

## 安全边界

- 写设计前关闭 ADS GUI；工具查 `.cdslck` 并自动备份 `sch.oa`。
- dry 不保存设计，但 ADS 仍可能更新工作区元数据；要求物理零写入时用副本。
- `sim`/`sweep` 会写输出目录，并清理本次顶层单元的旧 `.ds`。
- 无 reference 时，“无 problems”只代表结构和连接符合 spec。

## 验证状态

- **61 项**单元测试通过。
- 通用布局、六脚器件外侧并接、审计、网表比较、PCE 和失败门禁均有回归测试。
- 真实 ADS 写入/仿真需在具体项目工作区中验证；本仓库不附带项目数据和论文结果。

已知限制：仅 0/90/180/270 旋转；不支持镜像和任意拓扑自动布线；未知器件在网表比较中保守处理；其他 ADS 版本和电脑未验证。
