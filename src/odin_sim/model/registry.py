"""模型注册表。

注册是显式的：模型不会因"放在某个目录"而自动生效。重复注册同名模型
直接报错，避免实验静默地跑在错误实现上。
"""
from __future__ import annotations

from typing import Generic, TypeVar

from .protocol import DynamicsModel, Metric, TopologyModel

__all__ = ["RegistryError", "Registry"]

T = TypeVar("T")


class RegistryError(RuntimeError):
    """注册或查找失败。"""


class Registry(Generic[T]):
    """按名字索引的简易注册表。"""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T, *, overwrite: bool = False) -> None:
        if name in self._items and not overwrite:
            raise RegistryError(
                f"{self._kind} 已注册同名条目：{name}。"
                "如需替换请显式传 overwrite=True（实验复现通常不应这么做）。"
            )
        self._items[name] = item

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            available = ", ".join(sorted(self._items)) or "(空)"
            raise RegistryError(
                f"未注册的 {self._kind}：{name}。已注册：{available}"
            ) from None

    def has(self, name: str) -> bool:
        return name in self._items

    def unregister(self, name: str) -> None:
        """移除一个注册项。供测试与插件卸载使用；不存在时静默返回。"""
        self._items.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._items)

    def clear(self) -> None:
        self._items.clear()


DYNAMICS: Registry[DynamicsModel] = Registry("动力学模型")
TOPOLOGY: Registry[TopologyModel] = Registry("拓扑生成器")
METRICS: Registry[Metric] = Registry("指标")
