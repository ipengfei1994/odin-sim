"""L4 接口层之一：本地 HTTP 服务。

与 ``cli`` / ``ui`` 平级——**只读产物、转调分析层**，自己不重算统计量、
不引入动力学逻辑。所有数值口径都与 CLI 同源（同一 ``analysis`` 函数），
因此页面上的每个数字都能被 CLI 复算出来。

依赖方向单向：``api`` → ``analysis`` / ``experiments`` / ``storage`` / ``model``，
核心层不 import 本包（由 ``tests/unit/test_ui_layering.py`` 的 AST 检查守住）。
"""
from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
