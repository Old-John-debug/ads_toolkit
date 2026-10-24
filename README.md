# ads_toolkit —— ADS 原理图建模与无界面仿真

用 ADS 自带 Python 读回原理图、从 JSON/固定中文文本 spec 重建、检查连接、生成网表、调用 `hpeesofsim`，再计算 PCE。

- 完整操作手册：[docs/USAGE.md](docs/USAGE.md)
- AI 编写材料：[templates/circuit_skeleton.txt](templates/circuit_skeleton.txt)、[templates/device_pin_table.json](templates/device_pin_table.json)、[examples/specs/rectB_circuit.txt](examples/specs/rectB_circuit.txt)
- 示例目录说明：[examples/README.md](examples/README.md)

**目前只在 ADS 2025 Update 2 + Windows 实测，其他版本和电脑尚未验证。**

## 文件分类

```text
ads_toolkit/
  README.md                 入口与速查
  docs/USAGE.md             完整使用手册
  adstk/                    内核：建模/审计/网表/仿真/PCE/文本 spec
  tools/                    从已有设计或项目脚本生成 spec
  templates/                空白骨架、引脚表、最小设计配置
  examples/
    specs/                  可编辑 JSON/文本 spec
    evidence/               只读验证网表
    sim_out/                本地生成的仿真产物（不提交）
  tests/                    单元测试与回归反例
  de_sim.cfg                 ADS 环境配置
  hpeesofsim.cfg             ADS 仿真环境配置
```

## 五分钟上手

使用 ADS 自带 Python，在工具包根目录运行：

```bat
"<ADS>\tools\python\python.exe" -B -m unittest discover -s tests -v
"<ADS>\tools\python\python.exe" -B -m adstk --help
```

仓库不提交 `.ds`、`.raw` 等仿真二进制产物。本机若保留 `examples/sim_out/rectA.ds`，可以只读复算峰值 **81.5752% @ 27 dBm**：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk report --ds examples/sim_out/rectA.ds --rl 550
```

## 两条输入路线

### 已有 ADS 原理图

复制 `templates/design_only.json`，填写 `design` 三件套，然后：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk readback my_design.json --netlist-out my.log
```

`readback` 只读，不会保存设计。`sim` 可以直接对保存的设计生成网表并仿真。

### 从 spec 建图

```bat
:: 1. 只检查，不保存
"<ADS>\tools\python\python.exe" -B -m adstk build my_spec.json --reference expected.log --netlist-out built.log

:: 2. 检查通过后保存（自动查锁、备份）
"<ADS>\tools\python\python.exe" -B -m adstk build my_spec.json --apply --reference expected.log

:: 3. 仿真 / 扫参
"<ADS>\tools\python\python.exe" -B -m adstk sim my_spec.json --rl 550 --csv eta.csv
"<ADS>\tools\python\python.exe" -B -m adstk sweep my_spec.json --part L2 --param L --values "22 nH,30 nH,36 nH" --rl 2000
```

未通过 dry 前不要加 `--apply`。

## 保存门禁

`--apply` 只有在以下检查全部通过时才保存：

1. 引脚探测事务正常回滚，没有临时实例残留。
2. 参数和 VAR/TAHB 设置成功。
3. 几何检查通过：漏/重引脚、单脚网络、短路、悬空、交叉、共线、零长度/非正交线、器件重叠、线穿本体、同网连通。
4. spec 引脚关联集与 ADS 实际生成网表一致（使用 `term_number - 1` 映射）。
5. 如果提供 `--reference`，网表和控制器比较也通过。

普通网络必须连通；分离接地岛允许，但每个孤立组必须有 GROUND。

## 固定文本与 AI

文本固定为 `版本 = 1` 和六段：`[工程] [变量] [器件] [网络] [连线] [选项]`。

给 AI 时必须同时提供：

- 空白骨架：`templates/circuit_skeleton.txt`
- 引脚表和旋转公式：`templates/device_pin_table.json`
- 完整示例：`examples/specs/rectB_circuit.txt`

推荐两轮：先写工程/器件/网络，再补坐标/连线；每次用 `build` dry 的带行号错误迭代。JSON 与文本可互转：

```bat
"<ADS>\tools\python\python.exe" -B -m adstk convert input.json output.txt
"<ADS>\tools\python\python.exe" -B -m adstk convert input.txt output.json
```

转换会回读校验语义；无法无损表示的内容直接拒绝。

## PCE 口径

```text
Vdc = V_UP - V_DN      双轨
Vdc = V_OUT - 0        单端
PCE = 100 × (Vdc² / RL) / Pin_W
Pin_W = 10^(pwr_dBm/10) / 1000
```

`V_UP/V_DN` 必须成对；功率轴默认名 `pwr`，其他名称用 `--power-axis`；DC 必须满足 `freq == 0`。`--rl` 是负载电阻，不是匹配阻抗。

## 安全边界

- 写设计前关闭 ADS GUI；工具查 `.cdslck` 并自动备份 `sch.oa`。
- dry 不调用 `save_design()`，但 ADS 仍可能更新工作区 `.prf` 元数据；要求物理零写入时用工作区副本。
- `sim`/`sweep` 会写输出目录，并在运行前清理本次顶层单元的旧 `.ds`。
- 无 reference 时，“无 problems”只代表结构和连接符合 spec，不代表器件模型或论文参数正确。

## 验证状态（2026-10-24）

- **57 项**单元测试通过。
- rectA/rectB 在临时工作区副本中通过真实 ADS dry、spec/网表连接核对和参考网表比较。
- rectA 既有数据集：**81.5752% @ 27 dBm**；rectB 调参后：**73.8535% @ 2 dBm**。

已知限制：仅 0/90/180/270 旋转；不支持镜像和任意拓扑自动布线；未知器件在网表比较中保守处理；其他 ADS 版本和电脑未验证。
