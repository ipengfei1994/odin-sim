# Experiment Config Schema v1.0

**ODIN Experiment Specification v1.0 · 配套文档（实验配置规范）**

| 项 | 内容 |
|---|---|
| 状态 | 待研究者评审（规格，非实现） |
| 日期 | 2026-11-08 |
| 主规范 | [`experiment-specification-v1.md`](experiment-specification-v1.md) §6 |
| 依据 | 《实验指标体系 v2》§第四层（π）、§第五层（控制参数）、§落地最小采集表（采集频率） |
| 本次边界 | 仅文档；未修改 `src/`，未新增运行时依赖，未运行任何实验 |

> **重要**：本文件描述的是**目标配置契约**，不是当前 `SweepSpec` 能直接读取的格式。§1 与 §9 明确列出与现状的差距与迁移关系。

---

## 1. 与当前仓库实现的关系（事实核对）

| 现有实现 | 文件 | 可复用 | 不足 |
|---|---|---|---|
| `SweepSpec`（`name / model / base / axes / seeds / store_trajectory / on_error / control`） | `src/odin_sim/experiments/spec.py` | 确定性展开、`run_id = sha1(canonical(RunSpec))[:16]`、种子必须显式、重复 `run_id` 报错、断点续跑 | `axes` 只允许 `RUN_FIELDS`（mode/placement/beta_gov/tau/scenario）与 `param_overrides.*`；无 `population_size`、拓扑、推荐策略、底座等扫描轴 |
| `ControlSpec` | 同上 | 通用对照变换（字段覆盖 + 同种子 + 适用范围），一条主运行 → 一条对照 | **只支持单一对照**；L2 鲁棒性要求「无干预」与「随机话术」两个对照组，无法表达 |
| `RunSpec.to_dict()` | 同上 | 只含决定数值的字段，`role` / `control_*` 不进 `run_id` | 需要扩展为「决定数值的字段」新集合，且保持既有 G3 指纹回归不变 |
| `reproducibility.snapshot()` | `src/odin_sim/reproducibility.py` | git commit / dirty / Python / 平台 / 依赖版本，缺失写 `unknown` | 无 LLM 底座版本、投影器版本、prompt 模板哈希、复现等级 |
| `manifest.json` | `src/odin_sim/storage/manifest.py` | 自描述、`skipped_metrics`、失败也落盘 | 无 `config_version` / `metric_definition_versions` / `threshold_version` / `community_partition_method` / `reproducibility_level` |
| `PlatformTopology` | `src/odin_sim/topology/model.py` | ba / er / ring / ws / sbm | 边表未持久化（见 `data-schema.md` §1.7） |

**设计原则**：新配置以**追加与并存**方式落地，不替换、不重构现有 `SweepSpec`；既有 G3 / reference 模型的 `traj_hash` 回归必须保持逐字符一致。

---

## 2. `ExperimentConfig` 字段表（任务清单逐项）

| 字段 | 类型 | 必需 | 含义 | v2 / 任务依据 | 当前状态 |
|---|---|---|---|---|---|
| `config_version` | str | 是 | 配置 schema 版本（如 `"odin-experiment-v1"`） | 版本管理 | 缺 |
| `experiment_id` / `name` | str | 是 | 实验标识 | 现有 `name` | 已有 |
| `population_size` | int \| list[int]（可作扫描轴） | 是 | 群体规模；多群体规模与有限尺寸标定 | 任务 §七、v2 §第五层相变四要素 | **缺** |
| `network_topology` | object | 是 | `kind ∈ {er, ws, ba, sbm, ring, imported}`, `params`, `seed`, `homophily`, `signs`, `import_ref` | 多种拓扑 | 部分 |
| `initial_opinion_distribution` | object | 是 | `type ∈ {uniform, normal_truncated, bimodal, file}`, `params`, `dims d`, `rho`, `domain`（域 TBD Q-42） | ρ、d、初始分布 | 部分 |
| `agent_population_config` | object | 是 | 类型混合 `{rule, llm, hybrid}` 数量/比例、`p_AI`、`persona_source`、`bias_injection_config` | 多种 Agent 类型、V-zero、p_AI；**偏误注入是实验因子**（v2 §第一层 Chuang 2024 转述） | **缺** |
| `agent_policy_config` | object | 是 | L0/L1/L2 实现、权限集、超时/降级、批处理粒度 | 多种 Agent 策略 | **缺** |
| `opinion_dynamics_config` | object | 是 | `model_id`（注册表）、`epsilon`、参数覆盖 | 动力学模型可替换 | 部分 |
| `recommendation_policy` | object | 是 | `policy_id`, `alpha_personalization`（**命名空间隔离**）, `candidate_rule`, `ranking_rule`, `forced_exposure` | 多种推荐策略、A_ij、选择性接触 | **缺** |
| `content_generation_config` | object | 是 | 生成策略、话术库、judge、文本留存、议题维度 | 内容生成 | **缺** |
| `intervention_config` | object | 是 | `pi_omega / pi_tau / pi_kappa`、`script_library`、`ethics_boundary` | π = (ω, τ, κ)（v2 §第四层） | 部分（现有 G3 概念不同义） |
| `random_seed` | object | 是 | `master_seed`、`derivation`（现有 `seeds` 列表 / `{start,count,step}`）、`replicates`（v2 建议 100 或 ≥10–25，未锁定 Q-38） | 多随机种子、复现 | **已有**（`seeds`） |
| `simulation_steps` | int | 是 | 步数 | 实验配置 | 部分（模型内部） |
| `sampling_interval` | object | 是 | `structural_k`（3A 每 k 轮，**无默认值，Q-37**）、`state`、`behavior="every_tick"`、`exposure="every_tick"` | v2 采集频率 | **缺** |
| `metrics_config` | object | 是 | `enabled_metric_ids[]`、`definition_versions{}`、`threshold_version`、`statistics`（U/FDR/d/BF01，建议值未锁定） | 指标计算 | 部分（`metrics` 名称列表） |
| `model_metadata` | object | 是 | `backend_id`, `version`, `temperature T`, `prompt_template_hash`, `projector_version` | 不同 LLM 底座比较、模型版本漂移 | **缺** |
| `community_partition_method` | str | 是 | 社区划分口径（Piao 先验划分 / Louvain / 自定义），**v2 methods 声明 1** | 3A 全组可比性 | **缺** |
| `sweep` | object | 否 | `axes{}`、`pairing`、`controls[]`、`scan_direction`（正/反向） | 参数扫描、配对、对照组、迟滞 | 部分 |
| `on_error` / `store_trajectory` | enum / bool | 否 | 沿用现有语义 | 现有配置 | 已有 |

### 2.1 字段校验规则

1. 未知字段一律报错，不静默忽略（沿用 `SweepSpec.from_dict`）。
2. 种子、扫描轴、`sampling_interval.structural_k` **必须显式给出，平台不提供默认值**（沿用「平台不代研究猜配置」纪律）。
3. 所有阈值字段在未锁定时写 `threshold_version: "unlocked"`；此时 `metrics_config` 禁止启用 `pass/fail` 类输出。
4. `run_id` 只由「决定数值的字段」构成；`name`、角色、对照标注、指标定义版本**不进** `run_id`。
5. `population_size` 作为扫描轴时，每个 N 必须有相同种子集合（有限尺寸标定可比性）。

---

## 3. 必须支持的实验形态与表达方式

| 形态 | 配置表达 | 现有机制 |
|---|---|---|
| 多种网络拓扑 | `sweep.axes["network_topology.kind"] = ["er","ws","ba","sbm"]` | 需扩展 `axes` 白名单 |
| 多种 Agent 类型 | `sweep.axes["agent_population_config.mix"] = [...]` | 否 |
| 多种推荐策略 | `sweep.axes["recommendation_policy.policy_id"] = [...]` | 否 |
| 参数扫描 | `sweep.axes` 笛卡尔积，展开顺序按声明顺序 | **已有** |
| 多随机种子 | `random_seed.derivation` | **已有** |
| 配对实验 | `sweep.pairing = {"same_seed": true, "pairing_key": [...]}` | 部分（`ControlSpec` 同种子） |
| 对照组 | `sweep.controls = ["no_intervention", "random_script"]`（**两个对照组**，G-L2 要求） | 部分（单对照） |
| 多群体规模 | `sweep.axes["population_size"] = [500, 1000, 2000]` | 否 |
| 不同 LLM 底座比较 | `sweep.axes["model_metadata.backend_id"] = [...]` + 结果按底座分层、禁止跨底座合并 | 否 |
| 正反向扫描（迟滞） | `sweep.scan_direction = "forward_and_reverse"` | 否 |

---

## 4. 符号命名空间隔离（Q-31，**阻塞级**）

v2 与现有 G3 baseline 存在同名异义，配置必须用带前缀的键隔离：

| 符号 | 《实验指标体系 v2》含义 | G3 baseline 现有含义 | 配置键约定 |
|---|---|---|---|
| `ω` | 介入频率（每 k 轮） | 无 | `intervention_config.pi_omega` |
| `τ` | **目标节点**（中心/桥/边缘/随机） | **预热步数**（`tau=30/60`） | `intervention_config.pi_tau` vs 旧 `tau` |
| `κ` | **话术策略** | **跨维耦合系数** | `intervention_config.pi_kappa` vs `dynamics.kappa_coupling` |
| `α` | **推荐个性化** | **说服学习率** | `recommendation_policy.alpha_personalization` vs `dynamics.alpha_persuasion` |
| `ρ` | 议题相关（控制参数） | 议题相关系数（语义一致） | `initial_opinion_distribution.rho`；与 `M5-rho-obs` 分列 |
| `d` | 维度数 | 维度数 | `initial_opinion_distribution.dims` |
| `ε` | 置信阈值 | 置信阈值 | `opinion_dynamics_config.epsilon` |
| `T` | LLM 温度 | 无 | `model_metadata.temperature` |

**约束**：禁止把 v2 的 `τ/κ/α` 直接映射到现有 `RunRequest` 的 `tau` 或 `param_overrides` 中的同名键；旧键保持原语义，以保住既有 G3 指纹回归（第 13 章 T-L4/2）。

---

## 5. 配置示例（**规格示例，非可执行配置，未运行**）

```json
{
  "config_version": "odin-experiment-v1",
  "name": "g3-online-social-network-pilot",
  "population_size": [500, 1000],
  "network_topology": {
    "kind": "ba",
    "params": {"m": 3},
    "seed": 20261108,
    "homophily": 0.0
  },
  "initial_opinion_distribution": {
    "type": "bimodal",
    "dims": 2,
    "rho": 0.6,
    "domain": "TBD_Q42"
  },
  "agent_population_config": {
    "mix": {"rule": 0.85, "llm": 0.15},
    "p_AI": 0.15,
    "bias_injection_config": {"enabled": true, "factor_id": "confirmation_bias_v1"}
  },
  "agent_policy_config": {
    "level": "L2",
    "allowed_actions": ["post", "noop", "comment", "like", "reshare"],
    "timeout_ms": 3000,
    "degrade_to": "noop"
  },
  "opinion_dynamics_config": {
    "model_id": "g3-baseline-v1",
    "epsilon": 0.35
  },
  "recommendation_policy": {
    "policy_id": "chronological",
    "alpha_personalization": 0.0,
    "candidate_rule": "all_followees",
    "ranking_rule": "recency",
    "forced_exposure": false
  },
  "content_generation_config": {
    "text_retention": true,
    "script_library": "v1",
    "judge": "human_sample"
  },
  "intervention_config": {
    "pi_omega": 10,
    "pi_tau": "bridge",
    "pi_kappa": "cross_cutting",
    "ethics_boundary": "no_depolarization_for_discriminatory_stances"
  },
  "random_seed": {
    "master_seed": 42,
    "derivation": {"start": 1, "count": 25, "step": 1},
    "replicates": 25
  },
  "simulation_steps": 150,
  "sampling_interval": {
    "structural_k": 10,
    "state": "every_tick",
    "behavior": "every_tick",
    "exposure": "every_tick"
  },
  "community_partition_method": "piao_prior_left_right_neutral_by_tie_strength",
  "metrics_config": {
    "enabled_metric_ids": ["3A-Q", "3B-cascade-size", "3C-engagement-rate", "3D-E-ij"],
    "definition_versions": {},
    "threshold_version": "unlocked",
    "statistics": {"test": "mann_whitney_u", "correction": "fdr", "effect_size": "cohens_d"}
  },
  "model_metadata": {
    "backend_id": ["backend-A", "backend-B"],
    "temperature": 0.7,
    "prompt_template_hash": "TBD_HASH",
    "projector_version": "projector-A-v1"
  },
  "sweep": {
    "axes": {
      "network_topology.kind": ["er", "ws", "ba", "sbm"],
      "recommendation_policy.policy_id": ["chronological", "engagement_rank"],
      "intervention_config.pi_omega": [5, 10]
    },
    "pairing": {"same_seed": true},
    "controls": ["no_intervention", "random_script"],
    "scan_direction": "forward"
  },
  "on_error": "abort",
  "store_trajectory": true
}
```

> 示例中的 `structural_k = 10`、`replicates = 25`、`temperature = 0.7` 等**仅为展示字段形状的占位值**，不是建议阈值，不代表研究者已确认（Q-37 / Q-38）。`"TBD_Q42"` 与 `"TBD_HASH"` 表示定义未锁定。

---

## 6. 版本管理

| 版本对象 | 写入位置 | 规则 |
|---|---|---|
| `config_version` | 配置根 + manifest | 未知版本拒绝加载 |
| `metric_definition_versions` | `metrics_config` + manifest + `metrics.json` | 同一 `metric_id` 不同定义版本可共存，产物记录所用版本，**禁止覆盖式升级**（沿用注册表「重复注册报错」纪律） |
| `threshold_version` | `metrics_config` + manifest | 未锁定时写 `"unlocked"`；此状态下禁止 `pass/fail` 输出 |
| `event_schema_version` | 每个事件 payload | 事件流按版本可回放 |
| `model_metadata.version` / `projector_version` | manifest + 每次 LLM 调用 | 跨版本结果分层，禁止合并 |
| `community_partition_method` | 配置 + manifest + 指标产物 | v2 methods 声明 1：跨研究比较前必须对齐 |
| `git_commit` / `git_dirty` / 依赖版本 | `environment` | 沿用 `reproducibility.snapshot()`；缺失写 `unknown`，不静默省略 |

**升级策略**：新增字段以「可选 + 显式默认 `unknown`」方式引入；任何改变既有数值语义的变更必须提升 `config_version`，并以新 `run_id` 空间运行，不得复用旧产物目录。

---

## 7. 复现要求

1. **同配置 + 同种子 → 同结果**：`run_id` 内容寻址只由决定数值的字段构成；`traj_hash` / `network_hash` 逐字符一致（沿用 `verification.py` 纪律）；事件流逐条一致（T-REPRO/1、T-REPRO/2）。
2. **续跑判定**沿用现有规则：规格一致 + `status=ok` + 产物存在；**不看环境**（换机器重跑不判失效，环境差异记录在 manifest）。
3. **自管理 RNG**：不依赖全局随机源；每个 `Simulation Runtime` 派生独立流，`random_seed_context` 记录流 ID 与抽样序号。
4. **复现等级显式标注**：`reproducibility_level ∈ {bitwise, statistical, not_reproducible}`；含 LLM 节点时不得默认声称逐位复现（Q-41）。
5. **每个产物可回答七问**：配置快照、种子派生路径、模型/底座版本、投影器版本、社区划分口径、指标定义版本、环境指纹。
6. **失败也落盘**：`status=error` + 错误信息，不留「看起来跑过」的空目录。
7. **配对与对照口径由配置给出，平台不内置**：沿用 `ControlSpec` 的设计哲学；「无治理」与「治理失效」是不同对照，取决于研究问题。

---

## 8. 采集频率在配置中的落点（严格服从 v2）

| v2 规定 | 配置字段 | 值 |
|---|---|---|
| 3A：每 k 轮采样 | `sampling_interval.structural_k` | **必填，无默认值**（Q-37） |
| 3B：事件驱动，每条内容独立记录 | 事件系统（非采样字段） | `ContentCreated/Published/Exposed` + `InteractionCreated`，按 `content_id` 聚合 |
| 3C：每轮全量 | `sampling_interval.behavior` | `"every_tick"` |
| 3D：每轮全量 | `sampling_interval.exposure` | `"every_tick"`（**过程量实时，禁止事后反推**） |
| 其他层 | `sampling_interval.state` 等 | 工程推导，配置中标注为工程口径，不与 v2 混写 |

---

## 9. 迁移映射（现状 → 目标，**建议，不实施**）

| 目标字段 | 现有承载 | 迁移方式 |
|---|---|---|
| `random_seed.derivation` | `SweepSpec.seeds` | 直接复用 |
| `sweep.axes` 的既有键 | `RUN_FIELDS` + `param_overrides.*` | 保留；新增轴走白名单扩展，不改旧键语义 |
| `sweep.pairing` / `controls[0]` | `ControlSpec` | 保留单对照机制；第二对照组为新增能力 |
| `run_id` | `config_fingerprint(RunSpec.to_dict())` | 扩展决定数值的字段集合，保持旧 run 的哈希不变（旧字段值不变） |
| `environment` | `reproducibility.snapshot()` | 追加 `extra`：底座版本、投影器版本、指标定义版本、复现等级 |
| `skipped_metrics` | `storage/manifest.py` | 直接复用，扩展 reason 枚举（TBD / 阈值未锁定 / 缺事件流） |
| 其余新增字段 | 无 | 以 `ExperimentConfig` 新层承载，经适配器转换为 `RunRequest`；**不修改 `RunRequest` 既有字段** |

---

## 10. 验收清单（供后续开发对照，本次不执行）

1. 未知字段报错；种子、`structural_k` 无默认值。
2. `run_id` 不含角色/对照标注/指标定义版本；同配置换目录重跑命中同一批 `run_id`。
3. `population_size`、拓扑、推荐策略、底座四类扫描轴可展开且顺序确定。
4. 双对照组可表达；配对保持同种子。
5. Q-31 符号隔离通过 T-L4/2，且既有 G3 指纹回归逐字符一致。
6. `threshold_version="unlocked"` 时 `pass/fail` 类指标被跳过并写 `skipped_metrics`。
7. manifest 可回答 §7.5 的七个问题；缺失信息写 `unknown` 而非省略。
8. 本次工作未创建任何配置文件、未修改 `src/`、未运行实验。

---

*文档结束 · 状态：待研究者评审 · 未进行任何代码实现*
