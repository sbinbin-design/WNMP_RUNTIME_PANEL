# -*- coding: utf-8 -*-
"""
WNMP Panel Path Utilities - Centralized path management.

This module provides a single source of truth for all paths used by
the Panel components. All files should import from here instead of
calculating paths independently.
"""
import os
import sys
from pathlib import Path


def _get_frozen_root():
    """Get project root when running as PyInstaller frozen exe."""
    return Path(sys.executable).resolve().parent


def _get_dev_root():
    """Get project root when running from source (development mode)."""
    # runtime/panel/paths.py -> runtime/panel -> runtime -> project_root
    return Path(__file__).resolve().parents[2]


def get_root_dir():
    """Return the project root directory (WNMP_RUNTIME directory).

    This is the directory containing:
    - config/runtime.ini
    - bin/nginx, bin/php, bin/mysql
    - logs/, data/
    - runtime/
    - launcher/
    - WNMPanel.exe
    """
    if getattr(sys, 'frozen', False):
        return str(_get_frozen_root())
    return str(_get_dev_root())


def get_panel_dir():
    """Return the runtime/panel directory."""
    return str(Path(__file__).resolve().parent)


def get_template_dir():
    """Return the runtime/panel/templates directory."""
    return str(Path(__file__).resolve().parent / "templates")


def get_asset_dir():
    """Return the runtime/panel/assets directory."""
    return str(Path(__file__).resolve().parent / "assets")


def get_config_path():
    """Return path to config/runtime.ini."""
    return os.path.join(get_root_dir(), "config", "runtime.ini")


def get_log_dir():
    """Return path to logs directory."""
    return os.path.join(get_root_dir(), "logs")


def get_data_dir():
    """Return path to data directory."""
    return os.path.join(get_root_dir(), "data")


def get_bin_dir():
    """Return path to bin directory."""
    return os.path.join(get_root_dir(), "bin")
