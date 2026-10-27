# ODIN-Sim 架构方案 v1（草案）

- 状态：待确认，未进入编码阶段
- 日期：2026-10-28
- 前置材料：项目内部的技术选型与实施架构文档（含未公开内容，本地保留在
  `docs/reference/`，不随本仓库发布）
- 原则：本方案只做架构与协议设计，不替课题组决定模型公式与指标定义。所有未确认的数学假设均标注 **[待确认]**。

---

## 0. 前提与已知约束

| 项 | 现状 |
|---|---|
| 工作区代码 | 当前工作区只有一份架构 HTML，**没有任何 baseline 代码** |
| 既有研究线索 | 二维 HK、跨维耦合、心理抗拒、恶意源、治理策略；参数 `beta_adv / beta_gov / tau / theta / kappa`；指标 `extreme / joint / backfire / 预警提前量` |
| 规模参考 | 500–5,000 agents（来自 HTML，未经性能验证） |
| 项目指令 | 第一阶段纯 Python MVP；性能瓶颈被基准证明后再考虑 Rust；平台不写死某一种 agent 实现或某一种动力学模型 |

**关键前提缺口**：baseline 代码、模型规格文档、指标定义文档均未在本工作区出现。MVР 的回归基线无法在拿到 baseline 前建立。这一点必须在开工前解决（见第 6 节 Q-E1）。

---

## 1. 平台要解决的问题

### 1.1 科研问题（平台必须能回答）

| 编号 | 问题 | 平台需要提供的能力 |
|---|---|---|
| R1 | 给定拓扑与交互规则，群体意见从初始分布演化到何种终态（共识 / 极化 / 碎片化） | 终态分类、收敛判定、轨迹记录 |
| R2 | 哪些参数控制相变边界（置信阈值 τ、耦合强度 κ、维度数 D、度分布） | 参数扫描 + 相图 + 多种子重复 |
| R3 | 治理干预在何种条件下有效、何种条件下产生 backfire | 干预注入点、反事实对照、成本计量 |
| R4 | 联合极端度能否作为极端化早期预警信号，提前量多少、误报率多少 | 时序指标、阈值扫描、事件标注与 ROC 类评估 **[定义待确认]** |
| R5 | 结论在多大程度上依赖特定拓扑 / 特定 agent 实现 | 拓扑与 agent 均可替换的对照实验 |

### 1.2 工程问题（平台必须解决）

| 编号 | 问题 | 设计对策 |
|---|---|---|
| E1 | 可复现性：同配置同种子必须得到同结果 | 自管理 RNG（不依赖全局随机）、种子派生、配置快照、环境指纹 |
| E2 | 模型可替换：不能把 HK 写死进平台 | DynamicsModel 协议 + Registry，HK 只是注册项之一 |
| E3 | Agent 可替换：odin-agents 不能污染内核 | AgentPolicy 协议 + 防腐层适配器，**Agent 不直接写状态** |
| E4 | 性能：N × 步数 × 重复 × 扫描组合的乘积 | 分层优化，先基准后优化，见第 5 节 |
| E5 | 实验管理：批量运行、失败恢复、进度、取消 | Run manifest + 幂等产物路径 + 断点续跑 |
| E6 | 数据追溯：哪次结果由哪份代码/配置/数据产生 | 产物自描述（manifest.json）+ 内容哈希 |
| E7 | 统计可信度：单次运行不能支撑结论 | 内置 replication（多种子）+ 聚合与置信区间 |

### 1.3 明确非目标

- 不做通用 ABM 框架（不重造 Mesa）
- 不做 LLM serving / 提示工程平台（属 odin-agents）
- 不承载真实用户数据与平台数据抓取
- 不在 MVP 阶段做 Web UI 与分布式调度

---

## 2. 与 odin-agents 的职责边界

### 2.1 归属划分

| 能力 | 归属 | 说明 |
|---|---|---|
| 网络构建、演化、IO | **ODIN-Sim** | agent 无权改图，除非通过 Action 申请并由引擎裁决 |
| 意见状态表示与存储 | **ODIN-Sim** | 唯一状态持有者 |
| 时间推进、调度顺序、终止判定 | **ODIN-Sim** | 同步 / 异步 / 事件注入由平台决定 |
| 动力学更新规则（HK、DeGroot…） | **ODIN-Sim** | 模型是平台资产 |
| 指标计算 | **ODIN-Sim** | 指标定义版本化，属于平台 |
| 实验、种子、存储、复现 | **ODIN-Sim** | |
| Agent 决策策略（观测→行动） | **odin-agents** | 含规则策略、启发式、LLM 策略 |
| 记忆、人格、画像、提示词 | **odin-agents** | 平台只看到策略接口 |
| LLM 调用、缓存、预算 | **odin-agents** | 平台只接收 Action |
| 外部工具与检索 | **odin-agents** | |

### 2.2 接口契约（核心）

```
Observation（Sim → Agent，不可变快照）
  - self_index, self_opinion (D,), neighbors (idx[], opinions[][,D], weights[])
  - step, seed_stream, global_summary（可选聚合量，避免 agent 看到全量状态）
  - history_window（有限长度，长度由 config 决定）

Action（Agent → Sim）
  - kind: EXPRESS | MESSAGE | REWIRE | NOOP | ...
  - payload: 目标节点 / 意见向量 / 消息内容 / 边操作
  - cost: 由 agent 声明，平台计账（是否计入成本指标 [待确认]）

执行规则
  1. Agent 只返回 Action，永不直接修改 OpinionState 或 Graph
  2. Action 如何作用于状态，由已注册的 DynamicsModel 解释
  3. 引擎负责冲突消解、顺序、越界裁剪（clipping 策略 [待确认]）
  4. 平台可在 config 中限制 agent 的 Action 权限集合
```

**写权限边界是这条契约的全部要点。** 一旦允许 agent 直写状态，可复现性与模型可解释性都会失效。

### 2.3 Agent 实现层级（统一接口，性能分级）

| 层级 | 实现 | 调用方式 | 性能 |
|---|---|---|---|
| L0 | 平台内置规则策略（纯 numpy） | 批量向量化，单进程 | 最高 |
| L1 | odin-agents 进程内 / 本地服务 | 批处理 `batch_decide(observations) -> actions` | 中，受批处理粒度影响 |
| L2 | LLM 驱动的 agent | 批处理 + 缓存 + 预算 | 最低，必须离线预生成或冻结 |

统一签名 `batch_decide(List[Observation]) -> List[Action]` 的理由：向量化策略可以在一次调用内处理全部 agent；LLM 策略可以自行决定批大小与并发。这样平台不需要为不同实现写不同循环。

### 2.4 防腐措施

- `odin_sim/agents/adapters/` 是唯一的外部依赖入口，内核与 experiments 不 import odin-agents。
- 适配器必须实现超时、失败降级（如退化为 NOOP 并记录）与调用计数。
- odin-agents 缺失时，平台必须能完整运行（MVP 验收项）。

---

## 3. 模块设计

### 3.1 分层

```
L4 接口层    cli / api(二期) / ui(二期)
L3 实验层    experiments (runner, sweep, replication, seeds)
L2 执行层    engine (loop, scheduler, recorder, stop)  ── 依赖协议，不依赖实现
L1 领域层    models / agents / metrics / topology / state
L0 基础层    numpy / scipy.sparse（NetworkX 仅用于生成与 IO）
```

依赖方向严格自顶向下；L2 只依赖 L1 的**协议（Protocol）**，不依赖具体模型类。

### 3.2 模块职责

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `topology` | 生成 ER/WS/BA/SBM；导入外部图；统一为 `GraphView` | NetworkX 只在此层出现；内核只消费 CSR 稀疏结构 |
| `state` | 意见状态 `OpinionState`（N×D 数组 + 属性列：stubbornness、群组标签等） | 状态不可被 agent 直写；属性列 schema 化 |
| `models` | 动力学模型实现 + Registry：`initialize / step / spec` | 不 import engine/agents/storage；HK 为注册项，不是平台本体 |
| `agents` | AgentPolicy 协议、内置规则策略、外部适配器 | 无状态写入权限 |
| `engine` | 时间循环、同步/异步调度、事件注入、快照采集、终止判定 | 更新顺序可配置且必须记录进 manifest |
| `metrics` | 逐步指标与终态指标，指标定义带版本号 | 同一指标不同版本可共存，产物记录版本号 |
| `experiments` | 单次 run、参数扫描、多种子重复、种子派生 | 每个 run 独立可复现；产物幂等 |
| `storage` | 产物目录布局、序列化、索引 | MVP 用文件系统 + manifest.json；SQLite/Parquet 为二期可选 |
| `analysis` | 聚合、统计检验、相图、预警分析、绘图 | 只读产物，不反向写仿真 |
| `reproducibility` | 采集 git commit、依赖版本、配置哈希、种子、平台信息 | 缺失信息需显式标注 unknown，不静默省略 |

### 3.3 动力学模型协议（草案）

```python
class DynamicsModel(Protocol):
    spec: ModelSpec              # 名称 + 版本 + 参数 schema + 文献引用
    def initialize(self, state, graph, rng) -> OpinionState: ...
    def step(self, state, graph, actions, ctx, rng) -> OpinionState: ...
    def finalize(self, state, history) -> dict: ...   # 终态指标钩子
```

- `actions`：本步所有 agent 行为，由模型决定如何解释（忽略/加权/作为额外邻居）。
- `ctx`：step 号、事件列表、调度顺序。
- 模型内部的组件（置信核、跨维耦合、心理抗拒门）属于**模型实现细节**，平台只提供 hook 位，不定义其数学形式 **[待确认]**。

### 3.4 拓扑与状态表示

- 拓扑：`GraphView` = CSR 邻接（indices/indptr/data）+ 可选权重 + 节点元数据。理由：HK 每步的信任邻居筛选是热点，CSR 便于块化与后续 Rust 迁移。
- 状态：`float64` N×D 数组（D 可配置，MVP 只保证 D=2 验证）**[待确认]**。
- 初始意见分布：均匀分布 / 正态截断 / 双峰 / 从文件加载，作为可配置项并进 manifest。

---

## 4. 数据流与依赖关系

### 4.1 单次 run

```
ExperimentConfig (YAML → pydantic 校验)
   │
   ├─ seed 派生 → rng = default_rng(f(master_seed, run_idx, config_hash))
   │
   ├─ topology.build(config.topology, rng) → GraphView
   ├─ state.init(config.init, N, D, rng)   → OpinionState
   ├─ agents.bind(config.agents)           → List[AgentPolicy]
   ├─ models.get(config.model)             → DynamicsModel
   │
   ▼
engine.run() 循环 t = 0..T:
   obs      ← engine.build_observations(state, graph)      # 只暴露允许的视野
   actions  ← agent.batch_decide(obs)                       # 外部 agent 在此调用
   actions  ← engine.sanitize(actions)                      # 权限/越界裁剪
   state    ← model.step(state, graph, actions, ctx, rng)   # 唯一写入点
   metrics  ← metrics.compute(state, graph, t)              # 逐步指标
   recorder.record(...)                                     # 按采样频率落盘
   if stop(state, history, t): break
   │
   ▼
RunArtifacts/
   manifest.json   配置快照 + 种子 + 环境指纹 + 版本
   timeseries.*    逐步指标与采样快照
   final.*         终态状态与终态指标
   metrics.json    指标定义版本号
```

### 4.2 批处理（扫描）

```
SweepSpec → 展开为 Run 列表（幂等 run_id = hash(config, seed)）
          → 顺序 / 多进程执行（MVP 用进程池，天然绕过 GIL）
          → 每个 run 独立产物目录
          → analysis.aggregate() 生成汇总表 → 相图 / 对照表 / 预警评估
```

### 4.3 依赖规则（写在 CI 里检查）

1. `models / metrics / topology / state` 不得 import `engine / experiments / storage / ui`。
2. `engine` 只 import 上述模块的 Protocol 与 Registry。
3. `agents/adapters` 是唯一允许 import 外部 agent 包的位置。
4. `analysis` 不得 import `engine`（禁止在分析阶段重跑仿真）。

---

## 5. 实现路线：纯 Python vs Python + Rust

### 5.1 计算特征与真实热点

HK 类模型的每步成本集中在三处：

1. **信任邻居筛选**：`|x_i - x_j| ≤ τ` 依赖当前意见值，邻接集合每步变化，无法用固定稀疏矩阵一次 matmul 完成。稠密距离矩阵在 N=5000 时约 2.5×10⁷ 元素（≈200 MB/步，float64），不可行；必须块化 + 稀疏化。
2. **邻域聚合更新**：稀疏 matmul（scipy CSR，C 实现），成本 O(E·D)，numpy 侧已接近原生性能。
3. **Agent 决策**：L0 可向量化；L1/L2 退化为逐 agent 或批处理调用，是 Python 侧最大可变量。

外加乘数：扫描组合数 × 重复种子数 × 步数 × 快照频率。

结论方向（**需基准验证，非既成事实**）：真正的 Python 瓶颈更可能是"信任邻居筛选的 Python 层循环 + agent 调用 + IO"，而不是基础线性代数。

### 5.2 路线对比

| 维度 | 纯 Python（numpy/scipy） | Python + Rust 内核（PyO3） |
|---|---|---|
| 开发速度 | 快，课题组可直接改模型 | 慢，需额外构建/发布流程 |
| 科研可调试性 | 高：可断点、可逐步打印 | 低：需回到 Python 参考实现调试 |
| 单 run 性能（N≤5k，向量化良好） | 通常足够 | 提升有限（scipy 已是 C） |
| 大规模扫描吞吐 | 可用多进程横向扩展 | 单进程 Rayon 并行 + 免 GIL，扩展更省内存 |
| 数值一致性风险 | 无 | 必须建立双实现对齐测试与容差标准 |
| 维护成本 | 低 | 高：两套实现、FFI、打包矩阵 |
| 适用触发条件 | MVP 与绝大多数扫描 | N≥10⁵ 或单进程扫描成为明确瓶颈时 |

### 5.3 分阶段方案与决策门

| 阶段 | 内容 | 出口条件（决策门） |
|---|---|---|
| **P0 MVP** | 纯 Python；CLI；1 个模型（多维 HK）；ER/WS/BA（+SBM 可选）；L0 规则 agent + mock 适配器；基础指标；种子与 manifest；pytest | 能从 CLI 复现并产出可追溯产物 |
| **P1 基准** | 建立 `benchmarks/`：N∈{500,2000,5000} × T × reps 的耗时/内存剖析，识别热点分布；按热点做 Python 侧优化（块化距离、CSR 复用、降快照频率、进程池并行扫描） | 得到热点剖面报告：时间在"内核数值 / agent 调用 / IO"上的占比 |
| **P2 决策** | **仅当** 门 G1 ∧ G2 同时成立才引入 Rust：`odin_core` PyO3 扩展，替换信任邻居筛选 + 更新 + 批量 run 并行；Python 实现保留为参考实现 | G1：典型扫描在目标硬件上超出课题组可接受时长（阈值需指定）<br>G2：剖析显示 ≥70% 时间在内核数值更新，且 Python 侧优化已用尽 |
| **P3 固化** | Rust 与 Python 双实现数值对齐测试（逐位或容差，标准需先定义）、性能回归进 CI、文档与复现说明 | 双实现一致性测试通过且有基准证据 |

**建议默认走 P0 → P1，把 Rust 决策推迟到拿到 P1 数据之后。** 这与"先保证科学正确性"一致：过早引入 Rust 会锁定模型实现，反而提高科研迭代成本。

### 5.4 若引入 Rust，必须预先约定的事

- 浮点一致性标准：求和顺序差异会导致非逐位相等，需定义容差（如 `atol=1e-12`）并以 Python 实现为参考。
- 随机数：Rust 侧不得独立生成随机数，随机数由 Python 侧生成后传入，或双方使用同一可复现算法（如 PCG64 + 相同种子派生）。
- FFI 边界粒度：以"整步更新"为单位调用，禁止逐 agent 跨边界调用（否则 FFI 开销抵消收益）。

---

## 6. 缺失定义与待确认问题

### 6.1 研究定义（我不自行编造，需课题组给出）

| 编号 | 待确认项 | 为什么必须确认 |
|---|---|---|
| Q-R1 | 意见的数学表示：域（[0,1]^D 还是 [-1,1]^D）、D 的语义与是否固定为 2、范数定义（欧氏 / 分项阈值） | 决定状态结构、距离计算、全部指标 |
| Q-R2 | 跨维耦合项的数学形式（耦合矩阵？投影？κ 的作用位置） | 决定模型 step 的结构 |
| Q-R3 | 心理抗拒（reactance）：触发条件、反向更新规则、强度参数 θ 的作用方式 | backfire 指标的前提 |
| Q-R4 | 恶意源（adversary）行为模型：固定注入 / 自适应 / 预算约束 / 是否可用 LLM 策略 | 决定 agent 接口需要暴露哪些观测 |
| Q-R5 | 治理策略的作用机制：作为额外邻居发消息？直接扰动目标意见？改边？成本函数如何定义 | 决定 Action 类型与成本计量 |
| Q-R6 | 更新时序：同步 or 异步（随机顺序 / 单个 agent）；意见更新、消息注入、抗拒判定的先后顺序 | HTML 已指出顺序影响模型含义，必须固化并写进 manifest |
| Q-R7 | 终止条件：收敛阈值定义、最大步数、稳态判定方法 | 决定 stop 模块与收敛指标 |
| Q-R8 | 指标精确公式：`extreme`、`joint`（联合极端度）、`backfire` 的定义与归一化方式 | 指标模块与预警分析的全部基础 |
| Q-R9 | 早期预警：极端化事件的 ground truth 如何标注？阈值如何标定？误报如何定义？"提前量"以什么为参照时刻 | R4 能否成立的关键 |
| Q-R10 | 个体差异参数（stubbornness / susceptibility）的分布与是否随时间变化 | 状态 schema |
| Q-R11 | 网络是否共演化（同质性重连） | 决定拓扑模块是否需要动态图支持 |
| Q-R12 | 是否需要噪声项 / 外部媒体场 | 决定 step 是否含随机扰动及复现方式 |

### 6.2 工程与技术决策

| 编号 | 待决策项 | 我的建议（供选择，非定论） |
|---|---|---|
| Q-E1 | **现有 baseline 代码在何处、以何形式交付** | 必须先拿到；否则无法建立回归基线（阻塞 MVP） |
| Q-E2 | MVP 是否包含 Web UI | 建议不含：MVP 只做 CLI + 库 + notebook，UI 放二期 |
| Q-E3 | 存储格式 | 建议 MVP：manifest.json + CSV/NPZ；规模上来后再评估 Parquet/SQLite/DuckDB |
| Q-E4 | 配置形式 | YAML + pydantic 校验 + 版本字段；避免引入 Hydra 等重框架 |
| Q-E5 | 是否引入 Mesa | 建议不引入（自定义调度更可控），但需课题组确认 |
| Q-E6 | 并行策略 | MVP 顺序执行；P1 起用进程池（stdlib multiprocessing） |
| Q-E7 | Rust 触发阈值（扫描可接受时长、目标 N 上限） | 需给出具体数字，才能把 P2 决策门变成可执行规则 |
| Q-E8 | 目标运行环境 | 单机 / 服务器 / 集群？决定并行与并发设计 |
| Q-E9 | 许可证与仓库可见性 | MIT / GPL 家族属治理选择，需课题组定 |
| Q-E10 | 真实网络数据的来源与许可 | 涉及合规，需单独确认；MVP 用合成网络 |
| Q-E11 | 是否使用 experiment tracking 工具（MLflow/W&B） | 建议自研轻量 manifest，避免外部服务依赖 |
| Q-E12 | Python 版本与依赖锁定方式 | 建议 3.12 + uv + uv.lock（与 HTML 方案一致） |

---

## 7. MVP 范围

### 包含

1. 配置 schema + YAML 加载与校验
2. 拓扑：ER / WS / BA（SBM 视工作量）；统一 `GraphView`
3. 状态：N×D 意见 + 基础属性列；三种初始分布
4. 模型：多维 HK（同步更新）+ ModelRegistry；DeGroot 作为第二个注册项（用于协议验证，工作量极小）
5. Agent：L0 内置规则策略 + mock 外部适配器（验证防腐层可用）
6. 引擎：固定步数循环、事件注入点、采样快照、基础终止判定
7. 指标：逐步（意见方差 / 极化 / 极端度 / 聚类数）+ 终态（收敛步数）**[公式待 Q-R8 确认]**
8. 实验：单 run、多种子重复、种子派生、幂等 run_id
9. 存储：产物目录 + manifest.json + CSV/NPZ
10. CLI：`run`、`sweep`、`reproduce`、`info`
11. 测试：见第 9 节

### 不包含

Web UI / API 服务 / LLM 接入 / Rust / 分布式 / 真实数据 / 共演化网络 / 高级统计检验 / 预警标定 / 数据库索引

---

## 8. 目录结构（v1）

```
odin-sim/
├── README.md
├── pyproject.toml                 # 3.12+, uv 管理
├── uv.lock
├── configs/
│   ├── model/hk_2d.yaml
│   ├── topology/{er,ws,ba,sbm}.yaml
│   └── experiments/baseline.yaml
├── src/odin_sim/
│   ├── config/{schema.py,loader.py}
│   ├── topology/{base.py,generators.py,io.py,registry.py}
│   ├── state/{opinion.py,init.py}
│   ├── models/{base.py,hk.py,degroot.py,registry.py}
│   ├── agents/{base.py,builtin/,adapters/}
│   ├── engine/{engine.py,scheduler.py,recorder.py,stop.py}
│   ├── metrics/{base.py,polarization.py,...,registry.py}
│   ├── experiments/{runner.py,sweep.py,seeds.py,manifest.py}
│   ├── storage/{layout.py,artifacts.py}
│   ├── analysis/{aggregate.py,stats.py,plots.py}
│   ├── reproducibility.py
│   └── cli.py
├── tests/{unit/,integration/,regression/,benchmarks/,fixtures/}
├── benchmarks/
├── docs/
│   ├── architecture-v1.md         # 本文件
│   ├── model_spec.md              # 待 Q-R1~R7 确认后写
│   ├── metrics.md                 # 待 Q-R8, R9 确认后写
│   ├── experiment_protocol.md
│   └── reproducibility.md
├── scripts/
├── data/{raw/,processed/}         # gitignore
└── results/                       # gitignore
```

---

## 9. 测试策略

| 层级 | 内容 | 说明 |
|---|---|---|
| 单元 | 拓扑生成器 | 节点/边数、度分布期望、种子确定性、无自环 |
| 单元 | 状态初始化 | 域内约束、形状、可复现 |
| 单元 | 模型数学性质 | HK：意见区间单调不扩张、完全一致输入保持不动、τ 极大时收敛到全局均值、τ→0 时保持不变（解析可验证的小规模用例） |
| 单元 | 指标 | 手算 3–5 节点用例；归一化与边界（全同 / 两极化） |
| 单元 | 种子派生 | 同一 (master_seed, run_idx, config_hash) → 同一 rng 流；不同 run_idx → 不同流 |
| 集成 | 端到端 run | config → artifacts 全链路；产物字段齐全 |
| 复现 | 二次运行一致 | 同种子两次运行（跨进程）结果逐位一致 |
| 复现 | 环境无关 | 不依赖全局 numpy 随机；不依赖字典/集合遍历顺序 |
| 回归 | baseline 对齐 | 与既有 baseline 输出对比，**容差需先定义**（阻塞于 Q-E1） |
| 属性 | hypothesis（可选） | 意见始终在合法域内；无 NaN/Inf；Action 越界被裁剪 |
| 性能 | benchmark（非正确性门槛） | N∈{500,2000,5000} × T 的耗时与内存，输出剖析报告 |
| 契约 | 依赖方向检查 | CI 中静态检查第 4.3 节规则 |
| 一致性 | Python vs Rust（P3） | 双实现数值对齐，容差标准先定义 |

测试纪律：不写"必然通过"的空测试；不伪造运行成功状态；未通过的性能目标如实记录为已知限制。

---

## 10. 分阶段实施计划

| 阶段 | 内容 | 交付物 | 前置 |
|---|---|---|---|
| S0 对齐 | 回答第 6 节问题；取得 baseline；定模型规格与指标定义 | `docs/model_spec.md`、`docs/metrics.md` | 无（**当前阻塞点**） |
| S1 内核 | config / topology / state / models(HK, DeGroot) / metrics 基础 / 单元测试 | 可 import 的库 + 数学性质测试 | S0 |
| S2 引擎与实验 | engine / agents 协议 / experiments / storage / CLI | CLI 可跑单实验并产出可追溯产物 | S1 |
| S3 复现与回归 | 种子派生、manifest、环境指纹、baseline 对齐测试 | 复现性报告 | S1,S2 + baseline |
| S4 扫描与基准 | sweep、进程池并行、benchmarks、性能剖析 | 热点剖析报告 + 首批相图数据 | S3 |
| S5 分析与可视化 | aggregate / stats / plots / notebook 模板 | 分析脚本与图表 | S4 |
| S6 路线决策 | 基于 S4 数据决定是否引入 Rust | 决策记录 | S4 |

工期不做预估：取决于 S0 的确认速度与 baseline 的模块化程度。

---

## 11. 已知风险

1. **baseline 缺失**：无法建立回归基线，重构可能引入未察觉的模型偏差。
2. **更新顺序敏感性**：顺序未固化会导致结果不可比，必须在 S0 写死并进 manifest。
3. **过早抽象**：模型协议若基于臆测的公式设计，S0 后可能返工；因此 S1 只做最小协议。
4. **性能未知**：500–5,000 规模下的实际耗时未测量，任何性能承诺都不成立。
5. **外部 agent 的不确定性和复现性冲突**：LLM agent 即使固定种子也不保证输出一致，需在 manifest 中显式标注复现等级。
