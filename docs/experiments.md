# 实验层设计说明（W2 续）

- 日期：2026-10-28
- 状态：**已实现并通过测试**，其中若干口径为**待确认决策**（下方标注）
- 涉及代码：`src/odin_sim/experiments/`、`src/odin_sim/storage/manifest.py`、CLI `sweep`

---

## 1. 目标与范围

实验层把"单次运行"扩成"配置 × 种子"的批量实验，并保证可复现、可断点续跑。
它**不持有任何数值逻辑**：调度 `engine.run_once`，数值由已注册的动力学模型负责。

| 能力 | 状态 |
|---|---|
| 配置 → 运行列表（确定性展开） | 已实现 |
| 幂等落盘 + 断点续跑 | 已实现 |
| 单次运行 manifest（规格/模型/环境/产物/状态） | 已实现 |
| sweep 汇总表 `results.csv` 与 `sweep_manifest.json` | 已实现 |
| 不需要对照的指标计算 | 已实现 |
| 需要同种子对照的指标（如反噬） | **已实现**：通过可配置 `ControlSpec`，口径由实验配置给定 |
| 结果分析层（跨条件聚合 + 置信区间） | **已实现**：`analyze` 子命令，`odin_sim.analysis.aggregate`（仅 numpy） |
| 结果可视化（曲线 / 柱状 / 相图热图） | **已实现**：`plot` 子命令，`odin_sim.analysis.figures`（依赖 `[analysis]` 可选组） |

## 2. 产物结构

```text
<out_root>/
├── sweep_manifest.json    本次 sweep 的规格、指纹、计数与环境快照
├── results.csv            汇总表（行序 = 展开顺序）
└── runs/
    └── <run_id>/
        ├── manifest.json      单次运行自描述
        └── trajectory.npz     仅当 store_trajectory=true
```

`run_id = sha1(canonical(RunSpec))[:16]`，只由运行规格决定，与输出目录、执行
时间、执行次数无关。因此换目录重跑会命中同一批 `run_id`，天然支持续跑。

## 3. 配置 schema（JSON）

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | sweep 名称，只用于标识 |
| `model` | 是 | 注册表中的模型名 |
| `base` | 是 | 未扫描字段的取值；**必须显式给出 `mode`** |
| `axes` | 否 | 扫描轴，键为 `mode/placement/beta_gov/tau/scenario` 或 `param_overrides.<参数名>` |
| `seeds` | 是 | 列表，或 `{start, count, step}` |
| `store_trajectory` | 否 | 默认 `false` |
| `on_error` | 否 | `abort`（默认）或 `continue` |
| `control` | 否 | 对照口径：从主运行派生对照运行的**通用变换规则**（见下） |

未知字段一律报错，不静默忽略。

### 3.1 `control` 配置 schema（对照配对）

平台**不内置任何对照口径**——"什么是对照"是研究的判断，写在配置里随实验走。
`control` 只提供通用的"字段覆盖 + 种子策略 + 适用范围"变换机制：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 否 | 口径标识，原样写进 manifest，便于事后回答"这批结果用的是哪种对照" |
| `overrides` | **是** | 施加到主运行上的字段覆盖。键必须是 `mode/placement/beta_gov/tau/scenario` 之一，或 `param_overrides.<参数名>`；**不能覆盖 seed** | 
| `seed` | 否 | `"same"`（默认，与主运行同种子）或显式整数 |
| `apply_to` | 否 | 适用范围白名单，键为 `mode/placement/beta_gov/tau/scenario`，值为允许的取值列表；空 = 所有主运行都需对照 |

展开时：对照运行排在**前面**（先执行，主运行才能在内存取到配对轨迹），同一条对照只生成一次、被多个主运行共享；当某主运行与派生出的对照规格完全相同（自指）或对照与主运行轴中的某条冲突时，直接报错而非静默复用。

主运行的 `control_status` 记录配对关系：`none`（未配置/超出 `apply_to`）、`self`（对照变换后与自身相同，配对量无定义）、`linked`（已关联到 `control_run_id`）。需要对照的指标按此状态决定"计算"或"跳过并记录原因"。

合法示例：

```json
{
  "control": {
    "name": "no-governance",
    "overrides": {"mode": "none"},
    "seed": "same"
  }
}
```

## 4. 关键决策与理由

| 编号 | 决策 | 理由 | 状态 |
|---|---|---|---|
| D-1 | 展开顺序：轴（声明顺序）在外、种子在内 | 行序本身是可复现的一部分；同配置的重复样本相邻，便于聚合 | 已实现 |
| D-2 | 重复 `run_id` **直接报错**，不静默去重 | 重复通常意味着配置写错；去重会把"少跑了实验"伪装成"跑完了" | 已实现 |
| D-3 | 种子必须显式给出，平台不提供默认值 | 种子是研究配置，不是工程默认值 | 已实现 |
| D-4 | 续跑判定只看"规格一致 + 状态 ok + 产物存在"，**不看环境** | 换机器重跑时环境不同是事实，但不应因此把已完成结果判为失效；环境差异记录在 manifest 里 | 已实现 |
| D-5 | 轨迹默认不落盘 | 500 节点 × 150 步 × 2 维的单次轨迹约 1.2 MB，扫描组合下体积增长快；默认只存哈希与序列末值 | 已实现，阈值未标定 |
| D-6 | 需要对照的指标（`requires_control=True`）**配对时计算、缺失对照时跳过并记录** | 反噬是与同种子无治理对照的配对量；缺失对照（未配置 `control`、超出 `apply_to`、`self` 自指、对照轨迹不可用）时退化为绝对值判定会产出虚假结论，故显式跳过并写 `skipped_metrics` | 已实现（机制见 §3.1） |
| D-9 | 对照口径**由配置给出，平台不内置** | "无治理"与"治理失效"是不同的对照，取决于研究问题；平台提供通用变换机制（`overrides`+`seed`+`apply_to`）与记录，口径随实验走 | 已实现 |
| D-7 | 失败也写 manifest（`status=error`） | 不留"看起来跑过"的空目录；下次续跑会重试 | 已实现 |
| D-8 | `on_error` 默认 `abort` | 科学正确性优先：一次失败可能意味着实现或配置有问题，静默继续会污染整批结果 | 已实现 |
| D-10 | 跨条件聚合与画图**默认只看 `role=main`**；要含对照需显式 `--role all` | 对照是配对基线而非实验条件。配了 `control` 的 sweep 会把对照行也写进 results.csv，混进来既会平移均值，也会在数据重合处造成重复计数、把 CI 虚假收窄（伪重复）。实测见 §6 | 已实现 |

## 5. 接口变更（需要知会）

`RunOutput` 新增 `params: dict` 字段——模型实际使用的参数（场景参数解析后的
结果）。原因：指标口径依赖阈值等参数，而参数由模型解析；实验层若自己再猜一份，
就会出现"指标用的是另一套参数"的隐患。模型不提供时为空字典，指标应自行处理
缺失（当前实现：跳过该指标并记录原因）。

该字段是**追加**的，不改变既有字段语义；已由现有回归测试覆盖。

## 6. 验证

| 检查 | 结果 |
|---|---|
| 单元测试（规格展开 + 假模型调度） | 26 passed |
| G3 回归：实验层轨迹哈希 vs 直接调引擎 | 逐位一致 |
| G3 回归：重跑同一 sweep | `executed=0, skipped=N`，汇总行哈希不变 |
| G3 回归：`requires_control` 指标（未配置 control） | 被跳过且写入 `skipped_metrics`，未出现在结果表 |
| G3 回归：配置 `control`（mode=none 同种子）后 `g3.backfire` | 主运行真正配对计算；对照运行自身不计算；对照轨迹强制落盘 |
| 单元测试：对照配对机制（假模型，13 passed） | 覆盖派生/种子/适用范围/自指/重跑复用 |
| CLI 端到端（2 条运行 + 指标） | 首次 executed=2；复跑 skipped=2 |
| 单元测试：分析层 7 passed + 可视化层 17 passed | 数值/发生率口径、角色过滤、图上取值等于聚合值（不重算 CI）、缺数据时报错而非画空图 |
| G3 端到端：β_gov(3)×tau(2)×种子(3)×对照 = 36 run → 三种图 | 曲线（CI 带）/柱状（发生率）/相图热图均产出，标注与 colorbar 正常 |
| G3 实测：`--role main` vs `--role all` | β_gov=0.1 均值 0.495→0.601、std 0.038→0.114；β_gov=0.0 均值不变但 CI 宽度 0.031→0.018（重复计数造成的虚假收窄） |

## 7. 限制与下一步

1. **对照配对已实现（通用机制）**：`g3.backfire` 一类指标在配置 `control` 后配对计算；
   口径（如 `mode=none` 同种子 vs `beta_gov=0` 同种子）由实验配置决定，平台不内置。
   注意：跨 sweep 复用对照轨迹需开启 `store_trajectory`（或保证对照运行已落盘）。
2. **无并行**：当前为顺序执行。`patched_baseline_topology` 是进程全局注入，
   并行会互相干扰，因此并行化必须先解决拓扑注入的隔离问题。
3. **结果分析层 v1 已实现**：跨条件聚合与置信区间（`analyze` 子命令，纯 numpy，
   不强制 `[analysis]` 依赖；CI 默认正态近似，装了 scipy 自动切 t 区间）。
4. **结果可视化 v1 已实现**：曲线（CI 带）/ 柱状（误差棒）/ 相图热图，`plot` 子命令，
   依赖 `[analysis]` 可选组；渲染层只读聚合结果，不重算统计量。
5. **无时间序列图**：`trajectory.npz` 只存 `traj/is_adv/is_gov`，中间序列未落盘。
   要把"随时间演化"的曲线画出来，得先定义"对 traj 取哪个摘要"——这是模型相关的
   研究口径，平台不替模型编造，故暂未提供。
6. **无并行**：当前为顺序执行。`patched_baseline_topology` 是进程全局注入，
   并行会互相干扰，因此并行化必须先解决拓扑注入的隔离问题。
7. **未覆盖失败重试策略**：失败只记录，不自动重试。
8. **未做大规模性能测量**：单条耗时已写入 `wall_seconds`，但尚未做规模扫描。

下一步建议：**性能与并行**（先把 `patched_baseline_topology` 的进程全局注入隔离掉），
或与课题组确认时间序列摘要口径后再补序列落盘。

## 8. 分析层用法

`analyze` 消费一次 sweep 产出的 `results.csv`，按分组键聚合数值指标并给出置信区间。
不读模型、不跑仿真，是 `results.csv` 的纯函数，可复现。核心仅依赖 numpy；
CI 默认正态近似（95% 对应 1.96×SEM），若环境装了 scipy 自动切换为 t 分布区间。

```bash
# 按 mode + beta_gov 分组，聚合反噬指标，打印到 stdout（CSV 格式）
odin-sim analyze --sweep results \
    --group-by mode beta_gov \
    --metric g3.backfire.bf_final

# 输出到文件，多指标、自定义置信度
odin-sim analyze --sweep results \
    --group-by topology.type scenario \
    --metric g3.backfire.bf_final ord_extreme_final \
    --confidence 0.95 --out agg.csv
```

输出每行一个分组，含分组键、`n`（组内总 run 数），以及每个指标的统计量：

- **数值指标**：`<metric>.n / .mean / .std / .sem / .ci_low / .ci_high`
  （`.n` 为该组内数值样本数）。
- **布尔指标**（如是否反噬）：自动聚合成**发生率**
  `<metric>.n / .n_true / .rate / .rate_ci_low / .rate_ci_high`
  （`.n` 为该组内布尔样本数，只统计主运行等有值的行，对照行的空值不计入）。

非数值且非布尔（纯文本）或全缺失的列，在该组记 `n=0` 且统计量为空，绝不编造。

**角色过滤（重要）**：配了 `control` 的 sweep 会把对照运行也写进 `results.csv`
（`role=control`）。`analyze` / `plot` **默认只聚合 `role=main`**，要含对照必须显式
`--role all`，要只看对照用 `--role control`（理由见 D-10 与 §6 实测）。
没有 `role` 列的老产物原样返回，不报错——那时本就无从区分。

## 9. 可视化用法

`plot` 先按给定维度聚合并取 CI，再把**聚合好的值**画出来。渲染层不做任何统计推断，
因此图上的点与区间必然等于 `analyze` 表里的 `<metric>.mean / .ci_low / .ci_high`
（发生率类指标走 `.rate / .rate_ci_*`），图与表不会各说一套。

依赖 matplotlib（属 `[analysis]` 可选组）：`uv sync --extra analysis`。
未安装时给出明确引导，而不是在 import 期崩掉——免得没装可选依赖的人连纯 numpy 的
`analyze` 都用不了。

```bash
# 曲线：横轴 beta_gov，纵轴极端化末值，按 tau 分组，阴影是 95% CI
odin-sim plot --sweep results --x beta_gov --metric all_extreme_final --hue tau

# 柱状：反噬发生率（布尔指标自动折算成率），误差棒取自 CI（非对称）
odin-sim plot --sweep results --x beta_gov --metric g3.backfire.bf_final \
    --kind bar --hue tau

# 相图：两参数张成平面，单元格颜色是聚合值，缺失组合留白不插值
odin-sim plot --sweep results --x beta_gov --heatmap-y tau \
    --metric all_extreme_final --kind heatmap
```

- `--group-by` 默认由 `--x` / `--hue` / `--heatmap-y` 推导，可显式覆盖。
- 未知列名一律报错并列出可用列，不静默画出一张空图。
- 某组点估计全缺时跳过该组；全部缺失时直接报错，不产出无数据的坐标系。
- `--out` 不指定时写到 `<sweep>/figures/<metric>_<kind>.png`。
- 默认不使用中文字体（Linux CI 通常没有中文字体，硬写会渲染成豆腐块）：
  轴标签取列名，`--title` 由调用方自传。
