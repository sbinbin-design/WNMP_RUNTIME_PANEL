# -*- coding: utf-8 -*-
"""
WNMP Panel Version - 集中维护 Panel 自身版本号。

版本号来源优先级：
1. 项目根目录 VERSION 文件（推荐，便于 CI/构建脚本读取）
2. 本模块默认值（兜底）

此版本号代表 WNMP Panel 控制面板自身的版本，包括：
- WNMPPanel.exe 启动器
- runtime/panel_server.py Panel Server
- 前端 UI (app.js / style.css / index.html)
- 运行器管理逻辑 (wnmpctl / wnmp_state / wnmp_config 等)

不是 Nginx、PHP、MySQL、Python 的组件版本。
"""
import os

# ---- 默认兜底值 ----
PANEL_NAME = "WNMP Runtime Panel"
PANEL_VERSION = "0.2.1-dev"
BUILD_DATE = ""  # 构建时可由脚本注入，留空表示开发构建


def _read_version_file():
    """从项目根目录 VERSION 文件读取版本号，失败返回 None。"""
    try:
        # runtime/version.py -> runtime -> project_root
        version_file = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "VERSION"))
        if os.path.isfile(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                ver = f.read().strip()
                if ver:
                    return ver
    except Exception:
        pass
    return None


def get_panel_version():
    """获取 Panel 版本号，优先读 VERSION 文件，兜底用默认值。"""
    return _read_version_file() or PANEL_VERSION


def get_panel_info():
    """获取 Panel 版本信息字典，供 API 接口返回。"""
    return {
        "panel_name": PANEL_NAME,
        "panel_version": get_panel_version(),
        "build_date": BUILD_DATE or "",
        "root_dir": _get_root_dir(),
    }


def _get_root_dir():
    """获取项目根目录。"""
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # runtime/version.py -> runtime -> project_root
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
