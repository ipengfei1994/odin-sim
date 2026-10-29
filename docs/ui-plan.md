# ODIN-Sim UI 规划（L4 接口层）

> 状态：规划草案（v1）
> 依据：`docs/architecture-v1.md` 的 L4 接口层约束与依赖规则
> 范围：仅规划，不写实现代码；实现规划见文末 §9

## 1. 目标与定位

为 ODIN-Sim 增加一个**面向课题组成员**的易用入口，把已稳定的 CLI 能力
（`sweep` / `analyze` / `plot` / `curves`）编排成界面，覆盖
"配置实验 → 运行 → 浏览结果 → 看演化曲线" 的完整研究回路。

**定位边界（不可妥协）：**

- UI 是 **L4 接口层**（`cli / api(二期) / ui(二期)` 之一），与 `cli` 并列，**共存不替换**。
- UI 是已有能力的**展示与编排层**，不引入任何新的仿真 / 动力学逻辑。
- UI **只读产物、调分析层**，不反向写仿真——类比 `analysis` 不得 import `engine` 的约束。

## 2. 架构约束（来自 architecture-v1.md）

| 约束 | 出处 | 对 UI 的含义 |
|---|---|---|
| L4 接口层：`cli / api(二期) / ui(二期)` | §3.1 (119) | UI 与 cli 平级，不是核心层的一部分 |
| 核心层不得 import `ui` | §4.3 (208) | UI 是依赖方，反向依赖为 0；CI 的 import 规则检查反向 |
| `analysis` 不得 import `engine` | §4.3 (211) | UI 若要画图，调 `analysis` 公开函数，不得自己重跑仿真 |
| MVP 不含 Web UI | §2 (53)、Q-E2 (284) | 本期是二期项，优先级决策而非技术阻塞 |

**推论**：支撑 UI 的前置（storage / analysis / figures / curves）现已就绪，
UI 现在即可开工，且只能做成"消费文件系统产物 + 调分析层"的形态。

## 3. 技术选型：Streamlit 起步

`pyproject.toml` 的 `[web]` 组已声明 `fastapi / uvicorn / pydantic / streamlit / plotly`，
本期实际启用 **`streamlit` + `plotly`**，`fastapi/uvicorn` 留作二期升级。

### 3.1 与 FastAPI + plotly 前端的对比

| 维度 | A. Streamlit（本期） | B. FastAPI + plotly 前端（二期演进） |
|---|---|---|
| 开发量 | 小（数天） | 大（API + 前端 + 任务调度） |
| 多用户 / 鉴权 | 弱，定位单机 / 内部 | 原生支持 |
| 长任务（sweep） | 同步会阻塞会话，需自建后台 | 异步队列天然适配 |
| 复用现有能力 | 直接 import `experiments` / `analysis` | 同左，但经 API 暴露 |
| 升级路径 | 后续可外包 FastAPI 做异步 | 一步到位 |

### 3.2 为什么从 A 起步

MVP 刚完成、当前是课题组内部研究工具。A 把已稳定的 CLI 能力包成界面，
成本最低、最快出活，且完全满足 §2 的 L4 约束。当出现"多用户并发 + 异步大规模
sweep"的真实需求，再演进到 B——A→B 是**增量**（FastAPI 作中间层、前端保留 plotly），
不是推倒重来。

## 4. 模块结构（提议）

```
src/odin_sim/ui/
  __init__.py
  app.py            # streamlit 入口（侧边栏导航各 page）
  config_bridge.py  # 表单输出 → SweepSpec，复用 experiments 的 schema 校验（不重写）
  pages/
    configure.py    # 构建 sweep 配置（表单 → JSON）
    runs.py         # 浏览已有 sweep：sweep_manifest.json + results.csv
    explore.py      # 结果可视化：调 analysis 生成图，plotly 渲染
    timeseries.py   # 演化曲线：调 curves 逻辑
```

**关键**：`ui` 不重新实现 `sweep` / `analysis`，只 import 它们的公开函数
（`experiments.expand_sweep` / `run_sweep`、`analysis.aggregate` / `figures` /
`curves` 相关入口）。这正是 L4 的本职——编排。

## 5. 数据契约（UI 消费 / 产出）

| 方向 | 契约（与 CLI 同 schema） |
|---|---|
| 输入配置 | sweep 配置 JSON：`name` / `model` / `base{mode,placement,beta_gov,tau,scenario}` / `axes` / `seeds{start,count,step}` / `store_trajectory` / `control` |
| 浏览 | 根目录：`sweep_manifest.json`、`results.csv` |
| 单 run 详情 | `runs/<run_id>/manifest.json`、`timeseries.csv`、`trajectory.npz`（若 `store_trajectory=true`） |
| 可视化 | 复用 `analysis.aggregate` / `figures` / `curves`；前端 plotly 重绘或展示已生成 PNG |

> 注：`runs/<run_id>/timeseries.csv` 为本轮新增产物（`docs/experiments.md` §2 的
> 目录树尚未列出，待同步更新）；UI 直接依赖它，**无需开 `store_trajectory`** 即可看演化曲线。

## 6. 分期

- **P1（最小可用）**：配置表单 → 触发 sweep（小网格，同步）→ 结果表 + 静态图浏览。
  目标：打通 L4 依赖约束，证明 UI 能复现 CLI 结果。
- **P2**：演化曲线页（复用 `curves`） + 按 `beta_gov` / `tau` 分组切换。
- **P3（按需）**：长任务改异步（后台进程 / 任务队列）、多 sweep 对比、导出。

## 7. 开放问题与默认决策

| # | 问题 | 本期默认决策 | 状态 |
|---|---|---|---|
| 1 | sweep 是 CPU 密集长任务，UI 同步跑会卡死 | P1 仅支持**小网格同步**；异步改到 P3（届时演进 B） | 待课题组确认是否提前 |
| 2 | 是否需要多用户 / 鉴权 | A 形态下**暂不需要**，定位内部单机 | 取决于有无多人同时使用 |
| 3 | 图现算（plotly 交互）还是展示 PNG | `explore` 页用 **plotly 现算**（analysis 已在内存聚合）；静态报告用 PNG | 默认，可调 |
| 4 | `store_trajectory` 默认 `false`，UI 浏览轨迹需它 | UI 提供**显式开关**，默认关；演化曲线不依赖它（`timeseries.csv` 总是落盘） | 默认，可调 |

## 8. 验收标准

- `ui` 不 import 任何核心层之外的东西；反向依赖经 CI 的 import 规则检查通过
  （核心层 import 图不含 `ui`）。
- 能复现一个已有 sweep（导入 config → 跑 → 出图），结果与 CLI **逐位一致**。
- 不引入新的仿真 / 动力学逻辑；UI 内不出现 `engine.run_once` 的直接调用路径
  （只经 `experiments.run_sweep`）。

## 9. 实现规划（进入实现阶段后再细化）

- 先落 `config_bridge.py`：复用 `experiments` 的 pydantic / schema 校验，
  把表单 dict 转成 `SweepSpec`，失败给出可读错误（与 CLI 同口径）。
- `runs.py` 只读文件系统产物，不触发任何计算。
- `explore.py` / `timeseries.py` 调 `analysis` 公开函数，前端 plotly 渲染。
- 每个 page 配一个最小冒烟测试（用 `results/ts_demo` 这类已落盘数据，不重跑仿真）。
- 反向依赖检查接入现有 CI import 规则。

## 10. 风险

- **长任务阻塞**：P1 同步 sweep 仅适合小网格；大网格需 P3 异步，否则体验差。
- **重复实现**：UI 必须复用 `experiments` / `analysis`，警惕为"方便"重写校验或聚合。
- **产物契约漂移**：UI 依赖 `timeseries.csv` 等产物 schema，核心层改动需同步回归 UI 冒烟测试。
- **内核依赖**：「运行 sweep」需要 G3 基线内核（`odin_g3` + `ODIN_BASELINE_ROOT`）。
  读-only 页（浏览 / 可视化 / 演化曲线）不依赖内核，离线可用；configure 页在模型
  未注册时给出明确警告，并在运行时捕获异常友好提示，不崩溃。

## 11. 实现进度

| Checkpoint | 内容 | 状态 | 入口 |
|---|---|---|---|
| P1-1 | `config_bridge`：表单 dict → `SweepSpec`（复用校验） | 完成 | `ui/config_bridge.py` |
| P1-2 | Streamlit 入口 + 侧边栏导航 | 完成 | `ui/app.py` |
| P1-3 | 浏览结果（只读：概览 + 表 + 运行明细） | 完成 | `ui/pages/runs.py` |
| P1-4 | 配置实验 → 触发小网格同步 sweep | 完成 | `ui/pages/configure.py` |
| P1-5a | 可视化（plotly 交互，复用 `analysis.aggregate`） | 完成 | `ui/pages/explore.py` |
| P1-5b | 演化曲线（plotly 带置信带，复用 `analysis.aggregate_timeseries`） | 完成 | `ui/pages/timeseries.py` |
| P1-6 | 反向依赖 CI 检查 + 整页冒烟 | 完成（检查在 `test_ui_layering.py`；冒烟在各页测试） | `tests/unit/test_ui_*` |

**P1 验收对照**（§8）：核心层零反向 `ui` 依赖（`test_ui_layering` 守）；UI 复用
`experiments` / `analysis` 不重写；「运行 sweep」经 `experiments.run_sweep` 单一路径；
读-only 页不触发任何计算。UI 可复现 CLI 结果的逐位一致性由 `run_sweep` 同一代码路径保证
（需内核依赖就绪后实跑验证）。

**P2（待做）**：演化曲线按 `beta_gov` / `tau` 分组切换已在 P1-5b 的 `group_by` 多选支持；
多 sweep 对比、导出、长任务异步化留到 P3。
