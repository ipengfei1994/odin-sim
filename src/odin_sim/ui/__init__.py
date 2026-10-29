"""ODIN-Sim L4 接口层：Web UI（Streamlit）。

UI 是已有能力的展示与编排层，不引入仿真逻辑；核心层不得 import 本包
（见 ``docs/architecture-v1.md`` §4.3）。本包顶层**不** import streamlit，
以保证在不安装 ``[ui]`` 依赖时 ``import odin_sim.ui`` 仍可成功。
"""
from pathlib import Path

APP_PATH = Path(__file__).resolve().parent / "app.py"
