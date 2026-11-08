"""S2｜事件记录器：实时落盘、顺序校验、确定性回放。

事件是**过程量**：曝光、选择行为与事件时序在仿真结束后无法重建（v2 §3D），
因此必须发生时即写盘；本类不做缓冲批处理，也不允许事后补写。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from .events import EventEnvelope, EventType, validate_order

__all__ = ["EVENTS_FILENAME", "RecorderError", "EventRecorder", "read_events", "replay_digest"]

EVENTS_FILENAME = "events.jsonl"


class RecorderError(RuntimeError):
    """事件记录或回放失败。"""


class EventRecorder:
    """按发生顺序把事件追加写入 JSONL。

    ``sequence_no`` 由记录器校验（不自动改写）——改写会把"漏了一条事件"
    伪装成"顺序正确"。``tick`` 倒退或同 tick 内 phase 逆序同样报错。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.is_dir():
            self.path = self.path / EVENTS_FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8", newline="\n")
        self._prev: EventEnvelope | None = None
        self._count = 0
        self._digest = hashlib.sha256()

    def append(self, event: EventEnvelope) -> None:
        if self._fh.closed:
            raise RecorderError("Recorder 已关闭，不能追加事件")
        if self._prev is not None:
            errors = validate_order(self._prev, event)
            if errors:
                raise RecorderError("；".join(errors))
        # 校验 envelope 能被强类型还原（回放侧的对称性保证）
        try:
            event.typed_payload
        except (TypeError, ValueError, KeyError) as exc:
            raise RecorderError(f"payload 无法还原为强类型：{exc}") from exc
        line = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        self._fh.write(line + "\n")
        self._digest.update(line.encode("utf-8"))
        self._prev = event
        self._count += 1

    def append_all(self, events: Iterable[EventEnvelope]) -> None:
        for event in events:
            self.append(event)

    @property
    def count(self) -> int:
        return self._count

    @property
    def digest(self) -> str:
        """截至当前的事件流哈希（不含已 flush 状态差异）。"""
        return self._digest.hexdigest()

    def flush(self) -> None:
        if not self._fh.closed:
            self._fh.flush()

    def close(self) -> str:
        self.flush()
        if not self._fh.closed:
            self._fh.close()
        return self.digest

    def __enter__(self) -> "EventRecorder":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def write_digest(self, path: str | Path | None = None) -> Path:
        """把事件流哈希单独落盘，供跨进程复现比对（T-REPRO/2）。"""
        target = Path(path) if path else self.path.with_suffix(".sha256")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.digest + "\n", encoding="utf-8")
        return target


def read_events(path: str | Path) -> list[EventEnvelope]:
    """读取并重放事件流；逐条校验顺序与强类型。"""
    p = Path(path)
    if p.is_dir():
        p = p / EVENTS_FILENAME
    if not p.is_file():
        return []
    events: list[EventEnvelope] = []
    prev: EventEnvelope | None = None
    with p.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                event = EventEnvelope.from_dict(json.loads(line))
                EventType(event.event_type)
                event.typed_payload
            except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
                raise RecorderError(f"第 {line_no} 行事件非法：{exc}") from exc
            if prev is not None:
                errors = validate_order(prev, event)
                if errors:
                    raise RecorderError(f"第 {line_no} 行时序错误：" + "；".join(errors))
            events.append(event)
            prev = event
    return events


def replay_digest(events: Sequence[EventEnvelope]) -> str:
    """重放事件流的规范化哈希；两次运行必须逐字符一致。"""
    digest = hashlib.sha256()
    for event in events:
        line = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()
