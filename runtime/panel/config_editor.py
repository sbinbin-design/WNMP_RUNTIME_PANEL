# -*- coding: utf-8 -*-
"""
WNMP Config Editor - 配置文件保存、备份、校验、回滚、dirty 标记。

panel_server.py 只负责 HTTP 入参和返回 JSON，业务逻辑集中在此模块。
"""
import os
import time
import shutil
import logging
from runtime.wnmp_component_paths import (
    get_nginx_conf_path, get_nginx_site_conf_path,
    get_php_ini_path, get_php_cgi_ini_path, get_mysql_ini_path,
    get_runtime_ini_path,
)


def _build_config_file_map(root_dir):
    """构建配置名到文件绝对路径的映射。

    路径收敛：通过统一路径模块获取配置文件路径，而非硬编码相对路径。
    """
    return {
        "nginx": get_nginx_conf_path(root_dir),
        "nginx-site": get_nginx_site_conf_path(root_dir),
        "php": get_php_ini_path(root_dir),
        "php-cgi": get_php_cgi_ini_path(root_dir),
        "mysql": get_mysql_ini_path(root_dir),
        "runtime": get_runtime_ini_path(root_dir),  # 运行器配置，独立于 Nginx/PHP/MySQL 组件
    }


# 配置名到组件名的映射（用于标记 config_dirty）
CONFIG_COMPONENT_MAP = {
    "nginx": "nginx",
    "nginx-site": "nginx",
    "php": "php",
    "php-cgi": "php",
    "mysql": "mysql",
    # runtime 不映射到任何组件，保存时不触发组件 dirty 标记
}

# 允许的配置名白名单
VALID_CONFIG_NAMES = set(CONFIG_COMPONENT_MAP.keys()) | {"runtime"}


def save_config_file(root_dir, name, content):
    """保存配置文件，自动备份+校验+回滚+标记 dirty。

    最外层 try/except 兜底：任何未捕获异常都返回标准 dict，不冒泡到 panel_server。

    Args:
        root_dir: 项目根目录
        name: 配置名（如 "nginx", "nginx-site", "php-cgi"）
        content: 配置文件内容

    Returns:
        dict: {
            "success": bool,
            "message": str,
            "affected_component": str or None,
            "config_dirty": bool or None,
            "backup_path": str or None,
        }
    """
    # 先确定 component，用于异常兜底
    component = CONFIG_COMPONENT_MAP.get(name)

    try:
        return _save_config_file_impl(root_dir, name, content, component)
    except Exception as e:
        # 最外层兜底：任何未捕获异常都返回标准 dict
        _log_warning(root_dir, "save_config_file 未捕获异常: {}".format(str(e)))
        return {
            "success": False,
            "message": "保存配置时发生内部错误: " + str(e),
            "affected_component": component,
            "config_dirty": None,
            "backup_path": None,
        }


def _save_config_file_impl(root_dir, name, content, component):
    """save_config_file 的实际实现，由外层 try/except 保护。"""
    # 白名单校验
    if name not in VALID_CONFIG_NAMES:
        return {
            "success": False,
            "message": "无效配置名称",
            "affected_component": None,
            "config_dirty": None,
            "backup_path": None,
        }

    # runtime.ini 走独立保存逻辑，不触发 nginx -t、不标记组件 dirty
    if name == "runtime":
        return _save_runtime_config(root_dir, content)

    # 路径收敛：通过统一路径模块获取配置文件绝对路径
    config_file_map = _build_config_file_map(root_dir)
    path = config_file_map[name]
    backup_path = None

    # POST 文件存在性检查：禁止通过编辑器创建新配置文件
    if not os.path.isfile(path):
        return {
            "success": False,
            "message": "配置文件不存在，不能通过编辑器创建新配置",
            "affected_component": component,
            "config_dirty": None,
            "backup_path": None,
        }

    # 步骤 1：先比较新旧内容，如果完全一致则无需保存、备份、校验、标记 dirty
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            old_content = f.read()
        if old_content == content:
            # 内容未变化时，检查组件是否已经 config_dirty
            if component == "nginx":
                try:
                    from runtime.wnmp_state import is_component_config_dirty
                    nginx_dirty = is_component_config_dirty(root_dir, "nginx")
                except Exception:
                    nginx_dirty = False
                if nginx_dirty:
                    # 内容未变化但 nginx 仍 dirty，提示仍需重载/重启，不清除 dirty
                    return {
                        "success": True,
                        "message": "配置未变化，但当前配置仍待重载/重启生效",
                        "affected_component": component,
                        "config_dirty": True,
                        "backup_path": None,
                    }
                else:
                    return {
                        "success": True,
                        "message": "配置未变化，无需重载",
                        "affected_component": component,
                        "config_dirty": None,
                        "backup_path": None,
                    }
            else:
                return {
                    "success": True,
                    "message": "配置未变化",
                    "affected_component": component,
                    "config_dirty": None,
                    "backup_path": None,
                }
    except Exception:
        pass  # 读取失败时继续正常保存流程

    # 步骤 2：自动备份（内容确实变化时才备份）
    if os.path.isfile(path):
        try:
            backup_dir = os.path.join(root_dir, "config", "backup")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, "{}.{}.bak".format(name, timestamp))
            shutil.copy2(path, backup_path)
        except Exception as e:
            return {
                "success": False,
                "message": "备份失败，已取消保存：{}".format(str(e)),
                "affected_component": component,
                "config_dirty": None,
                "backup_path": None,
            }

    # 步骤 3：写入文件
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(content)
    except Exception as e:
        return {
            "success": False,
            "message": "保存失败: " + str(e),
            "affected_component": component,
            "config_dirty": None,
            "backup_path": backup_path,
        }

    # 步骤 3：Nginx 配置校验（复用 test_nginx_config，确保与 start/reload/restart 一致）
    if component == "nginx":
        validation_result = _validate_nginx_config(root_dir, path, backup_path)
        if validation_result is not None:
            return validation_result

    # 步骤 4：标记组件 config_dirty（Nginx/PHP/MySQL 保存时均标记 dirty）
    if component in ("nginx", "php", "mysql"):
        try:
            from runtime.wnmp_state import mark_component_config_dirty
            mark_component_config_dirty(root_dir, component)
        except Exception as e:
            _log_warning(root_dir, "标记 config_dirty 失败: {}".format(str(e)))

    # 步骤 5：同步更新 runtime-config.php
    # 修复点：原直接调用 generate_runtime_config，绕过默认站点归属判断，
    # 会导致用户删除/替换默认 index.php 或删除整个 www 后，保存配置仍重建
    # runtime-config.php（甚至重建 www）。改为复用 update_runtime_config_for_start，
    # 与 cmd_start/cmd_restart 使用同一套判断规则：仅在默认 index.php 仍由 WNMP
    # 管理时才更新 runtime-config.php。
    try:
        from runtime.wnmp_config import load_config
        from runtime.wnmp_default_site import update_runtime_config_for_start
        cfg = load_config(root_dir)
        update_runtime_config_for_start(root_dir, cfg, None)
    except Exception as e:
        _log_warning(root_dir, "同步 runtime-config.php 失败: {}".format(str(e)))

    # 构建成功消息
    if component == "nginx":
        message = "配置已保存并校验通过，需重载/重启 Nginx 后生效"
    else:
        message = "配置已保存，重启对应组件后生效"

    # PHP/MySQL 也标记 config_dirty，前端据此显示"需重启生效"
    config_dirty = True if component in ("nginx", "php", "mysql") else None

    return {
        "success": True,
        "message": message,
        "affected_component": component,
        "config_dirty": config_dirty,
        "backup_path": backup_path,
    }


def _validate_nginx_config(root_dir, config_path, backup_path):
    """校验 Nginx 配置，失败时回滚。

    复用 runtime.wnmp_nginx.test_nginx_config()，确保与 start/reload/restart 使用同一套规则。

    Returns:
        None: 校验通过
        dict: 校验失败时的错误响应
    """
    try:
        from runtime.wnmp_nginx import test_nginx_config
        # test_nginx_config 需要 cfg 和 logger 参数
        from runtime.wnmp_config import load_config
        from runtime.wnmp_log import setup_logging  # 修复：原 create_logger 不存在，改为 setup_logging
        cfg = load_config(root_dir)
        logger = setup_logging(root_dir)
        ok, output = test_nginx_config(root_dir, cfg, logger)
    except Exception as e:
        # test_nginx_config 调用失败：回滚
        rollback_msg = _rollback_backup(config_path, backup_path)
        return {
            "success": False,
            "message": "Nginx 配置校验异常，已恢复备份: " + str(e) + rollback_msg,
            "affected_component": "nginx",
            "config_dirty": None,
            "backup_path": backup_path,
        }

    if not ok:
        # 校验失败：回滚
        error_msg = (output or "").strip()
        rollback_msg = _rollback_backup(config_path, backup_path)
        return {
            "success": False,
            "message": "Nginx 配置校验失败，已恢复备份: " + error_msg + rollback_msg,
            "affected_component": "nginx",
            "config_dirty": None,
            "backup_path": backup_path,
        }

    return None  # 校验通过


def _rollback_backup(config_path, backup_path):
    """恢复备份文件。返回附加消息（空字符串表示成功）。"""
    if not backup_path or not os.path.isfile(backup_path):
        return ""
    try:
        shutil.copy2(backup_path, config_path)
        return ""
    except Exception as restore_err:
        return " | 恢复备份也失败: " + str(restore_err)


def _save_runtime_config(root_dir, content):
    """保存 runtime.ini 运行器配置，独立于组件配置逻辑。

    - 不触发 nginx -t
    - 不标记任何组件 config_dirty
    - 不影响 PHP/MySQL 状态
    - 不触发 pending_reload 语义
    - 保存前备份，保存失败返回 JSON 错误
    - 不在日志中输出敏感信息
    """
    # 路径收敛：通过统一路径模块获取 runtime.ini 绝对路径
    path = get_runtime_ini_path(root_dir)

    # 文件存在性检查：不允许通过接口创建未知路径文件
    if not os.path.isfile(path):
        return {
            "success": False,
            "message": "面板配置文件不存在，不能通过编辑器创建",
            "affected_component": None,
            "config_dirty": None,
            "backup_path": None,
        }

    # 内容未变化时不创建备份、不写入文件
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            old_content = f.read()
        if old_content == content:
            return {
                "success": True,
                "message": "面板配置未变化",
                "affected_component": None,
                "config_dirty": None,
                "backup_path": None,
            }
    except Exception:
        pass  # 读取失败时继续正常保存流程

    # 备份
    backup_path = None
    try:
        backup_dir = os.path.join(root_dir, "config", "backup")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, "runtime.{}.bak".format(timestamp))
        shutil.copy2(path, backup_path)
    except Exception as e:
        return {
            "success": False,
            "message": "备份面板配置失败，已取消保存：{}".format(str(e)),
            "affected_component": None,
            "config_dirty": None,
            "backup_path": None,
        }

    # 写入文件
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(content)
    except Exception as e:
        return {
            "success": False,
            "message": "保存面板配置失败: " + str(e),
            "affected_component": None,
            "config_dirty": None,
            "backup_path": backup_path,
        }

    # runtime.ini 保存成功，不触发任何组件 dirty/pending_reload
    return {
        "success": True,
        "message": "面板配置已保存，部分设置需重启面板或重新初始化后生效",
        "affected_component": None,
        "config_dirty": None,
        "backup_path": backup_path,
    }


def _log_warning(root_dir, message):
    """写入 panel_server.log 警告。"""
    try:
        log_dir = os.path.join(root_dir, "logs", "panel")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "panel_server.log")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("[{}] WARNING: {}\n".format(timestamp, message))
    except Exception:
        pass
