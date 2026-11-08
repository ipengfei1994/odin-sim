"""研究扩展层（ODIN Experiment Specification v1.0 的工程实现）。

分层纪律
--------
* 本包**单向依赖**平台核心（``model`` / ``experiments`` / ``storage``），
  核心层不得 import 本包。研究指标的口径定义在 ``research.metrics``，
  不写死进 ``model`` 或 ``experiments``。
* 所有能力**默认关闭**：不传 ``ExperimentConfig`` 时，既有 ``run_sweep``
  行为逐字节不变（G3 指纹回归不受影响）。
* 本包不实现 ODIN-Agents 侧的认知/决策/LLM 能力；``synthetic`` 模块只是
  **验证数据通道用的确定性合成体**，不含任何研究结论。

S0 状态（重要）
--------------
《实验指标体系 v2》中 43 条指标的数学定义仍为 TBD，27 项 Open Questions
为阻塞级。因此 ``DefinitionVersion.metric_def_version`` 缺省为
``metric_def_unset``：此时**所有研究指标一律进入 skipped_metrics**，
只落盘原始观测数据，绝不产出伪装成指标数值的结果。
"""
from __future__ import annotations

from .config import (
    CONFIG_VERSION,
    COMMUNITY_PARTITION_METHODS,
    DEFINITION_UNSET,
    REPRO_LEVELS,
    ConfigError,
    DefinitionVersion,
    ExperimentConfig,
)
from .events import EVENT_PAYLOAD_TYPES, EventEnvelope, EventType, make_event
from .recorder import EventRecorder, read_events
from .pipeline import ResearchResult, run_research_experiment

__all__ = [
    "CONFIG_VERSION",
    "COMMUNITY_PARTITION_METHODS",
    "DEFINITION_UNSET",
    "REPRO_LEVELS",
    "ConfigError",
    "DefinitionVersion",
    "ExperimentConfig",
    "EVENT_PAYLOAD_TYPES",
    "EventEnvelope",
    "EventType",
    "make_event",
    "EventRecorder",
    "read_events",
    "ResearchResult",
    "run_research_experiment",
]

__version__ = "1.0.0"
