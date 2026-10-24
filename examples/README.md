# examples 目录说明

- `specs/`：可编辑输入示例，复制或修改后交给 `build/readback/sim/sweep`。
  - `rectA_spec.json`：rectA 的 JSON spec。
  - `rectA_spec_from_design.json`：从已保存设计读回生成的 JSON spec。
  - `rectB_circuit.txt`：固定中文文本示例，包含 15 个原理图实例和 5 个网络。
- `evidence/`：验证用网表日志，只读参考。
  - `rectA_saved_netlist.log`：已保存 rectA 设计网表。
  - `rectA_built_netlist.log`：dry 重建得到的历史网表。
- `sim_out/`：一次 rectA 无界面仿真的样例输出，包括网表、数据集、`eta.csv` 和中间文件。该目录是本地产物，默认不提交到 GitHub。

`specs/` 中的相对工作区路径假定 `ads_toolkit`、`2_project`、`3_project` 保持同级目录。换机器或换工程后，请修改 spec 的 `design.workspace/library/cell`。

详细用法见 `../docs/USAGE.md`。
