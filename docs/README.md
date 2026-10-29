# 文档说明

本目录的文档按**归属**分层。ODIN-Sim 与 odin-agents 是两个独立项目，
odin-agents 的模型、参数与研究结论不属于本仓库，不入版本控制。

## 随仓库发布

| 文档 | 内容 |
|---|---|
| [`architecture-v1.md`](architecture-v1.md) | 架构方案 v1（草案）：职责边界、模块划分、数据流、实现路线比较 |
| [`experiments.md`](experiments.md) | 实验层：配置 schema、`run_id` 与续跑规则、存储决策、已知限制 |
| [`ui-plan.md`](ui-plan.md) | UI 规划（L4 接口层）：Streamlit 起步的技术选型、模块结构、数据契约、分期与验收标准 |
| [`topology-assumptions.md`](topology-assumptions.md) | `ws` / `sbm` 的参数与随机数约定，状态为**待确认假设** |

## 不在版本控制内（本地保留）

| 目录 | 内容 | 不发布的原因 |
|---|---|---|
| `docs/internal/` | 外部参考实现的公式、参数、指标口径与阶段工作稿 | 属 odin-agents，非本仓库内容；含本机绝对路径 |
| `docs/reference/` | 项目内部的技术选型与实施架构文档 | 含未公开的架构与实施安排 |

两者已在 `.gitignore` 中排除。若将来需要公开发布，应逐份确认后再移出。

## 约定

- 文档与代码注释中**不出现本机绝对路径**，外部项目位置一律用环境变量
  （`ODIN_BASELINE_ROOT`）或占位符表示。
- 引用外部项目时只描述**接口契约**，不复制其数值逻辑与研究结论。
- 已实现与未实现必须分开标注，未验证的能力不得写成已完成。
