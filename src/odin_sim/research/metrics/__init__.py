"""S4｜独立研究指标扩展包（不注册进核心 ``model.registry.METRICS``）。

门控纪律（对应主规范 §8.2 与评审清单 S0）
------------------------------------------
1. ``DefinitionVersion.metric_def_version == metric_def_unset``（S0 未完成）
   → **全部 75 个指标 skip**，只落盘原始观测量。
2. v2 中 ``mathematical_definition`` 为 TBD 的 43 条，未经研究者以
   ``register_definition()`` 显式登记公式 → skip。
3. 输入数据缺失 → skip 并写明缺什么，绝不用可推导数据造一个近似值。
4. 阈值未锁定 → 只报观测值，禁止出现 pass/fail 布尔字段。

被 skip 时 ``MetricOutcome.value`` 必须为 ``None``，并写 ``skipped_metrics``；
这既符合既有 ``experiments.runner`` 的跳过语义，也满足 T-L1/2 的断言。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..config import DEFINITION_UNSET, ExperimentConfig
from .definitions import LAYERS, METRIC_IDS, METRIC_ROWS, TBD_METRIC_IDS
from .compute import observables

__all__ = [
    "METRIC_IDS", "TBD_METRIC_IDS", "LAYERS", "METRIC_ROWS",
    "MetricDefinition", "MetricInput", "MetricOutcome", "DefinitionRegistry",
    "register_definition", "compute_all", "observables", "SkipReason",
]


class SkipReason:
    """标准化跳过原因（写入 skipped_metrics，便于机器统计）。"""

    S0_UNSET = "S0 未完成：metric_def_unset，禁止产出任何 v2 指标数值"
    TBD = "v2 数学定义为 TBD，Open Questions 未锁定，不得由工程侧代填"
    NO_DEFINITION = "该 metric_id 未登记定义版本，指标层不提供默认公式"
    VERSION_MISMATCH = "登记的定义版本与本次实验的 metric_def_version 不一致"
    MISSING_INPUT = "计算所需输入数据缺失"
    THRESHOLD_UNLOCKED = "达标线未锁定，只报观测值，不报 pass/fail"


@dataclass(frozen=True)
class MetricDefinition:
    """一条经研究者确认的指标定义（S0 产物）。

    ``source`` 必须指向研究者的书面结论；``version`` 必须与实验配置的
    ``metric_def_version`` 一致才允许计算。
    """

    metric_id: str
    version: str
    formula_id: str
    required_inputs: tuple[str, ...]
    compute: Callable[["MetricInput"], float | dict[str, float]]
    source: str
    validation_test: str = ""

    def __post_init__(self) -> None:
        if self.metric_id not in METRIC_IDS:
            raise ValueError(f"未登记的 metric_id：{self.metric_id}")
        if self.version == DEFINITION_UNSET:
            raise ValueError("定义版本不能是 metric_def_unset")
        if not self.source:
            raise ValueError("定义必须写明研究者书面结论来源 source")


@dataclass(frozen=True)
class MetricInput:
    """指标计算的唯一输入容器（只读，指标不得回写状态）。"""

    config: ExperimentConfig
    records: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    graphs: Mapping[str, Any] = field(default_factory=dict)
    extras: Mapping[str, Any] = field(default_factory=dict)

    def has(self, *keys: str) -> tuple[bool, tuple[str, ...]]:
        # "config" 是容器自带的必填字段，供配置回显类指标作输入引用；恒可用。
        missing = tuple(
            k for k in keys
            if k != "config"
            and k not in self.graphs and k not in self.extras
            and not self.records.get(k)
        )
        return (not missing, missing)


@dataclass(frozen=True)
class MetricOutcome:
    metric_id: str
    status: str
    """computed / skipped。"""
    value: float | dict[str, float] | None = None
    reason: str = ""
    definition_version: str = ""
    input_refs: tuple[str, ...] = ()

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "status": self.status,
            "value": self.value,
            "reason": self.reason,
            "definition_version": self.definition_version,
            "input_refs": list(self.input_refs),
        }


class DefinitionRegistry:
    """研究指标定义注册表。**默认空**：工程侧不预置任何科学口径。"""

    def __init__(self, *, load_examples: bool = False) -> None:
        self._items: dict[str, MetricDefinition] = {}
        if load_examples:
            for definition in EXAMPLE_DEFINITIONS:
                self.register(definition)

    def register(self, definition: MetricDefinition) -> None:
        if definition.metric_id in self._items:
            raise ValueError(
                f"重复登记指标定义：{definition.metric_id}。"
                "版本变更请用新 version 并显式替换，避免 silently 改口径"
            )
        self._items[definition.metric_id] = definition

    def get(self, metric_id: str) -> MetricDefinition | None:
        return self._items.get(metric_id)

    def names(self) -> list[str]:
        return sorted(self._items)

    def compute(self, metric_id: str, inp: MetricInput) -> MetricOutcome:
        config = inp.config
        version = config.definition_version.metric_def_version

        if version == DEFINITION_UNSET:
            return MetricOutcome(metric_id, "skipped", reason=SkipReason.S0_UNSET,
                                 definition_version=version)
        definition = self._items.get(metric_id)
        if definition is None:
            reason = SkipReason.TBD if metric_id in TBD_METRIC_IDS else SkipReason.NO_DEFINITION
            return MetricOutcome(metric_id, "skipped", reason=reason,
                                 definition_version=version)
        if definition.version != version:
            return MetricOutcome(metric_id, "skipped", reason=SkipReason.VERSION_MISMATCH,
                                 definition_version=version)
        ok, missing = inp.has(*definition.required_inputs)
        if not ok:
            return MetricOutcome(
                metric_id, "skipped",
                reason=f"{SkipReason.MISSING_INPUT}：{list(missing)}",
                definition_version=version,
                input_refs=definition.required_inputs,
            )
        value = definition.compute(inp)
        return MetricOutcome(
            metric_id, "computed", value=value, reason="",
            definition_version=version,
            input_refs=definition.required_inputs,
        )


_DEFAULT_REGISTRY = DefinitionRegistry()


def register_definition(definition: MetricDefinition) -> None:
    """S0 后登记研究者确认的公式（进程级；重启需重新登记或改为配置驱动）。"""
    _DEFAULT_REGISTRY.register(definition)


def compute_all(
    inp: MetricInput,
    *,
    registry: DefinitionRegistry | None = None,
    metric_ids: Sequence[str] | None = None,
) -> list[MetricOutcome]:
    """对全部（或指定）指标给出结果；**每个 metric_id 必有且仅有一条结果**。"""
    reg = registry or _DEFAULT_REGISTRY
    ids = list(metric_ids) if metric_ids is not None else list(METRIC_IDS)
    unknown = [m for m in ids if m not in METRIC_IDS]
    if unknown:
        raise ValueError(f"未知 metric_id：{unknown}")
    return [reg.compute(mid, inp) for mid in ids]


# ---------------------------------------------------------------------------
# 示例定义：只用于验证计算链路（T-L2/1 等），**不代表 v2 口径已锁定**。
# 默认不启用；测试用 DefinitionRegistry(load_examples=True) 显式加载。
# ---------------------------------------------------------------------------
def _example_pairwise_distance(inp: MetricInput) -> float:
    from .compute import pairwise_distance

    snapshots = inp.records.get("agent_snapshots") or ()
    final_tick = max((s.tick for s in snapshots), default=-1)
    final = [s.opinion_true[0] for s in snapshots
             if s.tick == final_tick and s.opinion_true]
    norm = str(inp.config.extras.get("opinion_norm", ""))
    return pairwise_distance(final, norm=norm)


def _example_control_epsilon(inp: MetricInput) -> float:
    return float(inp.config.opinion_dynamics_config.get(
        "ctrl_epsilon", inp.config.opinion_dynamics_config.get("epsilon", float("nan"))
    ))


EXAMPLE_DEFINITIONS = (
    MetricDefinition(
        metric_id="M0-D", version="example-v0", formula_id="v2-R3-pairwise-D",
        required_inputs=("agent_snapshots",),
        compute=_example_pairwise_distance,
        source="v2 规则三给出公式；norm 由配置显式指定（示例，非锁定口径）",
        validation_test="T-L2/1",
    ),
    MetricDefinition(
        metric_id="P-ctrl-epsilon", version="example-v0",
        formula_id="config-echo", required_inputs=("config",),
        compute=_example_control_epsilon,
        source="控制参数回显（示例，非锁定口径）",
        validation_test="T-L5/8",
    ),
)
