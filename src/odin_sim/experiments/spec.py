"""实验规格：把一份配置确定性地展开成一组可执行的运行。

设计约束
--------
1. **展开顺序固定**：同一份配置任何时候展开都得到同一顺序的同一批运行，
   行序本身就是可复现的一部分。
2. **run_id 内容寻址**：``run_id = sha1(canonical(spec))[:16]``，只由运行
   规格决定，与输出目录、执行时间、执行次数无关。因此换目录重跑会命中
   同一批 run_id，天然支持断点续跑。
3. **不允许静默去重**：展开出重复 run_id 直接报错。重复通常意味着配置写错，
   去重会把"少跑了实验"伪装成"跑完了"。
4. **不替研究做默认值**：种子必须显式给出；未声明的轴不参与扫描。
5. **对照口径由配置给出**：需要配对计算的指标（如反噬）依赖"什么是对照"
   这一研究判断。平台只提供**通用的对照变换机制**（一组字段覆盖 + 种子
   策略 + 适用范围），不内置任何具体口径——口径写在配置里，随实验走。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from itertools import product
from typing import Any, Mapping, Sequence

from ..model.protocol import RunRequest
from ..reproducibility import config_fingerprint

__all__ = [
    "SpecError",
    "RunSpec",
    "ControlSpec",
    "SweepSpec",
    "RUN_FIELDS",
    "PARAMS_PREFIX",
    "ROLE_MAIN",
    "ROLE_CONTROL",
    "CONTROL_NONE",
    "CONTROL_SELF",
    "CONTROL_LINKED",
    "expand_sweep",
]

RUN_FIELDS = ("mode", "placement", "beta_gov", "tau", "scenario")
PARAMS_PREFIX = "param_overrides."
_ON_ERROR = ("abort", "continue")
ROLE_MAIN = "main"
ROLE_CONTROL = "control"

# 主运行与其对照的关系。三者都会写进 manifest，分析层据此判断配对量是否可用。
CONTROL_NONE = "none"      # 未配置对照（适用范围未命中）
CONTROL_SELF = "self"      # 对照变换后与自身相同：配对量退化为恒零，无定义
CONTROL_LINKED = "linked"  # 已关联到一条对照运行（control_run_id 有效）


class SpecError(ValueError):
    """实验规格非法。"""


@dataclass(frozen=True)
class RunSpec:
    """一次运行的完整规格。对应一个 :class:`RunRequest` 加模型名。"""

    model: str
    mode: str
    seed: int
    placement: str = "ba"
    beta_gov: float = 0.0
    tau: int = 0
    scenario: str = "polarized"
    param_overrides: dict[str, Any] = field(default_factory=dict)
    role: str = ROLE_MAIN
    """运行角色：主运行或对照运行。

    **不参与 run_id 计算**（见 :meth:`to_dict`）。对照运行与同规格的主运行
    落在同一产物目录，这才使"对照就是某个已存在的运行"成为可能；角色只
    是本次 sweep 中对它的用法，不是它自身的一部分。
    """

    control_status: str = CONTROL_NONE
    control_run_id: str | None = None

    def to_request(self) -> RunRequest:
        return RunRequest(
            mode=self.mode,
            seed=self.seed,
            placement=self.placement,
            beta_gov=self.beta_gov,
            tau=self.tau,
            scenario=self.scenario,
            param_overrides=dict(self.param_overrides),
        )

    def to_dict(self) -> dict[str, Any]:
        """规范化字典（键序固定），用于 run_id 与 manifest。

        只含**决定数值的字段**：``role`` / ``control_*`` 是本次 sweep 的用法
        标注，不是运行本身的属性，因此不在此列，也不影响 run_id。
        """
        return {
            "model": self.model,
            "mode": self.mode,
            "seed": int(self.seed),
            "placement": self.placement,
            "beta_gov": float(self.beta_gov),
            "tau": int(self.tau),
            "scenario": self.scenario,
            "param_overrides": {str(k): v for k, v in sorted(self.param_overrides.items())},
        }

    @property
    def spec_fingerprint(self) -> str:
        """运行规格指纹。"""
        return config_fingerprint(self.to_dict())

    @property
    def run_id(self) -> str:
        """产物目录名：只由规格内容决定。"""
        return self.spec_fingerprint

    @property
    def config_id(self) -> str:
        return self.to_request().config_id


@dataclass(frozen=True)
class ControlSpec:
    """对照口径：**从一条主运行派生出它的对照运行**的变换规则。

    这是通用机制，不是某个具体口径。三种常见写法都可表达：

    * ``{"overrides": {"mode": "none"}}`` —— 对照为"关闭治理"（同种子）
    * ``{"overrides": {"beta_gov": 0.0}}`` —— 对照为"治理剂量归零"（同种子）
    * ``{"overrides": {"param_overrides.w_gov": 0.0}}`` —— 对照为"治理权重归零"

    字段
    ----
    overrides:
        施加到主运行上的字段覆盖。键必须是 :data:`RUN_FIELDS` 之一或
        ``param_overrides.<参数名>``。**不覆盖 seed**（种子由 ``seed`` 单独
        控制）——把两个不同性质的"变量"混在一起会让口径无法复述。
    seed:
        ``"same"``（默认，与主运行同种子，配对比较的标准做法）或显式整数。
    apply_to:
        适用范围白名单，键必须是 :data:`RUN_FIELDS` 之一，值为允许的取值
        列表。空表示"所有主运行都需要对照"。
    name:
        口径标识，原样写进 manifest 与汇总表，便于事后回答"这批结果用的是
        哪种对照口径"。

    为什么不让平台内置一个默认值：对照是什么，取决于研究问题（"无治理"
    与"治理失效"是两个不同的对照）。平台给出机制与记录，口径由实验配置。
    """

    overrides: dict[str, Any] = field(default_factory=dict)
    seed: Any = "same"
    apply_to: dict[str, list[Any]] = field(default_factory=dict)
    name: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ControlSpec":
        if not isinstance(data, Mapping):
            raise SpecError("control 必须是对象/映射")

        known = {"name", "overrides", "seed", "apply_to"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise SpecError(f"control 含未知字段：{unknown}")

        overrides = dict(data.get("overrides") or {})
        if not overrides:
            raise SpecError("control.overrides 不能为空（对照必须改变某些东西）")
        for key in overrides:
            if key in RUN_FIELDS:
                continue
            if key.startswith(PARAMS_PREFIX) and len(key) > len(PARAMS_PREFIX):
                continue
            raise SpecError(
                f"control.overrides 键非法：{key}；"
                f"允许 {list(RUN_FIELDS)} 或 {PARAMS_PREFIX}<参数名>；"
                "seed 由 control.seed 单独控制，不可在此覆盖"
            )

        seed = data.get("seed", "same")
        if isinstance(seed, bool) or not isinstance(seed, (str, int)):
            raise SpecError("control.seed 必须是 'same' 或整数")
        if isinstance(seed, str) and seed != "same":
            raise SpecError(f"control.seed 字符串只允许 'same'，实际：{seed}")

        apply_to = dict(data.get("apply_to") or {})
        for key, values in apply_to.items():
            if key not in RUN_FIELDS:
                raise SpecError(
                    f"control.apply_to 键非法：{key}；允许 {list(RUN_FIELDS)}"
                )
            if not isinstance(values, (list, tuple)) or not values:
                raise SpecError(f"control.apply_to.{key} 必须是非空列表")

        return cls(
            overrides={str(k): v for k, v in overrides.items()},
            seed=seed,
            apply_to={str(k): list(v) for k, v in apply_to.items()},
            name=str(data.get("name", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "overrides": {str(k): v for k, v in sorted(self.overrides.items())},
            "seed": self.seed,
            "apply_to": {str(k): list(v) for k, v in sorted(self.apply_to.items())},
        }

    def resolve_seed(self, run: "RunSpec") -> int:
        if self.seed == "same":
            return int(run.seed)
        return int(self.seed)

    def matches(self, run: "RunSpec") -> bool:
        """主运行是否在适用范围内（空范围 = 全部适用）。"""
        for key, allowed in self.apply_to.items():
            if getattr(run, key, None) not in allowed:
                return False
        return True

    def derive(self, run: "RunSpec") -> "RunSpec":
        """从主运行派生对照运行。对照不再派生对照（.role=control 即终止）。"""
        values = {
            "mode": run.mode,
            "placement": run.placement,
            "beta_gov": run.beta_gov,
            "tau": run.tau,
            "scenario": run.scenario,
        }
        params = dict(run.param_overrides)
        for key, value in self.overrides.items():
            if key.startswith(PARAMS_PREFIX):
                params[key[len(PARAMS_PREFIX):]] = value
            else:
                values[key] = value
        return RunSpec(
            model=run.model,
            mode=str(values["mode"]),
            seed=self.resolve_seed(run),
            placement=str(values["placement"]),
            beta_gov=float(values["beta_gov"]),
            tau=int(values["tau"]),
            scenario=str(values["scenario"]),
            param_overrides=params,
            role=ROLE_CONTROL,
        )


@dataclass(frozen=True)
class SweepSpec:
    """一次批量实验的规格：基准参数 × 扫描轴 × 种子。

    ``axes`` 的键必须是 ``RUN_FIELDS`` 之一，或 ``param_overrides.<key>``。
    ``base`` 提供未扫描字段的取值；``mode`` 与 ``scenario`` 无默认值，
    必须由配置显式给出——平台不猜研究配置。
    """

    name: str
    model: str
    base: dict[str, Any] = field(default_factory=dict)
    axes: dict[str, list[Any]] = field(default_factory=dict)
    seeds: list[int] = field(default_factory=list)
    store_trajectory: bool = False
    on_error: str = "abort"
    control: ControlSpec | None = None

    @property
    def fingerprint(self) -> str:
        """sweep 级指纹（含扫描轴、种子与对照口径，不含输出路径）。"""
        return config_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "base": dict(self.base),
            "axes": {str(k): list(v) for k, v in self.axes.items()},
            "seeds": [int(s) for s in self.seeds],
            "store_trajectory": bool(self.store_trajectory),
            "on_error": self.on_error,
            "control": self.control.to_dict() if self.control else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SweepSpec":
        """从配置字典构造，并做字段校验。未知字段一律报错，不静默忽略。"""
        if not isinstance(data, Mapping):
            raise SpecError("sweep 配置必须是对象/映射")

        known = {"name", "model", "base", "axes", "seeds",
                 "store_trajectory", "on_error", "control"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise SpecError(f"sweep 配置含未知字段：{unknown}")

        for required in ("name", "model"):
            if not data.get(required):
                raise SpecError(f"sweep 配置缺少必填字段：{required}")

        base = dict(data.get("base") or {})
        base_unknown = sorted(set(base) - set(RUN_FIELDS) - {"param_overrides"})
        if base_unknown:
            raise SpecError(
                f"base 含非法字段：{base_unknown}；"
                f"允许 {list(RUN_FIELDS)} 与 param_overrides"
            )
        if "mode" not in base:
            raise SpecError("base 必须显式给出 mode（平台不代研究猜配置）")
        if "param_overrides" in base and not isinstance(base["param_overrides"], Mapping):
            raise SpecError("base.param_overrides 必须是对象/映射")

        axes_raw = dict(data.get("axes") or {})
        axes: dict[str, list[Any]] = {}
        for key, values in axes_raw.items():
            if not isinstance(values, (list, tuple)) or not values:
                raise SpecError(f"axes.{key} 必须是非空列表")
            if key in RUN_FIELDS or key.startswith(PARAMS_PREFIX):
                axes[key] = list(values)
            else:
                raise SpecError(
                    f"axes 键非法：{key}；允许 {list(RUN_FIELDS)} 或 "
                    f"{PARAMS_PREFIX}<参数名>"
                )

        seeds = _parse_seeds(data.get("seeds"))

        on_error = str(data.get("on_error", "abort"))
        if on_error not in _ON_ERROR:
            raise SpecError(f"on_error 必须是 {_ON_ERROR} 之一，实际：{on_error}")

        control = None
        if data.get("control") is not None:
            control = ControlSpec.from_dict(data["control"])

        return cls(
            name=str(data["name"]),
            model=str(data["model"]),
            base=base,
            axes=axes,
            seeds=seeds,
            store_trajectory=bool(data.get("store_trajectory", False)),
            on_error=on_error,
            control=control,
        )


def _parse_seeds(raw: Any) -> list[int]:
    """种子必须是显式的：列表，或 {"start","count","step"}。"""
    if raw is None:
        raise SpecError("seeds 必须显式给出（列表或 {start, count, step}）")
    if isinstance(raw, Mapping):
        unknown = sorted(set(raw) - {"start", "count", "step"})
        if unknown:
            raise SpecError(f"seeds 对象含未知字段：{unknown}")
        try:
            start = int(raw["start"])
            count = int(raw["count"])
        except KeyError as exc:
            raise SpecError(f"seeds 对象缺少字段：{exc}") from exc
        step = int(raw.get("step", 1))
        if count <= 0:
            raise SpecError("seeds.count 必须为正")
        if step <= 0:
            raise SpecError("seeds.step 必须为正")
        return [start + i * step for i in range(count)]
    if isinstance(raw, (list, tuple)) and raw:
        return [int(s) for s in raw]
    raise SpecError("seeds 必须是非空列表或 {start, count, step}")


def expand_sweep(spec: SweepSpec) -> list[RunSpec]:
    """把 sweep 展开成运行列表。

    顺序：先按 ``axes`` 的**声明顺序**做笛卡尔积，再对每个组合按种子顺序
    展开。因此同一配置的重复样本在结果表中相邻，便于聚合。

    配置了 ``control`` 时，输出为 **对照运行在前、主运行在后**：对照必须先
    执行，主运行才能在内存中取到配对轨迹。同一条对照只生成一次（按首次
    被引用顺序去重），多个主运行共享它。
    """
    mains = _expand_main(spec)
    if spec.control is None:
        return mains

    control = spec.control
    main_ids = {m.run_id for m in mains}

    controls: list[RunSpec] = []
    control_index: dict[str, int] = {}
    linked: list[RunSpec] = []

    for main in mains:
        if not control.matches(main):
            linked.append(main)
            continue
        derived = control.derive(main)

        if derived.run_id == main.run_id:
            # 变换后仍是自己：配对差恒为零，指标无定义。不生成对照，
            # 但显式标记为 self，让指标层给出可追溯的跳过原因。
            linked.append(replace(main, control_status=CONTROL_SELF))
            continue

        if derived.run_id in main_ids:
            raise SpecError(
                f"对照变换后的规格与某条主运行完全相同（run_id={derived.run_id}）："
                f"对照应只由 control 机制生成，请从扫描轴中移除该配置，"
                f"或用 control.apply_to 缩小适用范围"
            )

        if derived.run_id not in control_index:
            control_index[derived.run_id] = len(controls)
            controls.append(derived)
        linked.append(replace(
            main,
            control_status=CONTROL_LINKED,
            control_run_id=derived.run_id,
        ))

    return controls + linked


def _expand_main(spec: SweepSpec) -> list[RunSpec]:
    keys = list(spec.axes.keys())
    combos: Sequence[tuple] = list(product(*(spec.axes[k] for k in keys))) if keys else [()]

    out: list[RunSpec] = []
    seen: dict[str, tuple] = {}
    for combo in combos:
        overrides = dict(spec.base)
        params = dict(spec.base.get("param_overrides") or {})
        for key, value in zip(keys, combo):
            if key.startswith(PARAMS_PREFIX):
                params[key[len(PARAMS_PREFIX):]] = value
            else:
                overrides[key] = value
        for seed in spec.seeds:
            run = RunSpec(
                model=spec.model,
                mode=str(overrides.get("mode", "")),
                seed=int(seed),
                placement=str(overrides.get("placement", "ba")),
                beta_gov=float(overrides.get("beta_gov", 0.0)),
                tau=int(overrides.get("tau", 0)),
                scenario=str(overrides.get("scenario", "polarized")),
                param_overrides=dict(params),
            )
            if run.run_id in seen:
                raise SpecError(
                    f"展开出重复的运行（run_id={run.run_id}）："
                    f"{seen[run.run_id]} 与 {run.to_dict()}；"
                    "平台不去重，请修正配置"
                )
            seen[run.run_id] = run.to_dict()
            out.append(run)
    return out


def sweep_from_json(text: str) -> SweepSpec:
    """从 JSON 文本解析 sweep 配置。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"sweep 配置不是合法 JSON：{exc}") from exc
    return SweepSpec.from_dict(data)


def hash_payload(obj: Mapping[str, Any]) -> str:
    """与 :func:`config_fingerprint` 一致的内容哈希（此处仅为显式复用点）。"""
    return hashlib.sha1(
        json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
