# 本机适配记录（Windows + ADS 2024 Update 2）

本副本基于上游 `main`（基线 commit `e6e1a33`），为在 **Windows + ADS 2024 Update 2** 上正常使用
（以及复现 paper2 的三个原理图）做了以下本地改动。每个改动都有实测验证；改动只在本机工作区，
未 commit。

## 改动一览

### 1. `adstk/core.py::ads_root` 接受字符串路径

- **原问题**：`build` / `sim` / `readback` 里都是 `core.setup_env(spec.get("ads"))`，把字符串直接传进
  `ads_root()`；而 `ads_root` 只按 dict 处理（`spec.get("ads")`），于是 spec 里**只要写了 `ADS路径`
  就会崩溃**：`AttributeError: 'str' object has no attribute 'get'`。
- **改法**：`ads_root` 先判断 `isinstance(spec, (str, os.PathLike))` 直接按路径返回。
- **验证**：含 `ADS路径 = D:/Program Files/Keysight/ADS2024_Update2` 的 spec 运行 `build` dry →
  `ok: True`（修复前直接抛异常）。

### 2. `adstk/core.py::sim_path_dirs`（新增）+ `adstk/sim.py::run_netlist` 注入 PATH

- **原问题**：本机直接 spawn `hpeesofsim.exe` 会以 `0xC0000135`（STATUS_DLL_NOT_FOUND）静默退出，
  git-bash 下还会误报成缺 `api-ms-win-crt-*.dll`。实际是 ADS 2024 的仿真器依赖 `<ADS>/bin` 之外的
  辅助 DLL（`ptgem.dll`、`python310.dll` 等），需要这些目录在 PATH 上。
- **改法**：`sim_path_dirs(ads)` 返回存在的：
  `<ADS>/bin`、`<ADS>/adsptolemy/lib.win32_64`、`<ADS>/fem/*/win32_64/bin`；
  `run_netlist` 在构造子进程环境时把它们前置到 PATH（跨平台安全：只加入实际存在的目录）。
- **验证**：在 PATH 不含任何 ADS 目录的裸环境下直接调用 `adstk.sim.run_netlist` → 仿真成功并生成
  `.ds`（修复前同样的调用返回 `ok=False`、无输出）。

### 3. `adstk/sim.py::_sanitize_netlist`（新增）剔除 LinearCollapse 注入行

- **原问题**：ADS 给含 `S_Param` 的网表自动追加
  `#load "python","LinearCollapse"` 和 `Component Module="LinearCollapse" ...` 两行；无界面运行时
  内嵌 Python 找不到该模块（`ModuleNotFoundError: No module named 'LinearCollapse'`），**整个仿真终止**，
  即使所请求的数据与它无关。
- **改法**：`run_netlist` 写盘前剔除以这两行开头的行（仅此两条精确前缀，避免误删）。
- **验证**：readback 原始网表（含这两行）裸跑成功，S 参数列完整（S/Z/PortZ 均存在）。

### 4. `adstk/build.py::_params` 支持"可重复参数"

- **原问题**：ADS 2024 的 `ParamSweep` 没有普通字符串参数 `SimInstanceName`，它是**可重复参数**
  （`number_of_repeats` / `get_repeat(i)`），文本 spec 原先无法表达，`build` 报
  `param Sweep1.SimInstanceName not found`。
- **改法**：`core.repeated_params(inst)`（新增）收集可重复参数；`_params` 在普通参数找不到时回退到
  "写第一个重复槽"（沿用 `QUOTE_PARAMS` 自动补引号）。num 为 0 时给出明确报错。
- **验证**：spec 直接写 `SimInstanceName=SP1` → `build --apply` 后网表出现
  `ParamSweep:Sweep1 SimInstanceName[1]="SP1" ...`，参数扫描仿真正常出调谐曲线。

### 5. `adstk/netlistx.py` 解析 `"MODEL":INST` 网表行

- **原问题**：ADS 把“器件绑定模型”写成 `"DIODEM1":D1  N__1 N__2`（如 Diode + Diode_Model）。原解析器只认
  `Type:Name`，这些行整行落入 opaque，`compare_spec_connectivity` 看不到二极管的两个节点，会把它们判成
  单脚网络。
- **改法**：新增 `MODEL_DEV_RE`，把 `"model":inst node node ...` 解析成 `ModelDevice` 并纳入 incidence；
  器件名仍取实例名，节点顺序保持不变。补 2 项单元测试。
- **验证**：含两个封装二极管的整流器 spec → `build` dry 的 spec↔生成网表连接核对通过；72/72 测试通过。

## 回归

```bat
"D:\Program Files\Keysight\ADS2024_Update2\tools\python\python.exe" -B -m unittest discover -s tests
:: Ran 72 tests ... OK
```

另有端到端实测：paper2 三个单元（`PA_HarmNet`/`Coupler`/`SIR_ideal`）build dry、apply、readback、
裸环境仿真全部通过（证据在 `F:\reproduction\paper2\`）。

`tools/package_subcircuit.py` 也在本机实测：3 端口 cell → 删除临时 Term → 添加 hierarchical term/pin →
生成 symbol → 母图 `X1` 例化；HB 结果与摊平版逐位一致（WPTreproduct 的 `HSMS286C_pkg`）。

## 用本仓库时会踩的其他坑（非代码问题，记录备查）

- 没有布局工艺库（tech）的工作区里，`MLOPENSTUB`（网表类型 `PC_OpenStub`）在仿真中**塌缩为理想
  短路**（实测 Z11=0），会悄悄毁掉整条网络。开路枝节请用经典 `MLOC`（网表类型 `MLOC2`）。
- `ParamSweep` 的扫描值要带单位（如 `Start=2 pF; Stop=19 pF`），否则被当成无量纲数。
- ADS GUI 打开某单元期间（存在 `sch.oa.cdslck`），工具包会拒绝对该单元 build/apply —— 这是正常
  保护行为，关掉对应窗口即可。

## 与上游的关系

以上 5 处改动都是通用性的（不是本机专属 hack）：字符串路径兼容、PATH 注入、注入行清洗、可重复
参数支持、模型绑定器件行解析。建议上游评估后合并；本机副本未提交、未推送。
