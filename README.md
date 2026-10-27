# ODIN-Sim

ODIN 研究体系中的仿真平台：负责网络环境、仿真调度、动力学执行、实验管理、数据记录与结果分析。
平台不写死某一种动力学模型或智能体实现。

当前阶段：**W1-c（协议抽取：模型 / 拓扑 / 指标三层协议 + 引擎骨架 + 指标等价性回归）**。尚未产出任何新的科学结论。

**验证状态**：266 行逐次运行记录 × 25 个指标字段**全部通过**（0 不一致）。
- 轨迹指纹：`traj_hash`、`network_hash` 逐字符一致；
- 指标口径：`extreme` / `joint` / `delta` / `backfire` / `alert` / `lead_time` / `recovery` / 成本计数逐字段一致。

详见 [`docs/w1-verification-record.md`](docs/w1-verification-record.md)。
注意：指标回归委托 baseline 的行构造器完成，验证的是**平台调用链与落盘口径**，
不等于平台侧已有独立指标实现（独立实现留待 W2 重构，届时以本回归为保护）。

---

## 与 G3 baseline 的关系

G3 baseline（模型版本 `g3-baseline-v1.0.0`）位于另一个项目 `odin-agents/baseline/`，已实跑并产出结论。

**本平台第一期不重写动力学，而是"包一层"**：通过适配层在运行期加载 baseline 内核，
用其自带的 `traj_hash` 做逐位回归。事实台账见 [`docs/baseline-g3-facts.md`](docs/baseline-g3-facts.md)。

适配层：`src/odin_sim/adapters/baseline_g3.py`（不复制源码、不修改 odin-agents 任何文件）。

### 协议分层（W1-c）

```
RunRequest ──► engine.run_once ──► DynamicsModel.run ──► RunOutput ──► Metric.compute
                    ▲                      ▲                                ▲
                    │                      │                                │
               只做分发/计时          model/registry.py 注册           model/registry.py 注册
```

- `model/protocol.py` 只定义接口与数据形状，不含任何模型语义；
- `model/registry.py` 显式注册，同名重复注册直接报错；
- `model/adapters/g3.py` 只做协议转换，**不复制任何更新方程**；
- `engine/` 只有三件事：分发请求、计时、组装产物。

G3 明确声明 `capabilities.stepwise = False`：baseline 内核不存在逐步驱动路径，
引擎不得假设可以每步回调 agent。逐步能力要等内核重构后再打开。

---

## 环境

```bash
uv venv --python 3.13
uv sync --extra dev
```

Python 要求 `>=3.12,<3.14`；本机实测 3.13.14，与 baseline 的 3.13 venv 一致。

---

## 常用命令

```bash
# 运行环境与代码版本快照
uv run python -m odin_sim.cli info

# 对 baseline 数据集做轨迹指纹回归（需要指定 baseline 根目录）
uv run python -m odin_sim.cli verify-baseline \
    --baseline-root D:/桌面/odin-agents/baseline \
    --data D:/桌面/odin-agents/baseline/data/g3_grid_runs.csv

# 对 baseline 数据集做指标等价性回归（25 个字段逐字段比对）
uv run python -m odin_sim.cli verify-metrics \
    --baseline-root D:/桌面/odin-agents/baseline \
    --data D:/桌面/odin-agents/baseline/data/g3_grid_runs.csv

# 单元测试与回归测试
uv run pytest -q
```

也可用环境变量免去每次传路径：

```bash
export ODIN_BASELINE_ROOT=D:/桌面/odin-agents/baseline
```

---

## 目录

```
src/odin_sim/
├── model/
│   ├── protocol.py          协议与数据形状（无模型语义）
│   ├── registry.py          显式注册表（重复注册报错）
│   └── adapters/g3.py       G3 baseline 的协议转换（不含数值逻辑）
├── engine/runner.py         请求分发 / 计时 / 产物组装
├── reproducibility.py      实验元信息采集（commit / Python / 依赖 / 配置指纹）
├── verification.py          baseline 指纹与指标比对逻辑
├── adapters/baseline_g3.py  外部 baseline 的只读加载（唯一的外部依赖入口）
├── storage/layout.py        产物目录命名规则
└── cli.py                   命令行入口
docs/                        架构、baseline 事实台账、验证记录
docs/reference/              参考文档（技术选型与实施架构）
scripts/                     独立可运行的验证入口
tests/{unit,regression}/     单元与回归测试
```

---

## 与参考文档的差异登记（逐条可追溯）

| 项 | 参考文档 | 本仓库现状 | 原因 / 何时对齐 |
|---|---|---|---|
| numpy 版本 | `>=2.0,<3.0` | 锁定 `==2.3.4` | baseline 的 BA 网络与初值依赖 PCG64 序列，版本变化会使 `traj_hash` 无法对齐；待跨版本稳定性验证后再放宽 |
| 依赖安装 | 一次性安装文档全部清单 | 主依赖只装 numpy；`analysis` / `web` 拆为 optional extras | W1 用不到；W2 启用 `analysis`，W3 启用 `web`，均按文档清单 |
| 实验框架 | 未强制 Mesa | 未引入 | 与文档"不强制迁移 Mesa"一致 |

---

## 已知限制

1. **指标实现仍委托 baseline**：平台侧没有独立的指标实现，`Metric` 协议下的四个 G3 指标全部转发到 baseline 的 `metrics` 模块。独立实现要在 W2 重构时引入，并以现有 266×25 回归为保护。
2. **平台不能注入新拓扑**：baseline 的 `model.simulate` 在模块作用域直接引用了自己的 `build_network`，因此 `G3Topology` 目前只能被外部调用，ws / sbm 无法接入主流程——这是 W2 的重构内容（注入后必须重跑指纹回归证明 ba 不变）。
3. **无逐步驱动**：`capabilities.stepwise = False`。外部 agent（odin-agents）要参与每步决策，必须先做内核逐步化重构。
4. **未覆盖含非默认参数覆盖的数据集**：`g3_metric_probe`、`g3_sensitivity_runs` 等需按脚本还原参数覆盖，不是简单场景名。
5. 仅在 Windows + Python 3.13.14 + numpy 2.3.4 上验证，未做跨机器与跨版本验证。
6. 参考文档已移入 `docs/reference/`；**未确认是否适合提交进公开仓库**（含内部架构信息）。
