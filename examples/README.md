# examples 目录说明

本目录只保留**不含论文数值**的合成示例，用来演示格式和布局流程。

- `specs/demo_layout.plan.txt`：简化布局计划，不写坐标和连线。
- `specs/demo_circuit.txt`：由该计划生成的完整 v1 文本。

demo 使用占位工作区 `../workspace_demo`、库 `demo_lib`、单元 `demo_network`。真正运行前，复制一份并改成自己的 ADS 工作区/库/单元。

论文专属的 spec、器件引脚表、网表证据和仿真结果不放在本仓库，应放在对应项目目录。

详细用法见 `../docs/USAGE.md` 和 `../docs/LAYOUT_PLAN.md`。
