"""S1｜实验配置规范实现（ExperimentConfig v1）。

与核心层的关系
--------------
**不修改** ``experiments.spec.RunSpec`` / ``SweepSpec``：``RunSpec.to_dict()``
决定 ``run_id``，改一个键就会让既有 G3 指纹回归整体失效。本模块是并存的
研究层配置，核心 sweep 仍由 ``ExperimentConfig.to_core_sweep()`` 显式生成。

符号命名空间隔离（Q-31，阻塞级）
--------------------------------
v2 的 ``τ``=目标节点、``κ``=话术策略、``α``=推荐个性化；既有 G3 baseline 的
``tau``=预热步数、``kappa``=跨维耦合、``alpha``=说服学习率。同名异义。
本模块强制：研究层干预/推荐配置**只允许带前缀的键**，裸 ``tau/kappa/alpha``
一律拒绝；G3 侧字段保持原样，两侧不共享命名空间。

阈值与定义版本
--------------
所有 v2 达标线保持「建议值 · 未锁定」。``DefinitionVersion`` 记录定义版本；
未登记（S0 未完成）时，指标层必须跳过计算。配置层不代研究者锁定任何口径。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from ..experiments.spec import SweepSpec
from ..reproducibility import config_fingerprint

__all__ = [
    "CONFIG_VERSION",
    "DEFINITION_UNSET",
    "REPRO_LEVELS",
    "COMMUNITY_PARTITION_METHODS",
    "GOVERNANCE_PREFIX",
    "CONTROL_PREFIX",
    "RECOMMENDATION_PREFIX",
    "RESERVED_BARE_SYMBOLS",
    "ConfigError",
    "DefinitionVersion",
    "ExperimentConfig",
]

CONFIG_VERSION = "odin-research-config-v1"
DEFINITION_UNSET = "metric_def_unset"
REPRO_LEVELS = ("bitwise", "statistical", "not_reproducible")
COMMUNITY_PARTITION_METHODS = ("piao_prior", "louvain", "explicit_label")

# 前缀隔离：研究语义的符号必须带命名空间，裸符号保留给既有 G3。
GOVERNANCE_PREFIX = "gov_"
CONTROL_PREFIX = "ctrl_"
RECOMMENDATION_PREFIX = "rec_"
RESERVED_BARE_SYMBOLS = ("tau", "kappa", "alpha")
"""这三个裸名字属于既有 G3 参数，研究层配置中出现即判为非法。"""


class ConfigError(ValueError):
    """实验配置非法。"""


@dataclass(frozen=True)
class DefinitionVersion:
    """科学定义与阈值的版本状态。

    ``metric_def_version`` 为 :data:`DEFINITION_UNSET` 时表示 S0 未完成：
    指标层**不得**产出任何 v2 指标数值，只能写 ``skipped_metrics``。
    ``thresholds_locked=False`` 时，任何达标线只报观测值，不报 pass/fail。
    """

    metric_def_version: str = DEFINITION_UNSET
    threshold_lock_version: str = "unlocked"
    thresholds_locked: bool = False
    source: str = "《实验指标体系 v2》"

    @property
    def definitions_locked(self) -> bool:
        return self.metric_def_version != DEFINITION_UNSET

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_def_version": self.metric_def_version,
            "threshold_lock_version": self.threshold_lock_version,
            "thresholds_locked": bool(self.thresholds_locked),
            "source": self.source,
        }


@dataclass(frozen=True)
class ExperimentConfig:
    """研究实验的完整配置（任务清单第五部分逐项对应）。

    字段分组
    --------
    population_*           群体规模与 N 序列（有限尺寸标定，Q-44）
    network_*              拓扑与社区划分口径（Q-12，methods 声明 1）
    initial_* / agent_*    初始意见、Agent 群体与策略
    opinion_*              意见动力学
    recommendation_policy  推荐/分发策略
    content_*              内容生成
    intervention_config    治理干预（带 ``gov_`` 前缀）
    random_seed / seeds    随机种子（显式，不代猜）
    simulation_steps       步数
    sampling_interval_k    3A 每 k 轮采样（Q-37，配置必填但不代表口径已锁定）
    metrics_config         指标启用清单与跳过策略
    model_metadata         LLM 底座 / 版本 / 温度
    control_groups         双对照组（L2 鲁棒性）
    backends               多底座比较
    hysteresis_scan        正反扫描路径（迟滞检验）
    """

    name: str
    model: str
    population_size: int = 100
    population_sizes: tuple[int, ...] = ()
    network_topology: str = "ba"
    network_options: dict[str, Any] = field(default_factory=dict)
    initial_opinion_distribution: dict[str, Any] = field(default_factory=dict)
    agent_population_config: dict[str, Any] = field(default_factory=dict)
    agent_policy_config: dict[str, Any] = field(default_factory=dict)
    opinion_dynamics_config: dict[str, Any] = field(default_factory=dict)
    recommendation_policy: dict[str, Any] = field(default_factory=dict)
    content_generation_config: dict[str, Any] = field(default_factory=dict)
    intervention_config: dict[str, Any] = field(default_factory=dict)
    random_seed: int = 0
    seeds: tuple[int, ...] = ()
    simulation_steps: int = 20
    sampling_interval_k: int = 1
    metrics_config: dict[str, Any] = field(default_factory=dict)
    model_metadata: dict[str, Any] = field(default_factory=dict)
    community_partition_method: str = ""
    config_version: str = CONFIG_VERSION
    definition_version: DefinitionVersion = field(default_factory=DefinitionVersion)
    reproducibility_level: str = "bitwise"
    control_groups: tuple[str, ...] = ()
    backends: tuple[str, ...] = ()
    hysteresis_scan: bool = False
    extras: dict[str, Any] = field(default_factory=dict)
    """扩展位：S0 后新增的研究字段先进这里，不改 :meth:`to_dict` 的顶层键集。"""

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """配置校验。任何一条不满足都直接报错，不静默补默认值。"""
        if not self.name:
            raise ConfigError("name 不能为空")
        if not self.model:
            raise ConfigError("model 不能为空")
        if self.config_version != CONFIG_VERSION:
            raise ConfigError(
                f"config_version 不匹配：{self.config_version} != {CONFIG_VERSION}"
            )
        if self.population_size <= 0:
            raise ConfigError("population_size 必须为正整数")
        if any(int(n) <= 0 for n in self.population_sizes):
            raise ConfigError("population_sizes 中每个 N 必须为正整数")
        if self.simulation_steps <= 0:
            raise ConfigError("simulation_steps 必须为正整数")
        if self.sampling_interval_k <= 0:
            raise ConfigError("sampling_interval_k 必须为正整数（3A 每 k 轮采样）")
        if not self.community_partition_method:
            raise ConfigError(
                "community_partition_method 必填：v2 methods 声明 1 要求模块度与"
                "同配系数必须随社区划分口径一同报告，平台不代为选择"
            )
        if self.community_partition_method not in COMMUNITY_PARTITION_METHODS:
            raise ConfigError(
                f"community_partition_method 非法：{self.community_partition_method}；"
                f"允许 {list(COMMUNITY_PARTITION_METHODS)}"
            )
        if self.reproducibility_level not in REPRO_LEVELS:
            raise ConfigError(
                f"reproducibility_level 非法：{self.reproducibility_level}；"
                f"允许 {list(REPRO_LEVELS)}"
            )
        if not self.seeds:
            raise ConfigError("seeds 必须显式给出（不替研究猜随机种子）")
        if len(set(int(s) for s in self.seeds)) != len(self.seeds):
            raise ConfigError("seeds 含重复值")
        for key in self.extras:
            if key in _TOP_LEVEL_KEYS:
                raise ConfigError(f"extras 不得覆盖顶层字段：{key}")
        self._validate_namespaces()

    def _validate_namespaces(self) -> None:
        """Q-31：研究层不得使用与 G3 同名的裸符号。"""
        for section, data in (
            ("intervention_config", self.intervention_config),
            ("recommendation_policy", self.recommendation_policy),
            ("opinion_dynamics_config", self.opinion_dynamics_config),
        ):
            for key in data:
                if key in RESERVED_BARE_SYMBOLS:
                    raise ConfigError(
                        f"{section}.{key} 与既有 G3 参数同名异义（Q-31）："
                        f"请使用 {GOVERNANCE_PREFIX}/{RECOMMENDATION_PREFIX}/"
                        f"{CONTROL_PREFIX} 前缀，例如 {GOVERNANCE_PREFIX}{key}。"
                        "裸符号保留给 G3，以保住既有指纹回归"
                    )
        # 治理三元组 π=(ω,τ,κ) 在研究层以 gov_omega/gov_tau/gov_kappa 表达。
        for key in ("gov_omega", "gov_tau", "gov_kappa"):
            if key in self.intervention_config:
                _ = self.intervention_config[key]

    @property
    def effective_population_sizes(self) -> tuple[int, ...]:
        """有限尺寸标定的 N 序列；未配置时退化为单一群体规模。"""
        return tuple(self.population_sizes) or (int(self.population_size),)

    @property
    def fingerprint(self) -> str:
        """配置内容指纹：与键序无关，不含输出路径与时间。"""
        return config_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """规范化字典。顶层键集固定，扩展字段一律进 ``extras``。"""
        return {
            "config_version": self.config_version,
            "name": self.name,
            "model": self.model,
            "population_size": int(self.population_size),
            "population_sizes": [int(n) for n in self.population_sizes],
            "network_topology": self.network_topology,
            "network_options": dict(self.network_options),
            "initial_opinion_distribution": dict(self.initial_opinion_distribution),
            "agent_population_config": dict(self.agent_population_config),
            "agent_policy_config": dict(self.agent_policy_config),
            "opinion_dynamics_config": dict(self.opinion_dynamics_config),
            "recommendation_policy": dict(self.recommendation_policy),
            "content_generation_config": dict(self.content_generation_config),
            "intervention_config": dict(self.intervention_config),
            "random_seed": int(self.random_seed),
            "seeds": [int(s) for s in self.seeds],
            "simulation_steps": int(self.simulation_steps),
            "sampling_interval_k": int(self.sampling_interval_k),
            "metrics_config": dict(self.metrics_config),
            "model_metadata": dict(self.model_metadata),
            "community_partition_method": self.community_partition_method,
            "definition_version": self.definition_version.to_dict(),
            "reproducibility_level": self.reproducibility_level,
            "control_groups": list(self.control_groups),
            "backends": list(self.backends),
            "hysteresis_scan": bool(self.hysteresis_scan),
            "extras": dict(self.extras),
        }

    # ------------------------------------------------------------------
    # 构造 / 派生
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperimentConfig":
        if not isinstance(data, Mapping):
            raise ConfigError("实验配置必须是对象/映射")
        unknown = sorted(set(data) - _TOP_LEVEL_KEYS - {"extras"})
        if unknown:
            raise ConfigError(f"实验配置含未知字段：{unknown}；请放进 extras")

        dv_raw = data.get("definition_version") or {}
        if not isinstance(dv_raw, Mapping):
            raise ConfigError("definition_version 必须是对象/映射")
        try:
            dv = DefinitionVersion(
                metric_def_version=str(dv_raw.get("metric_def_version", DEFINITION_UNSET)),
                threshold_lock_version=str(dv_raw.get("threshold_lock_version", "unlocked")),
                thresholds_locked=bool(dv_raw.get("thresholds_locked", False)),
                source=str(dv_raw.get("source", "《实验指标体系 v2》")),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"definition_version 非法：{exc}") from exc

        def _dict(key: str) -> dict[str, Any]:
            value = data.get(key) or {}
            if not isinstance(value, Mapping):
                raise ConfigError(f"{key} 必须是对象/映射")
            return dict(value)

        def _ints(key: str) -> tuple[int, ...]:
            value = data.get(key) or ()
            if not isinstance(value, (list, tuple)):
                raise ConfigError(f"{key} 必须是列表")
            return tuple(int(v) for v in value)

        def _strs(key: str) -> tuple[str, ...]:
            value = data.get(key) or ()
            if not isinstance(value, (list, tuple)):
                raise ConfigError(f"{key} 必须是列表")
            return tuple(str(v) for v in value)

        cfg = cls(
            name=str(data.get("name", "")),
            model=str(data.get("model", "")),
            population_size=int(data.get("population_size", 100)),
            population_sizes=_ints("population_sizes"),
            network_topology=str(data.get("network_topology", "ba")),
            network_options=_dict("network_options"),
            initial_opinion_distribution=_dict("initial_opinion_distribution"),
            agent_population_config=_dict("agent_population_config"),
            agent_policy_config=_dict("agent_policy_config"),
            opinion_dynamics_config=_dict("opinion_dynamics_config"),
            recommendation_policy=_dict("recommendation_policy"),
            content_generation_config=_dict("content_generation_config"),
            intervention_config=_dict("intervention_config"),
            random_seed=int(data.get("random_seed", 0)),
            seeds=_ints("seeds"),
            simulation_steps=int(data.get("simulation_steps", 20)),
            sampling_interval_k=int(data.get("sampling_interval_k", 1)),
            metrics_config=_dict("metrics_config"),
            model_metadata=_dict("model_metadata"),
            community_partition_method=str(data.get("community_partition_method", "")),
            config_version=str(data.get("config_version", CONFIG_VERSION)),
            definition_version=dv,
            reproducibility_level=str(data.get("reproducibility_level", "bitwise")),
            control_groups=_strs("control_groups"),
            backends=_strs("backends"),
            hysteresis_scan=bool(data.get("hysteresis_scan", False)),
            extras=_dict("extras"),
        )
        cfg.validate()
        return cfg

    @classmethod
    def from_json(cls, text: str) -> "ExperimentConfig":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"实验配置不是合法 JSON：{exc}") from exc
        return cls.from_dict(data)

    def with_overrides(self, **changes: Any) -> "ExperimentConfig":
        """返回带覆盖项的新配置；覆盖后重新校验。"""
        cfg = replace(self, **changes)
        cfg.validate()
        return cfg

    def to_core_sweep(self) -> SweepSpec:
        """生成核心 sweep（**不含** N 序列 / 双对照 / 底座等研究维度）。

        研究维度由 ``research.governance.expand_research_runs`` 展开，不塞进
        ``SweepSpec``——否则会改变 ``SweepSpec.to_dict()`` 的键集与指纹，
        破坏既有实验的可复现性。
        """
        base: dict[str, Any] = {
            "mode": str(self.opinion_dynamics_config.get("mode", "none")),
            "scenario": str(self.initial_opinion_distribution.get("scenario", "polarized")),
        }
        for key in ("placement", "beta_gov", "tau"):
            if key in self.extras:
                base[key] = self.extras[key]
        return SweepSpec(
            name=self.name,
            model=self.model,
            base=base,
            axes={},
            seeds=[int(s) for s in self.seeds],
            store_trajectory=bool(self.metrics_config.get("store_trajectory", False)),
        )


_TOP_LEVEL_KEYS = frozenset(ExperimentConfig.__dataclass_fields__.keys())
