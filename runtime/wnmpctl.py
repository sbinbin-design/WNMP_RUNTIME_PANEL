# -*- coding: utf-8 -*-
"""
WNMP Control Tool - Python main entry point
Replaces PowerShell-based runtime scripts.
Usage: python runtime/wnmpctl.py <command>

Commands:
  start               Start all services (auto-init on first run)
  stop                Stop all services
  restart             Restart all services
  status              Show configuration summary and service status
  open                Open browser to default page
  reset-config        Reset component config files to defaults (does NOT touch runtime.ini, data, www, logs)
  cert                Manage certificates (see cert --help)
  safe-start          Start with English console summary only
  install-autostart   Install Windows scheduled task for auto-start
  uninstall-autostart Remove auto-start scheduled task
  autostart-status    Query auto-start scheduled task status
"""
import os
import sys
import io
import json
import time

# 修复导入顺序：必须先计算 _script_dir、_root_dir 并插入 sys.path，
# 再执行 from runtime.xxx import ...，否则直接运行 python runtime/wnmpctl.py
# 会报 ModuleNotFoundError: No module named 'runtime'
_script_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.normpath(os.path.join(_script_dir, ".."))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

# 确保工作目录为项目根目录，防止 schtasks 未设置 WorkingDirectory 时相对路径异常
try:
    os.chdir(_root_dir)
except Exception:
    pass

# sys.path 已初始化，现在可以安全导入 runtime 包
from runtime.wnmp_component_paths import (
    get_nginx_conf_path, get_nginx_site_conf_path,
    get_php_ini_path, get_php_cgi_ini_path, get_mysql_ini_path,
    get_nginx_vhosts_dir, get_nginx_custom_http_dir, get_nginx_custom_server_dir,
    get_php_user_ini_path, get_mysql_user_ini_path,
)

# Ensure stdout/stderr use UTF-8 with error replacement
# 使用安全的 reconfigure 方式，避免 I/O operation on closed file
from runtime.wnmp_stdio import configure_stdio_utf8
configure_stdio_utf8()


def get_root_dir():
    """Get tool root directory (parent of runtime/)."""
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _ensure_directories(root_dir, logger=None):
    """创建运行所需的目录结构。

    失败时抛出异常，中断启动。
    """
    dirs = [
        os.path.join(root_dir, "logs", "nginx"),
        os.path.join(root_dir, "logs", "php"),
        os.path.join(root_dir, "logs", "mysql"),
        os.path.join(root_dir, "logs", "runtime"),
        os.path.join(root_dir, "tmp"),
        os.path.join(root_dir, "runtime", "pids"),
        os.path.join(root_dir, "temp", "client_body_temp"),
        os.path.join(root_dir, "temp", "proxy_temp"),
        os.path.join(root_dir, "temp", "fastcgi_temp"),
        os.path.join(root_dir, "temp", "uwsgi_temp"),
        os.path.join(root_dir, "temp", "scgi_temp"),
        os.path.join(root_dir, "config", "certs"),
    ]
    from runtime.wnmp_path import resolve_path
    from runtime import wnmp_config
    from runtime.wnmp_log import log_error

    # 从配置读取 MYSQL_DATA_DIR 并创建
    cfg = _load_config_cached()
    if cfg:
        mysql_data = resolve_path(root_dir, wnmp_config.get(cfg, "MYSQL_DATA_DIR", "./data/mysql"))
        dirs.append(mysql_data)

    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            log_error(logger, "Failed to create directory: " + d)
            log_error(logger, "Error: " + str(e))
            raise Exception("Directory creation failed: " + d)


def _ensure_config_layout_before_component_start(root_dir, cfg, logger):
    """单组件启动前保障配置布局。

    P2 启动前配置布局保障小收口：cmd_start() 已全量启动前调用，
    但单组件 cmd_start_nginx/php/mysql 独立启动时也需要保障。

    保障规则参见 ensure_component_configs_ready()：
    - 创建必要目录
    - 迁移旧 config 到新组件目录（只补缺失，不覆盖已有）
    - 生成缺失的活跃配置文件

    Args:
        root_dir: 项目根目录
        cfg: 配置对象
        logger: 日志记录器

    Returns:
        tuple: (success: bool, message: str)
    """
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_templates import ensure_component_configs_ready

    log_info(logger, "Ensuring component config layout before starting...")
    ok, msg = ensure_component_configs_ready(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "Config layout check failed: " + msg)
        print("ERROR: Config layout check failed: " + msg)
        return False, msg
    log_info(logger, "Config layout ready: " + msg)
    return True, msg


def _check_binaries(root_dir, logger):
    """检测二进制文件是否存在。

    返回 (True, None) 或 (False, missing_list)
    """
    from runtime.wnmp_log import log_error, log_warn

    binaries = [
        ("Nginx", "bin/nginx/nginx.exe"),
        ("PHP-CGI", "bin/php/php-cgi.exe"),
        ("PHP CLI", "bin/php/php.exe"),
        ("MySQL", "bin/mysql/bin/mysqld.exe"),
        ("MySQL Admin", "bin/mysql/bin/mysqladmin.exe"),
        ("MySQL Client", "bin/mysql/bin/mysql.exe"),
    ]

    missing = []
    for name, rel_path in binaries:
        full_path = os.path.join(root_dir, rel_path)
        if not os.path.isfile(full_path):
            log_error(logger, "Binary not found: " + rel_path)
            missing.append(name)

    openssl_path = os.path.join(root_dir, "bin/openssl/openssl.exe")
    if not os.path.isfile(openssl_path):
        log_warn(logger, "OpenSSL not found: bin/openssl/openssl.exe (optional)")

    if missing:
        return False, missing
    return True, None


def _check_php_extensions(root_dir, logger):
    """检测 PHP 扩展 DLL 是否存在。

    仅记录警告，不阻断启动。
    """
    from runtime.wnmp_log import log_warn

    php_ext_dir = os.path.join(root_dir, "bin", "php", "ext")

    extensions = [
        "php_openssl.dll",
        "php_mysqli.dll",
        "php_pdo_mysql.dll",
        "php_curl.dll",
        "php_mbstring.dll",
        "php_fileinfo.dll",
        "php_gd.dll",
        "php_zip.dll",
    ]

    missing = []
    for ext in extensions:
        full_path = os.path.join(php_ext_dir, ext)
        if not os.path.isfile(full_path):
            missing.append(ext)

    if missing:
        log_warn(logger, "PHP extension DLLs not found, extensions may not work: " + ", ".join(missing))

    return True


def _check_ports(root_dir, cfg, logger):
    """检测端口是否可用，区分本项目组件占用和外部程序占用。

    基于 check_listener_ownership 判断归属：
    - 本项目组件占用（status=running）：视为已运行，不报冲突
    - 外部程序占用（status=external）：报端口冲突
    - 归属未知（status=unknown）：报端口冲突（保守策略）
    - 端口未监听（status=stopped）：端口可用

    返回 (True, None) 或 (False, conflict_list)
    conflict_list 中每项为 dict: {port, component, owner_type, owner_path, is_ours, message}
    """
    from runtime.wnmp_log import log_error, log_warn, log_info
    from runtime.wnmp_process import check_listener_ownership
    from runtime import wnmp_config

    conflicts = []

    # 构建各组件的 exe 路径
    nginx_exe = os.path.normpath(os.path.join(root_dir, "bin", "nginx", "nginx.exe"))
    php_cgi_exe = os.path.normpath(os.path.join(root_dir, "bin", "php", "php-cgi.exe"))
    mysqld_exe = os.path.normpath(os.path.join(root_dir, "bin", "mysql", "bin", "mysqld.exe"))

    def _build_conflict(port, component, ownership, proto_label=""):
        """根据 ownership 结果构建冲突条目，含 owner_path 和精确 message。"""
        owner_type = ownership["status"]  # external 或 unknown
        owner_path = ownership.get("path")
        is_ours = ownership.get("is_ours", False)
        if owner_type == "external":
            if owner_path:
                msg = "{}端口 {} 被外部程序 {} 占用".format(proto_label, port, owner_path)
            else:
                msg = "{}端口 {} 被外部程序占用，无法确认路径".format(proto_label, port)
        else:  # unknown
            msg = "{}端口 {} 已被占用，但无法确认进程归属，为避免误操作已阻断启动".format(proto_label, port)
        return {"port": port, "component": component, "owner_type": owner_type,
                "owner_path": owner_path, "is_ours": is_ours, "message": msg}

    # 检测 Nginx 所有实际 listen 端口
    eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
    for p in eff["http"]:
        ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                             root_dir=root_dir, timeout=3, logger=logger)
        if ownership["status"] == "running":
            log_info(logger, "HTTP port {} is already occupied by this project's Nginx, treating as already running".format(p))
        elif ownership["status"] in ("external", "unknown"):
            log_error(logger, "HTTP port {} conflict: {}".format(p, ownership["message"]))
            conflicts.append(_build_conflict(p, "nginx", ownership, "HTTP "))
    for p in eff["https"]:
        ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                             root_dir=root_dir, timeout=3, logger=logger)
        if ownership["status"] == "running":
            log_info(logger, "HTTPS port {} is already occupied by this project's Nginx, treating as already running".format(p))
        elif ownership["status"] in ("external", "unknown"):
            log_error(logger, "HTTPS port {} conflict: {}".format(p, ownership["message"]))
            conflicts.append(_build_conflict(p, "nginx", ownership, "HTTPS "))

    # 检测 PHP-CGI 端口（优先从 php-cgi.ini 解析）
    php_cgi_host, php_cgi_port = wnmp_config.get_effective_php_cgi_host_port(root_dir, cfg)
    ownership = check_listener_ownership(php_cgi_port, php_cgi_exe, host=php_cgi_host,
                                         root_dir=root_dir, timeout=3, logger=logger)
    if ownership["status"] == "running":
        log_info(logger, "PHP-CGI port {}:{} is already occupied by this project's PHP-CGI, treating as already running".format(php_cgi_host, php_cgi_port))
    elif ownership["status"] in ("external", "unknown"):
        log_error(logger, "PHP-CGI port {}:{} conflict: {}".format(php_cgi_host, php_cgi_port, ownership["message"]))
        conflicts.append(_build_conflict(php_cgi_port, "php", ownership))

    # 检测 MySQL 端口（优先从 my.ini 解析）
    mysql_host = wnmp_config.get(cfg, "MYSQL_HOST", "127.0.0.1")
    mysql_port = wnmp_config.get_effective_mysql_port(root_dir, cfg)
    ownership = check_listener_ownership(int(mysql_port), mysqld_exe, host=mysql_host,
                                         root_dir=root_dir, timeout=3, logger=logger)
    if ownership["status"] == "running":
        log_info(logger, "MySQL port {}:{} is already occupied by this project's MySQL, treating as already running".format(mysql_host, mysql_port))
    elif ownership["status"] in ("external", "unknown"):
        log_error(logger, "MySQL port {}:{} conflict: {}".format(mysql_host, mysql_port, ownership["message"]))
        conflicts.append(_build_conflict(int(mysql_port), "mysql", ownership))

    if conflicts:
        return False, conflicts
    return True, None


def _rollback_started_components(root_dir, cfg, logger, started_components):
    """回滚已启动的组件。

    按 Nginx -> PHP-CGI -> MySQL 顺序回滚。
    started_components 是已启动组件名称列表，如 ["mysql", "php-cgi", "nginx"]
    """
    from runtime.wnmp_log import log_info, log_warn, log_error

    # 回滚顺序：Nginx -> PHP-CGI -> MySQL
    rollback_order = ["nginx", "php-cgi", "mysql"]

    log_info(logger, "=== Starting rollback ===")

    for component in rollback_order:
        if component not in started_components:
            continue

        log_info(logger, "Rolling back component: " + component)

        try:
            if component == "nginx":
                from runtime.wnmp_nginx import stop_nginx
                stop_nginx(root_dir, cfg, logger)
            elif component == "php-cgi":
                from runtime.wnmp_php import stop_php_cgi
                stop_php_cgi(root_dir, cfg, logger)
            elif component == "mysql":
                from runtime.wnmp_mysql import stop_mysql
                stop_mysql(root_dir, cfg, logger)
        except Exception as e:
            log_error(logger, "Rollback error for {}: {}".format(component, str(e)))

    log_info(logger, "=== Rollback completed ===")


# 全局配置缓存，避免重复读取
_config_cache = None


def _load_config_cached(root_dir=None):
    """加载配置（带缓存）。"""
    global _config_cache
    if _config_cache is None:
        from runtime.wnmp_config import load_config
        if root_dir is None:
            root_dir = get_root_dir()
        _config_cache = load_config(root_dir)
    return _config_cache


def _get_panel_result_file():
    """解析 --panel-result-file <path> 参数，仅 Panel 调用时使用。

    用于一次性跨进程传递 MySQL 初始密码，不作为本地持久密码文件。
    """
    for i in range(len(sys.argv)):
        if sys.argv[i] == "--panel-result-file" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _write_panel_result(result_file, data):
    """写入临时结果文件，失败不抛异常也不记录明文密码。"""
    try:
        os.makedirs(os.path.dirname(result_file), exist_ok=True)
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass  # 不记录明文密码到日志


def _do_first_time_init(root_dir, cfg, logger):
    """Execute one-time initialization: dirs, configs, cert, default site, MySQL init.

    Called by cmd_start only when state.json shows INITIALIZED=false.
    """
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime import wnmp_config
    from runtime.wnmp_path import is_default_web_root, resolve_path
    from runtime.wnmp_state import (
        mark_config_generated,
        mark_default_site_initialized, mark_cert_initialized,
        set_init_phase,
    )

    # 标记初始化开始
    set_init_phase(root_dir, "preparing_config")
    log_info(logger, "init_phase=preparing_config: starting first-time initialization")

    log_info(logger, "=== First-time initialization ===")
    print("First-time initialization...")

    # 1. Create directories
    try:
        _ensure_directories(root_dir, logger)
    except Exception as e:
        log_error(logger, "Failed to create directories: " + str(e))
        print("   ERROR: " + str(e))
        return False
    print("   Directories created.")

    # 2. Check binaries
    ok, missing = _check_binaries(root_dir, logger)
    if not ok:
        log_error(logger, "Binary check failed, missing: " + ", ".join(missing))
        print("   ERROR: Missing binaries: " + ", ".join(missing))
        return False
    print("   Binaries OK.")

    # 3. Check PHP extensions (non-blocking)
    _check_php_extensions(root_dir, logger)

    # 4. Generate certificate (one-time, only if missing)
    auto_gen_cert = wnmp_config.get_int(cfg, "AUTO_GENERATE_CERT", 1)
    if auto_gen_cert == 1:
        from runtime.wnmp_openssl import ensure_self_signed_cert
        cert_ok, cert_msg = ensure_self_signed_cert(root_dir, cfg, logger, force=False)
        if cert_ok:
            mark_cert_initialized(root_dir)
            print("   Certificate: " + cert_msg)
        else:
            log_warn(logger, "Certificate not available: " + cert_msg)
            print("   Certificate: " + cert_msg)
    else:
        print("   AUTO_GENERATE_CERT=0, skipping certificate.")

    # 5. Generate config files from templates (one-time)
    from runtime.wnmp_templates import generate_all_configs
    ok = generate_all_configs(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "Config generation failed")
        print("   ERROR: Config generation failed")
        return False
    mark_config_generated(root_dir)
    print("   Config files generated.")

    # 6. Generate default site (only if WEB_ROOT is default ./www)
    web_root_raw = wnmp_config.get(cfg, "WEB_ROOT")
    web_root = resolve_path(root_dir, web_root_raw)

    if is_default_web_root(root_dir, web_root_raw):
        os.makedirs(web_root, exist_ok=True)
        # 统一使用 init_default_index 生成默认页，避免重复逻辑
        from runtime.wnmp_default_site import init_default_index
        index_path, created, status = init_default_index(web_root)
        if created:
            print("   Default index.php created.")
        mark_default_site_initialized(root_dir)

        # Generate runtime-config.php
        from runtime.wnmp_default_site import generate_runtime_config
        generate_runtime_config(web_root, cfg, root_dir)
        print("   runtime-config.php created.")
    else:
        print("   WEB_ROOT is custom directory, skipping default site.")

    # 7. Copy user config examples
    # 路径收敛：通过统一路径模块获取 php.user.ini 和 my.user.ini 路径
    import shutil
    examples = [
        ("config/php/php.user.ini.example", get_php_user_ini_path(root_dir)),
        ("config/mysql/my.user.ini.example", get_mysql_user_ini_path(root_dir)),
    ]
    for example_src, user_dest in examples:
        src = os.path.join(root_dir, example_src)
        dest = user_dest
        if os.path.isfile(src) and not os.path.isfile(dest):
            shutil.copy2(src, dest)

    # 8. Ensure nginx custom and vhosts directories
    # 路径收敛：通过统一路径模块获取目录路径
    nginx_custom_http = get_nginx_custom_http_dir(root_dir)
    nginx_custom_server = get_nginx_custom_server_dir(root_dir)
    nginx_vhosts = get_nginx_vhosts_dir(root_dir)
    os.makedirs(nginx_custom_http, exist_ok=True)
    os.makedirs(nginx_custom_server, exist_ok=True)
    os.makedirs(nginx_vhosts, exist_ok=True)

    # 8b. Copy vhosts example files if not exist
    # 路径收敛：目标路径通过统一路径模块推导，避免 P2 切换时漏改
    vhosts_examples = [
        ("config/nginx/vhosts/example.local.conf.disabled", "example.local.conf.disabled"),
        ("config/nginx/vhosts/example-ssl.local.conf.disabled", "example-ssl.local.conf.disabled"),
    ]
    for example_src, vhost_dest_name in vhosts_examples:
        src = os.path.join(root_dir, example_src)
        # 目标路径从统一路径模块推导，不再硬编码 config/nginx/vhosts
        dest = os.path.join(nginx_vhosts, vhost_dest_name)
        if os.path.isfile(src) and not os.path.isfile(dest):
            shutil.copy2(src, dest)

    # 8c. Ensure vhosts placeholder.conf exists
    vhosts_placeholder = os.path.join(nginx_vhosts, "placeholder.conf")
    if not os.path.isfile(vhosts_placeholder):
        # 从绝对路径反推相对路径用于展示文案，不手写 config/nginx/vhosts
        vhosts_rel = os.path.relpath(nginx_vhosts, root_dir).replace(os.sep, "/")
        with open(vhosts_placeholder, "w", encoding="utf-8") as f:
            f.write("# Nginx Virtual Hosts Placeholder\n")
            f.write("# Place your complete server {{ ... }} virtual host configs in {}/\n".format(vhosts_rel))
            f.write("# This file ensures nginx -t does not fail when the directory is empty.\n")

    # 9. Add to system PATH if configured
    from runtime.wnmp_state import mark_env_path_configured
    add_to_path = wnmp_config.get_int(cfg, "ADD_TO_SYSTEM_PATH", 1)
    add_openssl = wnmp_config.get_int(cfg, "ADD_OPENSSL_TO_SYSTEM_PATH", 0) == 1

    if add_to_path == 1:
        from runtime.wnmp_env import is_admin, add_tool_paths_to_system_path, get_tool_paths
        if is_admin():
            ok, added, skipped, err = add_tool_paths_to_system_path(root_dir, add_openssl)
            if ok:
                tool_paths = get_tool_paths(root_dir, add_openssl)
                mark_env_path_configured(root_dir, configured=True, items=tool_paths)
                print("   System PATH: {} paths added, {} already existed".format(added, skipped))
            else:
                mark_env_path_configured(root_dir, configured=False, reason=err)
                print("   System PATH: failed to add - " + str(err))
        else:
            mark_env_path_configured(root_dir, configured=False, reason="no_admin_privilege")
            print("   System PATH: skipped (requires admin, run WNMPPanel.exe as admin or use bin\\python\\python.exe runtime\\wnmpctl.py install-env)")
            log_warn(logger, "System PATH not modified: requires admin privileges")
    else:
        print("   System PATH: disabled by ADD_TO_SYSTEM_PATH=0")

    # 10. Write partial init state (INITIALIZED=true is written after all services start)
    print("   Phase 1 init state saved to runtime/state.json")

    log_success(logger, "First-time initialization phase 1 completed (config, cert, default site)")
    print("Initialization phase 1 completed (config, cert, default site).")
    print()
    return True


def _is_nginx_need_action_message(message):
    """判断 start_nginx 返回的 message 是否属于 need_action（非真失败）。

    need_action 场景：Nginx 正在运行但配置未应用、旧端口残留等，
    不应回滚已启动的 PHP/MySQL，只需提示用户重载或重启。
    真失败场景：nginx -t 校验失败、外部端口占用、启动进程失败等，
    需要回滚已启动组件。
    """
    if not message:
        return False
    msg = str(message)
    _NEED_ACTION_KEYWORDS = [
        "配置已修改尚未应用",
        "配置尚未应用",
        "正在旧配置下运行",
        "请执行重载或重启",
        "旧端口仍由本项目 Nginx 占用",
        "旧端口仍被本项目 Nginx 占用",
        "旧配置仍在运行",
        "pending_reload",
        "仍由本项目 nginx 监听",
        "仍被本项目 Nginx 占用",
    ]
    for kw in _NEED_ACTION_KEYWORDS:
        if kw in msg:
            return True
    return False


def cmd_init(root_dir, cfg, logger):
    """Handle init command - first-time initialization then start services.

    Uses INIT_PHASE to track progress. Only for first-time initialization.
    If already initialized, returns error.
    """
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime import wnmp_config
    from runtime.wnmp_state import is_initialized, try_backfill_state, set_init_phase, mark_initialized

    log_info(logger, "=== WNMP Runtime Initialization ===")

    # 已初始化时拒绝重复初始化
    if is_initialized(root_dir):
        log_info(logger, "Environment already initialized, use start instead")
        print("环境已初始化，请使用启动环境")
        return 1

    # Migration compatibility: backfill state.json if old environment detected
    try_backfill_state(root_dir, logger)

    # First run: execute one-time init
    ok = _do_first_time_init(root_dir, cfg, logger)
    if not ok:
        set_init_phase(root_dir, "failed")
        log_error(logger, "First-time initialization failed")
        print("ERROR: Initialization failed. Check logs for details.")
        return 1

    # Post-init: start services (same flow as cmd_start but using INIT_PHASE)

    # 1. Ensure directories exist
    try:
        _ensure_directories(root_dir, logger)
    except Exception as e:
        log_error(logger, "Failed to create directories: " + str(e))
        print("ERROR: " + str(e))
        set_init_phase(root_dir, "failed")
        return 1

    # 2. Check binaries
    ok, missing = _check_binaries(root_dir, logger)
    if not ok:
        log_error(logger, "Binary check failed, missing: " + ", ".join(missing))
        print("ERROR: Missing binaries: " + ", ".join(missing))
        set_init_phase(root_dir, "failed")
        return 1

    # 3. Check PHP extensions (non-blocking)
    _check_php_extensions(root_dir, logger)

    # 4. Certificate check
    enable_https = wnmp_config.is_effective_nginx_https_enabled(root_dir, cfg)
    if enable_https:
        from runtime.wnmp_openssl import is_cert_valid, get_cert_paths
        _, cert_path, key_path = get_cert_paths(root_dir)
        if not is_cert_valid(cert_path, key_path):
            log_warn(logger, "HTTPS certificate missing, falling back to HTTP mode")
            enable_https = False
        else:
            log_info(logger, "HTTPS certificate verified")

    # 5. Check ports
    ok, conflicts = _check_ports(root_dir, cfg, logger)
    if not ok:
        conflict_msgs = [c["message"] for c in conflicts]
        log_error(logger, "Port check failed: " + "; ".join(conflict_msgs))
        print("ERROR: " + "; ".join(conflict_msgs))
        set_init_phase(root_dir, "failed")
        return 1

    # 6. Start services: MySQL -> PHP-CGI -> Nginx
    started_components = []
    mysql_result = {"state": "unknown", "success": False, "need_action": False, "failed": False, "message": ""}
    php_result = {"state": "unknown", "success": False, "need_action": False, "failed": False, "message": ""}
    nginx_result = {"state": "unknown", "success": False, "need_action": False, "failed": False, "message": ""}

    panel_result_file = _get_panel_result_file()

    # 6.1 Start MySQL
    log_info(logger, "Starting MySQL...")
    from runtime.wnmp_mysql import start_mysql
    from runtime.wnmp_process import check_listener_ownership
    mysqld_exe = os.path.normpath(os.path.join(root_dir, "bin", "mysql", "bin", "mysqld.exe"))
    mysql_host_chk = wnmp_config.get(cfg, "MYSQL_HOST", "127.0.0.1")
    mysql_port_chk = wnmp_config.get_effective_mysql_port(root_dir, cfg)
    mysql_ownership = check_listener_ownership(int(mysql_port_chk), mysqld_exe, host=mysql_host_chk,
                                                root_dir=root_dir, timeout=3, logger=logger)
    msg = None
    if mysql_ownership["status"] == "running":
        mysql_result["state"] = "already_running"
        mysql_result["success"] = True
        log_info(logger, "MySQL is already running on port {}, skipping start".format(mysql_port_chk))
        print("MySQL: already running, skipped")
    else:
        ok, msg = start_mysql(root_dir, cfg, logger, panel_result_file=panel_result_file)
        if not ok:
            mysql_result["state"] = "failed"
            mysql_result["failed"] = True
            mysql_result["message"] = str(msg.get("error", msg) if isinstance(msg, dict) else msg)
            if isinstance(msg, dict):
                log_error(logger, "MySQL start failed: " + str(msg.get("error", "unknown error")))
            else:
                log_error(logger, "MySQL start failed: " + str(msg))
            if panel_result_file and isinstance(msg, dict) and "mysql_init_password" in msg:
                _write_panel_result(panel_result_file, {"mysql_root_password": msg["mysql_init_password"]})
            set_init_phase(root_dir, "failed")
            log_info(logger, "init_phase=failed: MySQL start failed")
            _rollback_started_components(root_dir, cfg, logger, started_components)
            return 1
        mysql_result["state"] = "started"
        mysql_result["success"] = True
        started_components.append("mysql")

    # MySQL 初始化成功后，将 root 密码写入临时结果文件
    if panel_result_file and isinstance(msg, dict) and "mysql_init_password" in msg:
        _write_panel_result(panel_result_file, {"mysql_root_password": msg["mysql_init_password"]})

    # MySQL 初始化成功后标记 init_phase=mysql_secure_init
    set_init_phase(root_dir, "mysql_secure_init")
    log_info(logger, "init_phase=mysql_secure_init: MySQL init succeeded, services starting...")

    # 6.2 Start PHP-CGI
    log_info(logger, "Starting PHP-CGI...")
    set_init_phase(root_dir, "starting_php_cgi")
    log_info(logger, "init_phase=starting_php_cgi")
    from runtime.wnmp_php import start_php_cgi
    php_cgi_exe = os.path.normpath(os.path.join(root_dir, "bin", "php", "php-cgi.exe"))
    php_cgi_host_chk, php_cgi_port_chk = wnmp_config.get_effective_php_cgi_host_port(root_dir, cfg)
    php_ownership = check_listener_ownership(php_cgi_port_chk, php_cgi_exe, host=php_cgi_host_chk,
                                              root_dir=root_dir, timeout=3, logger=logger)
    if php_ownership["status"] == "running":
        php_result["state"] = "already_running"
        php_result["success"] = True
        log_info(logger, "PHP-CGI is already running on port {}, skipping start".format(php_cgi_port_chk))
        print("PHP-CGI: already running, skipped")
    else:
        ok, msg = start_php_cgi(root_dir, cfg, logger)
        if not ok:
            php_result["state"] = "failed"
            php_result["failed"] = True
            php_result["message"] = str(msg)
            log_error(logger, "PHP-CGI start failed: " + str(msg))
            set_init_phase(root_dir, "failed")
            log_info(logger, "init_phase=failed: PHP-CGI start failed")
            _rollback_started_components(root_dir, cfg, logger, started_components)
            return 1
        php_result["state"] = "started"
        php_result["success"] = True
        started_components.append("php-cgi")

    # 6.3 Start Nginx
    log_info(logger, "Starting Nginx...")
    set_init_phase(root_dir, "starting_nginx")
    log_info(logger, "init_phase=starting_nginx")
    from runtime.wnmp_nginx import start_nginx, detect_nginx_runtime_ports
    nginx_exe = os.path.normpath(os.path.join(root_dir, "bin", "nginx", "nginx.exe"))
    eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
    desired_ports = set(eff["http"] + eff["https"])

    runtime_ports_list = detect_nginx_runtime_ports(root_dir, nginx_exe, logger=logger, fast_mode=True, timeout=3)
    runtime_ports = set(runtime_ports_list)
    stale_ports = runtime_ports - desired_ports

    from runtime.wnmp_state import is_component_config_dirty
    nginx_config_dirty = is_component_config_dirty(root_dir, "nginx")

    if desired_ports and desired_ports.issubset(runtime_ports) and not stale_ports and not nginx_config_dirty:
        nginx_result["state"] = "already_running"
        nginx_result["success"] = True
        log_info(logger, "Nginx is already running on all desired ports {}, skipping start".format(desired_ports))
        print("Nginx: already running, skipped")
    elif runtime_ports:
        nginx_result["state"] = "pending_reload"
        nginx_result["need_action"] = True
        if nginx_config_dirty and desired_ports.issubset(runtime_ports) and not stale_ports:
            msg = "Nginx 正在运行，但配置已修改尚未应用，请执行重载或重启 Nginx 生效"
        elif stale_ports:
            msg = "当前 Nginx 正在旧配置下运行（旧端口 {} 仍监听），配置尚未应用，请执行重载或重启 Nginx 生效".format(sorted(stale_ports))
        else:
            missing = desired_ports - runtime_ports
            msg = "当前 Nginx 正在运行但端口 {} 未生效，配置尚未应用，请执行重载或重启 Nginx 生效".format(sorted(missing))
        nginx_result["message"] = msg
        log_info(logger, msg)
        print("Nginx: " + msg)
    else:
        ok, msg = start_nginx(root_dir, cfg, logger)
        if not ok:
            if _is_nginx_need_action_message(msg):
                nginx_result["state"] = "pending_reload"
                nginx_result["need_action"] = True
                nginx_result["message"] = str(msg)
                log_info(logger, "Nginx need_action: " + str(msg))
                print("Nginx: " + str(msg))
            else:
                nginx_result["state"] = "failed"
                nginx_result["failed"] = True
                nginx_result["message"] = str(msg)
                log_error(logger, "Nginx start failed: " + str(msg))
                set_init_phase(root_dir, "failed")
                log_info(logger, "init_phase=failed: Nginx start failed")
                _rollback_started_components(root_dir, cfg, logger, started_components)
                return 1
        else:
            nginx_result["state"] = "started"
            nginx_result["success"] = True
            started_components.append("nginx")

    # 7. Wait for Nginx ports
    if nginx_result["state"] == "started":
        set_init_phase(root_dir, "verifying_services")
        log_info(logger, "init_phase=verifying_services: verifying Nginx ports...")
        eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
        from runtime.wnmp_process import wait_for_port_open
        all_nginx_ports = [(p, "HTTP") for p in eff["http"]] + [(p, "HTTPS") for p in eff["https"]]
        for port, proto in all_nginx_ports:
            log_info(logger, "Waiting for {} port {}...".format(proto, port))
            if not wait_for_port_open("127.0.0.1", port, timeout=15, logger=logger):
                log_error(logger, "{} port {} timeout".format(proto, port))
                set_init_phase(root_dir, "failed")
                log_info(logger, "init_phase=failed: Nginx port {} timeout".format(port))
                _rollback_started_components(root_dir, cfg, logger, started_components)
                return 1

    # 7b. 所有服务启动确认完成，写入 INITIALIZED=true
    mark_initialized(root_dir)
    log_info(logger, "init_phase=completed: all services started, INITIALIZED=true written to state.json")
    print("All services started. Initialization completed.")

    # 8. 汇总模块结果
    if nginx_result["need_action"]:
        log_error(logger, "环境未完全启动：Nginx 配置已修改但尚未应用，请先重载或重启 Nginx；PHP/MySQL 状态不受影响")
        print("环境未完全启动：Nginx 配置已修改但尚未应用，请先重载或重启 Nginx；PHP/MySQL 状态不受影响")
        try:
            from runtime.wnmp_default_site import generate_runtime_config
            from runtime.wnmp_path import resolve_path
            web_root = resolve_path(root_dir, wnmp_config.get(cfg, "WEB_ROOT", "./www"))
            generate_runtime_config(web_root, cfg, root_dir)
        except Exception:
            pass
        return 1

    # 9. Open browser if not autostart
    autostart_mode = "--autostart" in sys.argv
    if not autostart_mode and wnmp_config.get_int(cfg, "AUTO_OPEN_BROWSER", 1) == 1:
        from runtime.wnmp_open import open_browser
        open_browser(cfg, root_dir)

    log_success(logger, "WNMP Runtime initialized and started successfully")

    try:
        from runtime.wnmp_default_site import generate_runtime_config
        from runtime.wnmp_path import resolve_path
        web_root = resolve_path(root_dir, wnmp_config.get(cfg, "WEB_ROOT", "./www"))
        generate_runtime_config(web_root, cfg, root_dir)
    except Exception:
        pass

    if not autostart_mode:
        from runtime.wnmp_open import build_open_url
        url = build_open_url(cfg, root_dir)
        print("WNMP Runtime initialized successfully.")
        print("Open browser to " + url)

    return 0


def cmd_start(root_dir, cfg, logger):
    """Handle start command - start services for already-initialized environment.

    Uses START_PHASE (not INIT_PHASE) to track progress.
    If environment is not initialized, returns error instead of auto-initializing.
    """
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime import wnmp_config
    from runtime.wnmp_state import is_initialized, try_backfill_state, set_start_phase, clear_start_phase

    autostart_mode = "--autostart" in sys.argv

    log_info(logger, "=== WNMP Runtime Starting ===")
    if autostart_mode:
        log_info(logger, "Auto-start mode: no browser will be opened")

    # 未初始化时拒绝启动，提示先初始化
    if not is_initialized(root_dir):
        log_error(logger, "Environment not initialized, please run init first")
        print("ERROR: 环境未初始化，请先初始化环境")
        return 1

    # Backfill missing state fields for old state.json compatibility
    from runtime.wnmp_state import backfill_missing_fields
    backfill_missing_fields(root_dir, logger)

    # 清除可能残留的 INIT_PHASE（已初始化环境不应有初始化阶段残留）
    from runtime.wnmp_state import set_init_phase
    set_init_phase(root_dir, "completed")

    # Post-init: only check and start services, no config regeneration

    # 1. Ensure directories exist
    try:
        _ensure_directories(root_dir, logger)
    except Exception as e:
        log_error(logger, "Failed to create directories: " + str(e))
        print("ERROR: " + str(e))
        set_start_phase(root_dir, "failed")
        return 1

    # 1.5 P2 配置路径归位修复：启动前保障配置布局
    # 已有初始化环境直接启动时，新路径配置可能未迁移/未生成
    from runtime.wnmp_templates import ensure_component_configs_ready
    ok, msg = ensure_component_configs_ready(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "Config layout check failed: " + msg)
        print("ERROR: Config layout check failed: " + msg)
        print("Please run 'python runtime/wnmpctl.py reset-config' to regenerate configs")
        set_start_phase(root_dir, "failed")
        return 1
    log_info(logger, "Config layout check: " + msg)

    # 2. Check binaries
    ok, missing = _check_binaries(root_dir, logger)
    if not ok:
        log_error(logger, "Binary check failed, missing: " + ", ".join(missing))
        print("ERROR: Missing binaries: " + ", ".join(missing))
        set_start_phase(root_dir, "failed")
        return 1

    # 3. Check PHP extensions (non-blocking)
    _check_php_extensions(root_dir, logger)

    # 4. Certificate check (one-time only, do not regenerate)
    enable_https = wnmp_config.is_effective_nginx_https_enabled(root_dir, cfg)
    if enable_https:
        from runtime.wnmp_openssl import is_cert_valid, get_cert_paths
        _, cert_path, key_path = get_cert_paths(root_dir)
        if not is_cert_valid(cert_path, key_path):
            log_warn(logger, "HTTPS certificate missing, falling back to HTTP mode")
            enable_https = False
        else:
            log_info(logger, "HTTPS certificate verified")

    # 5. Check ports（区分本项目占用和外部占用，本项目占用视为已运行）
    ok, conflicts = _check_ports(root_dir, cfg, logger)
    if not ok:
        conflict_msgs = [c["message"] for c in conflicts]
        log_error(logger, "Port check failed: " + "; ".join(conflict_msgs))
        print("ERROR: " + "; ".join(conflict_msgs))
        set_start_phase(root_dir, "failed")
        return 1

    # 6. Start services: MySQL -> PHP-CGI -> Nginx
    started_components = []

    # 模块结果模型：state/success/need_action/failed/message
    mysql_result = {"state": "unknown", "success": False, "need_action": False, "failed": False, "message": ""}
    php_result = {"state": "unknown", "success": False, "need_action": False, "failed": False, "message": ""}
    nginx_result = {"state": "unknown", "success": False, "need_action": False, "failed": False, "message": ""}

    # 解析 Panel 临时结果文件路径（仅 Panel 调用时存在）
    panel_result_file = _get_panel_result_file()

    # 6.1 Start MySQL（幂等：已运行则跳过）
    log_info(logger, "Starting MySQL...")
    set_start_phase(root_dir, "starting_mysql")
    log_info(logger, "start_phase=starting_mysql")
    from runtime.wnmp_mysql import start_mysql
    from runtime.wnmp_process import check_listener_ownership
    mysqld_exe = os.path.normpath(os.path.join(root_dir, "bin", "mysql", "bin", "mysqld.exe"))
    mysql_host_chk = wnmp_config.get(cfg, "MYSQL_HOST", "127.0.0.1")
    mysql_port_chk = wnmp_config.get_effective_mysql_port(root_dir, cfg)
    mysql_ownership = check_listener_ownership(int(mysql_port_chk), mysqld_exe, host=mysql_host_chk,
                                                root_dir=root_dir, timeout=3, logger=logger)
    msg = None  # 初始化，避免跳过时未定义
    if mysql_ownership["status"] == "running":
        mysql_result["state"] = "already_running"
        mysql_result["success"] = True
        log_info(logger, "MySQL is already running on port {}, skipping start".format(mysql_port_chk))
        print("MySQL: already running, skipped")
    else:
        ok, msg = start_mysql(root_dir, cfg, logger, panel_result_file=panel_result_file)
        if not ok:
            mysql_result["state"] = "failed"
            mysql_result["failed"] = True
            mysql_result["message"] = str(msg.get("error", msg) if isinstance(msg, dict) else msg)
            if isinstance(msg, dict):
                log_error(logger, "MySQL start failed: " + str(msg.get("error", "unknown error")))
            else:
                log_error(logger, "MySQL start failed: " + str(msg))
            set_start_phase(root_dir, "failed")
            log_info(logger, "start_phase=failed: MySQL start failed")
            _rollback_started_components(root_dir, cfg, logger, started_components)
            return 1
        mysql_result["state"] = "started"
        mysql_result["success"] = True
        started_components.append("mysql")

    # 6.2 Start PHP-CGI（幂等：已运行则跳过）
    log_info(logger, "Starting PHP-CGI...")
    set_start_phase(root_dir, "starting_php_cgi")
    log_info(logger, "start_phase=starting_php_cgi")
    from runtime.wnmp_php import start_php_cgi
    php_cgi_exe = os.path.normpath(os.path.join(root_dir, "bin", "php", "php-cgi.exe"))
    php_cgi_host_chk, php_cgi_port_chk = wnmp_config.get_effective_php_cgi_host_port(root_dir, cfg)
    php_ownership = check_listener_ownership(php_cgi_port_chk, php_cgi_exe, host=php_cgi_host_chk,
                                              root_dir=root_dir, timeout=3, logger=logger)
    if php_ownership["status"] == "running":
        php_result["state"] = "already_running"
        php_result["success"] = True
        log_info(logger, "PHP-CGI is already running on port {}, skipping start".format(php_cgi_port_chk))
        print("PHP-CGI: already running, skipped")
    else:
        ok, msg = start_php_cgi(root_dir, cfg, logger)
        if not ok:
            php_result["state"] = "failed"
            php_result["failed"] = True
            php_result["message"] = str(msg)
            log_error(logger, "PHP-CGI start failed: " + str(msg))
            set_start_phase(root_dir, "failed")
            log_info(logger, "start_phase=failed: PHP-CGI start failed")
            _rollback_started_components(root_dir, cfg, logger, started_components)
            return 1
        php_result["state"] = "started"
        php_result["success"] = True
        started_components.append("php-cgi")

    # 6.3 Start Nginx（幂等：使用 runtime_ports/desired_ports/stale_ports 完整判断）
    log_info(logger, "Starting Nginx...")
    set_start_phase(root_dir, "starting_nginx")
    log_info(logger, "start_phase=starting_nginx")
    from runtime.wnmp_nginx import start_nginx, detect_nginx_runtime_ports
    nginx_exe = os.path.normpath(os.path.join(root_dir, "bin", "nginx", "nginx.exe"))
    eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
    desired_ports = set(eff["http"] + eff["https"])

    # 检测本项目 nginx.exe 当前实际监听端口
    runtime_ports_list = detect_nginx_runtime_ports(root_dir, nginx_exe, logger=logger, fast_mode=True, timeout=3)
    runtime_ports = set(runtime_ports_list)
    stale_ports = runtime_ports - desired_ports

    # 检查 config_dirty：端口正常不等于配置已应用
    from runtime.wnmp_state import is_component_config_dirty
    nginx_config_dirty = is_component_config_dirty(root_dir, "nginx")

    if desired_ports and desired_ports.issubset(runtime_ports) and not stale_ports and not nginx_config_dirty:
        nginx_result["state"] = "already_running"
        nginx_result["success"] = True
        log_info(logger, "Nginx is already running on all desired ports {}, skipping start".format(desired_ports))
        print("Nginx: already running, skipped")
    elif runtime_ports:
        nginx_result["state"] = "pending_reload"
        nginx_result["need_action"] = True
        if nginx_config_dirty and desired_ports.issubset(runtime_ports) and not stale_ports:
            msg = "Nginx 正在运行，但配置已修改尚未应用，请执行重载或重启 Nginx 生效"
        elif stale_ports:
            msg = "当前 Nginx 正在旧配置下运行（旧端口 {} 仍监听），配置尚未应用，请执行重载或重启 Nginx 生效".format(sorted(stale_ports))
        else:
            missing = desired_ports - runtime_ports
            msg = "当前 Nginx 正在运行但端口 {} 未生效，配置尚未应用，请执行重载或重启 Nginx 生效".format(sorted(missing))
        nginx_result["message"] = msg
        log_info(logger, msg)
        print("Nginx: " + msg)
    else:
        # nginx 未运行，正常启动
        ok, msg = start_nginx(root_dir, cfg, logger)
        if not ok:
            if _is_nginx_need_action_message(msg):
                nginx_result["state"] = "pending_reload"
                nginx_result["need_action"] = True
                nginx_result["message"] = str(msg)
                log_info(logger, "Nginx need_action: " + str(msg))
                print("Nginx: " + str(msg))
            else:
                nginx_result["state"] = "failed"
                nginx_result["failed"] = True
                nginx_result["message"] = str(msg)
                log_error(logger, "Nginx start failed: " + str(msg))
                set_start_phase(root_dir, "failed")
                log_info(logger, "start_phase=failed: Nginx start failed")
                _rollback_started_components(root_dir, cfg, logger, started_components)
                return 1
        else:
            nginx_result["state"] = "started"
            nginx_result["success"] = True
            started_components.append("nginx")

    # 7. Wait for all actual Nginx listen ports（仅新启动时等待）
    if nginx_result["state"] == "started":
        set_start_phase(root_dir, "verifying_services")
        log_info(logger, "start_phase=verifying_services: verifying Nginx ports...")
        eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
        from runtime.wnmp_process import wait_for_port_open
        all_nginx_ports = [(p, "HTTP") for p in eff["http"]] + [(p, "HTTPS") for p in eff["https"]]
        for port, proto in all_nginx_ports:
            log_info(logger, "Waiting for {} port {}...".format(proto, port))
            if not wait_for_port_open("127.0.0.1", port, timeout=15, logger=logger):
                log_error(logger, "{} port {} timeout".format(proto, port))
                set_start_phase(root_dir, "failed")
                log_info(logger, "start_phase=failed: Nginx port {} timeout".format(port))
                _rollback_started_components(root_dir, cfg, logger, started_components)
                return 1

    # 7b. 启动完成，清理 START_PHASE
    set_start_phase(root_dir, "completed")
    log_info(logger, "start_phase=completed: all services started")

    # 8. 汇总模块结果，判断是否完整成功
    if nginx_result["need_action"]:
        # Nginx 配置待应用，不算完整成功，不回滚 PHP/MySQL，不打开浏览器
        log_error(logger, "环境未完全启动：Nginx 配置已修改但尚未应用，请先重载或重启 Nginx；PHP/MySQL 状态不受影响")
        print("环境未完全启动：Nginx 配置已修改但尚未应用，请先重载或重启 Nginx；PHP/MySQL 状态不受影响")
        # 同步 runtime-config.php：仅当默认 index.php 仍由 WNMP 管理时才更新
        # 修复点：原无条件调用 generate_runtime_config，导致用户删除/替换默认 index.php
        # 后 start 仍会重建 runtime-config.php。改为复用 update_runtime_config_for_start
        # 统一走"仅在默认页仍归 WNMP 管理时才维护 runtime-config.php"的判断
        try:
            from runtime.wnmp_default_site import update_runtime_config_for_start
            update_runtime_config_for_start(root_dir, cfg, logger)
        except Exception:
            pass
        return 1

    # 9. Open browser if not autostart（仅完整成功时才打开）
    if not autostart_mode and wnmp_config.get_int(cfg, "AUTO_OPEN_BROWSER", 1) == 1:
        from runtime.wnmp_open import open_browser
        open_browser(cfg, root_dir)

    log_success(logger, "WNMP Runtime started successfully")

    # 启动成功后同步 runtime-config.php：仅当默认 index.php 仍由 WNMP 管理时才更新
    # 修复点：与上面 need_action 分支保持同一套启动维护入口，避免逻辑分叉
    try:
        from runtime.wnmp_default_site import update_runtime_config_for_start
        update_runtime_config_for_start(root_dir, cfg, logger)
    except Exception:
        pass  # 同步失败不影响启动结果

    if not autostart_mode:
        from runtime.wnmp_open import build_open_url
        url = build_open_url(cfg, root_dir)
        print("WNMP Runtime started successfully.")
        print("Open browser to " + url)

    return 0


def cmd_stop(root_dir, cfg, logger):
    """Handle stop command - stop all services in order: Nginx -> PHP-CGI -> MySQL.

    收集每个组件停止结果和端口释放结果，任一失败返回非 0。
    """
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime import wnmp_config

    log_info(logger, "=== WNMP Runtime Stopping ===")
    print("Stopping WNMP Runtime...")

    # 停止环境时清理 START_PHASE
    from runtime.wnmp_state import clear_start_phase
    clear_start_phase(root_dir)

    from runtime.wnmp_nginx import stop_nginx
    from runtime.wnmp_php import stop_php_cgi
    from runtime.wnmp_mysql import stop_mysql
    from runtime.wnmp_process import is_port_listening, wait_for_port_close

    # 端口优先从实际配置文件解析
    eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
    php_cgi_host, php_cgi_port = wnmp_config.get_effective_php_cgi_host_port(root_dir, cfg)
    mysql_host = wnmp_config.get(cfg, "MYSQL_HOST", "127.0.0.1")
    mysql_port = wnmp_config.get_effective_mysql_port(root_dir, cfg)

    failures = []

    log_info(logger, "Stopping Nginx...")
    nginx_stop_ok, nginx_stop_result = stop_nginx(root_dir, cfg, logger)
    if not nginx_stop_ok:
        log_error(logger, "Nginx stop failed: " + str(nginx_stop_result))
        failures.append("Nginx")
    else:
        print("  Nginx: stopped")

    log_info(logger, "Stopping PHP-CGI...")
    php_stop_ok, php_stop_result = stop_php_cgi(root_dir, cfg, logger)
    if not php_stop_ok:
        log_error(logger, "PHP-CGI stop failed: " + str(php_stop_result))
        failures.append("PHP-CGI")
    else:
        print("  PHP-CGI: stopped")

    log_info(logger, "Stopping MySQL...")
    mysql_stop_ok, mysql_stop_result = stop_mysql(root_dir, cfg, logger)
    if not mysql_stop_ok:
        log_error(logger, "MySQL stop failed: " + str(mysql_stop_result))
        failures.append("MySQL")
    else:
        print("  MySQL: stopped")

    # 端口优先从实际配置文件解析，同时获取 Nginx applied_ports
    eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
    php_cgi_host, php_cgi_port = wnmp_config.get_effective_php_cgi_host_port(root_dir, cfg)
    mysql_host = wnmp_config.get(cfg, "MYSQL_HOST", "127.0.0.1")
    mysql_port = wnmp_config.get_effective_mysql_port(root_dir, cfg)

    # 合并 Nginx desired + applied 端口（去重），确保旧端口也能被检查
    from runtime.wnmp_state import get_component_config_apply_state
    nginx_apply_state = get_component_config_apply_state(root_dir, "nginx")
    nginx_applied_ports = nginx_apply_state.get("applied_ports", [])
    all_nginx_ports = list(dict.fromkeys(eff["http"] + eff["https"] + nginx_applied_ports))

    # 等待所有 Nginx listen 端口关闭（含 applied_ports）
    nginx_port_results = {}
    for p in all_nginx_ports:
        proto = "HTTPS" if p in eff["https"] else "HTTP"
        nginx_port_results[(proto, p)] = wait_for_port_close("127.0.0.1", p, timeout=10, logger=logger)
    php_port_ok = wait_for_port_close(php_cgi_host, php_cgi_port, timeout=10, logger=logger)
    mysql_port_ok = wait_for_port_close(mysql_host, mysql_port, timeout=10, logger=logger)

    port_failures = []
    for (proto, p), ok in nginx_port_results.items():
        if not ok:
            port_failures.append("{} port {} still listening".format(proto, p))
    if not php_port_ok:
        port_failures.append("PHP-CGI port {}:{} still listening".format(php_cgi_host, php_cgi_port))
    if not mysql_port_ok:
        port_failures.append("MySQL port {} still listening".format(mysql_port))

    if nginx_stop_ok:
        for (proto, p), ok in nginx_port_results.items():
            if not ok:
                failures.append("{} port {} not released".format(proto, p))
    if php_stop_ok and not php_port_ok:
        failures.append("PHP-CGI port {}:{} not released".format(php_cgi_host, php_cgi_port))
    if mysql_stop_ok and not mysql_port_ok:
        failures.append("MySQL port {} not released".format(mysql_port))

    if port_failures:
        failures.extend(port_failures)

    if failures:
        log_error(logger, "Stop completed with failures: " + ", ".join(failures))
        log_error(logger, "Ports still in use:")
        for (proto, p), ok in nginx_port_results.items():
            if not ok and is_port_listening("127.0.0.1", p):
                log_error(logger, "  {} port {} still listening".format(proto, p))
        if is_port_listening(php_cgi_host, php_cgi_port):
            log_error(logger, "  PHP-CGI port {}:{} still listening".format(php_cgi_host, php_cgi_port))
        if is_port_listening(mysql_host, mysql_port):
            log_error(logger, "  MySQL port {} still listening".format(mysql_port))
        print()
        print("ERROR: Stop completed with failures: " + ", ".join(failures))
        print("Some ports may still be in use. Please check manually.")
        return 1

    log_success(logger, "WNMP Runtime stopped successfully")
    print("WNMP Runtime stopped successfully.")
    return 0


def cmd_restart(root_dir, cfg, logger):
    """Handle restart command - stop then start, no config regeneration."""
    from runtime.wnmp_log import log_info, log_error, log_success
    from runtime import wnmp_config

    log_info(logger, "=== WNMP Runtime Restarting ===")

    log_info(logger, "Stopping services...")
    stop_rc = cmd_stop(root_dir, cfg, logger)

    if stop_rc != 0:
        log_error(logger, "Restart aborted: stop failed, resolve issues before restart")
        print()
        print("ERROR: Stop failed, cannot restart. Please resolve the stop issues first.")
        return 1

    from runtime.wnmp_process import wait_for_port_close

    # 端口优先从实际配置文件解析，同时获取 Nginx applied_ports
    eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
    php_cgi_host, php_cgi_port = wnmp_config.get_effective_php_cgi_host_port(root_dir, cfg)
    mysql_host = wnmp_config.get(cfg, "MYSQL_HOST", "127.0.0.1")
    mysql_port = wnmp_config.get_effective_mysql_port(root_dir, cfg)

    # 合并 Nginx desired + applied 端口（去重），确保旧端口也能被等待释放
    from runtime.wnmp_state import get_component_config_apply_state
    nginx_apply_state = get_component_config_apply_state(root_dir, "nginx")
    nginx_applied_ports = nginx_apply_state.get("applied_ports", [])
    all_nginx_ports = list(dict.fromkeys(eff["http"] + eff["https"] + nginx_applied_ports))

    log_info(logger, "Waiting for ports to be released...")
    for p in all_nginx_ports:
        wait_for_port_close("127.0.0.1", p, timeout=15, logger=logger)
    wait_for_port_close(php_cgi_host, php_cgi_port, timeout=15, logger=logger)
    wait_for_port_close(mysql_host, mysql_port, timeout=15, logger=logger)

    # 3. Start (no config regeneration)
    log_info(logger, "Starting services...")
    return cmd_start(root_dir, cfg, logger)


def cmd_status(root_dir, cfg, logger):
    """Handle status command - show full status."""
    from runtime.wnmp_status import show_status
    return show_status(root_dir, cfg, logger)


def _restore_config_backup(backup_dir, root_dir, logger, pre_existing_set=None):
    """从备份目录恢复组件配置文件，并删除 reset 前不存在的半成品文件。

    只恢复备份中存在的文件，不恢复 runtime.ini，不触碰 data/mysql、www、logs。
    恢复时验证目标路径在 root_dir 内且无路径逃逸，防止错误路径写入。
    以 root_dir 为统一安全基准计算相对路径，支持未来 bin/nginx/conf 等新路径。
    pre_existing_set: reset 前存在的目标配置文件绝对路径集合。
      对 reset 前不存在但本次生成出的目标配置文件，删除该半成品。
    返回 (ok, error_msg) 元组。
    """
    import shutil
    root_dir_real = os.path.realpath(root_dir)
    if not os.path.isdir(backup_dir):
        return False, "备份目录不存在: " + backup_dir
    for dirpath, dirnames, filenames in os.walk(backup_dir):
        for fname in filenames:
            src = os.path.join(dirpath, fname)
            rel = os.path.relpath(src, backup_dir)
            # 安全校验：不得是绝对路径，不得包含 .. 逃逸
            if os.path.isabs(rel) or rel.startswith("..") or ".." + os.sep in rel or "../" in rel or "..\\" in rel:
                return False, "恢复路径逃逸，拒绝写入: " + rel
            # 以 root_dir 为基准恢复，支持未来 bin/nginx/conf 等新路径
            dest = os.path.join(root_dir, rel)
            dest_real = os.path.realpath(os.path.dirname(dest))
            # 验证目标路径在 root_dir 内
            if not dest_real.startswith(root_dir_real):
                return False, "恢复路径越界，拒绝写入: " + rel
            # 跳过 runtime.ini，不恢复面板配置
            if rel.replace("\\", "/") == "runtime.ini":
                continue
            try:
                dest_parent = os.path.dirname(dest)
                if not os.path.isdir(dest_parent):
                    os.makedirs(dest_parent, exist_ok=True)
                shutil.copy2(src, dest)
            except Exception as e:
                return False, "恢复 {} 失败: {}".format(rel, str(e))

    # 删除 reset 前不存在但本次生成出的半成品目标配置文件
    if pre_existing_set is not None:
        # 五个目标配置文件
        # 路径收敛：通过统一路径模块获取配置文件路径
        target_files = [
            get_nginx_conf_path(root_dir),
            get_nginx_site_conf_path(root_dir),
            get_php_ini_path(root_dir),
            get_php_cgi_ini_path(root_dir),
            get_mysql_ini_path(root_dir),
        ]
        for f in target_files:
            if f not in pre_existing_set and os.path.isfile(f):
                try:
                    os.remove(f)
                except Exception as e:
                    return False, "自动恢复失败，文件 {} 需要手动处理: {}".format(
                        os.path.relpath(f, root_dir), str(e))
    return True, ""


def cmd_reset_config(root_dir, cfg, logger):
    """Handle reset-config command - reset component config files from templates.

    Only resets component configs (nginx/php/mysql), does NOT touch:
    - config/runtime.ini (panel config)
    - data/mysql (database files)
    - www (website directory)
    - logs (log files)

    Requires --force to confirm.
    Backup existing config files before overwriting; abort if backup fails.
    If config deletion fails, abort immediately without generating.
    If config generation fails, auto-rollback from backup.
    """
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime.wnmp_state import mark_config_generated
    import shutil

    force = "--force" in sys.argv

    print("=" * 50)
    print("  WNMP Runtime - Reset Component Configs")
    print("=" * 50)
    print()

    if not force:
        print("This will reset the following component config files to defaults:")
        # P2：配置文件路径提示切换到新组件配置路径
        print("  - bin/nginx/conf/nginx.conf")
        print("  - bin/nginx/conf/site.conf")
        print("  - bin/php/php.ini")
        print("  - bin/php/php-cgi.ini")
        print("  - bin/mysql/my.ini")
        print()
        print("This does NOT delete MySQL data, website files, logs, or panel config (runtime.ini).")
        print("To proceed, run: bin\\python\\python.exe runtime\\wnmpctl.py reset-config --force")
        print()
        return 1

    log_info(logger, "=== Resetting component config files ===")
    print("Resetting component config files from templates...")

    # 将被重置的组件配置文件列表（不含 runtime.ini）
    # 路径收敛：通过统一路径模块获取配置文件路径
    config_files_to_reset = [
        get_nginx_conf_path(root_dir),
        get_nginx_site_conf_path(root_dir),
        get_php_ini_path(root_dir),
        get_php_cgi_ini_path(root_dir),
        get_mysql_ini_path(root_dir),
    ]

    # 第一步：记录 reset 前目标配置文件是否存在（用于回滚时删除半成品）
    pre_existing_set = set(f for f in config_files_to_reset if os.path.isfile(f))

    # 第二步：备份当前配置文件到 backups/config-reset-YYYYMMDD-HHMMSS
    # 备份失败必须中止，不允许静默覆盖
    backup_dir = None
    existing_files = [f for f in config_files_to_reset if os.path.isfile(f)]
    if existing_files:
        backup_timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = os.path.join(root_dir, "backups", "config-reset-{}".format(backup_timestamp))
        try:
            os.makedirs(backup_dir, exist_ok=True)
            for f in existing_files:
                # 以 root_dir 为统一安全基准计算相对路径，支持未来 bin/nginx/conf 等新路径
                # 备份目录内部保留原始相对结构（如 config/nginx.conf 或 bin/nginx/conf/nginx.conf）
                safe_rel = os.path.relpath(f, root_dir)
                # 安全校验：不得是绝对路径，不得包含 .. 逃逸
                if os.path.isabs(safe_rel) or safe_rel.startswith("..") or ".." + os.sep in safe_rel or "../" in safe_rel or "..\\" in safe_rel:
                    raise ValueError("备份路径逃逸，拒绝处理: " + safe_rel)
                dest = os.path.join(backup_dir, safe_rel)
                dest_dir = os.path.dirname(dest)
                if not os.path.isdir(dest_dir):
                    os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(f, dest)
            log_info(logger, "Config backup created: " + backup_dir)
            print("Backup created: " + backup_dir)
        except Exception as e:
            log_error(logger, "Config backup failed, aborting reset: {}".format(str(e)))
            print("ERROR: Config backup failed, aborting reset: {}".format(str(e)))
            return 1
    else:
        log_info(logger, "No existing config files to backup")

    # 第三步：删除旧配置文件，任一删除失败立即中止
    for f in config_files_to_reset:
        if os.path.isfile(f):
            try:
                os.remove(f)
                log_info(logger, "Removed existing config: " + f)
            except Exception as e:
                err_msg = "删除 {} 失败: {}".format(
                    os.path.relpath(f, root_dir), str(e))
                log_error(logger, err_msg)
                print("ERROR: " + err_msg)
                # 删除失败：中止，不继续 generate，保留备份
                return 1

    # 第四步：重新生成默认配置
    from runtime.wnmp_templates import generate_all_configs
    ok = generate_all_configs(root_dir, cfg, logger)
    if not ok:
        # 生成失败：自动从备份恢复原配置，并删除 reset 前不存在的半成品
        log_error(logger, "Config regeneration failed, attempting rollback from backup")
        print("ERROR: Config regeneration failed")
        if backup_dir and os.path.isdir(backup_dir):
            rollback_ok, rollback_err = _restore_config_backup(backup_dir, root_dir, logger, pre_existing_set)
            if rollback_ok:
                log_info(logger, "Rollback from backup succeeded: " + backup_dir)
                print("Rollback: original configs restored from " + backup_dir)
                return 1  # 仍返回失败，但已恢复原配置
            else:
                log_error(logger, "Rollback failed: " + rollback_err)
                print("ERROR: Rollback also failed: " + rollback_err)
                print("Please manually restore from: " + backup_dir)
                return 1
        else:
            log_error(logger, "No backup available for rollback")
            print("ERROR: No backup available for rollback")
            return 1

    # 第五步：验证生成后的目标配置文件存在
    for f in config_files_to_reset:
        if not os.path.isfile(f):
            err_msg = "生成后配置文件缺失: {}".format(
                os.path.relpath(f, root_dir))
            log_error(logger, err_msg)
            print("ERROR: " + err_msg)
            # 尝试回滚
            if backup_dir and os.path.isdir(backup_dir):
                _restore_config_backup(backup_dir, root_dir, logger, pre_existing_set)
            return 1

    mark_config_generated(root_dir)

    # 第六步：仅标记 Nginx dirty（PHP/MySQL 当前无完整 dirty 展示和清理闭环，不写入 hidden dirty）
    # 不自动重启服务，不改变 PHP/MySQL 运行状态，不重新显示 PID
    try:
        from runtime.wnmp_state import mark_component_config_dirty
        # Nginx 配置已重置，标记 dirty（运行中→pending_reload，已停止→启动后生效）
        mark_component_config_dirty(root_dir, "nginx")
        log_info(logger, "Marked nginx config_dirty after reset")
    except Exception as e:
        log_warn(logger, "Failed to mark nginx config_dirty: {}".format(str(e)))

    # 成功：返回备份目录信息，明确各组件生效方式
    backup_info = ""
    if backup_dir:
        backup_info = " 备份目录：{}".format(backup_dir)
    success_msg = "组件配置已重置并已备份原配置。Nginx 如正在运行需重载/重启后生效；PHP/PHP-CGI 和 MySQL 配置需重启对应组件后生效。{}".format(backup_info)
    log_success(logger, success_msg)
    print(success_msg)
    print()
    return 0


def cmd_open(root_dir, cfg, logger):
    """Handle open command."""
    from runtime.wnmp_open import open_browser
    return open_browser(cfg, root_dir)


def cmd_safe_start(root_dir, cfg, logger):
    """Handle safe-start command - English console summary only."""
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime import wnmp_config

    # Console output in English only
    print("WNMP Runtime - Starting")
    print("=" * 40)

    # Check binaries
    binaries = [
        ("Nginx", "bin/nginx/nginx.exe"),
        ("PHP-CGI", "bin/php/php-cgi.exe"),
        ("PHP CLI", "bin/php/php.exe"),
        ("MySQL", "bin/mysql/bin/mysqld.exe"),
        ("MySQL Admin", "bin/mysql/bin/mysqladmin.exe"),
        ("MySQL Client", "bin/mysql/bin/mysql.exe"),
    ]

    missing = []
    for name, rel_path in binaries:
        full_path = os.path.join(root_dir, rel_path)
        if not os.path.isfile(full_path):
            print("  MISSING: " + rel_path)
            log_error(logger, "Binary not found: " + rel_path)
            missing.append(name)
        else:
            print("  FOUND: " + rel_path)

    openssl_path = os.path.join(root_dir, "bin/openssl/openssl.exe")
    if not os.path.isfile(openssl_path):
        print("  OPTIONAL: bin/openssl/openssl.exe not found")
        log_warn(logger, "OpenSSL not found (optional)")

    if missing:
        print()
        print("ERROR: Missing binaries: " + ", ".join(missing))
        log_error(logger, "Binary check failed")
        return 1

    print()
    print("All required binaries found.")
    print("Starting services... (details in log file)")

    # 调用完整 start 流程（如未初始化则先初始化）
    from runtime.wnmp_state import is_initialized
    if not is_initialized(root_dir):
        return cmd_init(root_dir, cfg, logger)
    return cmd_start(root_dir, cfg, logger)


def cmd_start_nginx(root_dir, cfg, logger):
    """Handle start-nginx command - start Nginx only."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_nginx import start_nginx

    # P2 启动前配置布局保障小收口：单组件启动前也需要保障配置布局
    ok, msg = _ensure_config_layout_before_component_start(root_dir, cfg, logger)
    if not ok:
        return 1

    log_info(logger, "=== Starting Nginx ===")
    ok, result = start_nginx(root_dir, cfg, logger)
    if ok:
        print("Nginx: started")
        return 0
    else:
        log_error(logger, "Nginx start failed: " + str(result))
        print("ERROR: Nginx start failed: " + str(result))
        return 1


def cmd_stop_nginx(root_dir, cfg, logger):
    """Handle stop-nginx command - stop Nginx only."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_nginx import stop_nginx
    log_info(logger, "=== Stopping Nginx ===")
    ok, result = stop_nginx(root_dir, cfg, logger)
    if ok:
        print("Nginx: stopped")
        return 0
    else:
        log_error(logger, "Nginx stop failed: " + str(result))
        print("ERROR: Nginx stop failed: " + str(result))
        return 1


def cmd_restart_nginx(root_dir, cfg, logger):
    """Handle restart-nginx command - stop then start Nginx.

    安全边界：stop_nginx 失败时立即中止，不继续 start，防止新旧进程冲突。
    """
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_nginx import stop_nginx, start_nginx
    log_info(logger, "=== Restarting Nginx ===")
    ok, result = stop_nginx(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "Nginx stop failed: " + str(result))
        print("ERROR: Nginx stop failed, aborting restart: " + str(result))
        print("请先解决停止失败问题，再尝试重启")
        return 1

    # P2 启动前配置布局保障小收口：restart 时 stop 成功后 start 前也需要保障
    ok, msg = _ensure_config_layout_before_component_start(root_dir, cfg, logger)
    if not ok:
        return 1

    ok, result = start_nginx(root_dir, cfg, logger)
    if ok:
        print("Nginx: restarted")
        return 0
    else:
        log_error(logger, "Nginx start failed: " + str(result))
        print("ERROR: Nginx restart failed: " + str(result))
        return 1


def cmd_reload_nginx(root_dir, cfg, logger):
    """Handle reload-nginx command - reload Nginx configuration only."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_nginx import reload_nginx
    log_info(logger, "=== Reloading Nginx ===")
    ok, result = reload_nginx(root_dir, cfg, logger)
    if ok:
        print("Nginx: reloaded")
        return 0
    else:
        log_error(logger, "Nginx reload failed: " + str(result))
        print("ERROR: Nginx reload failed: " + str(result))
        return 1


def cmd_start_php(root_dir, cfg, logger):
    """Handle start-php command - start PHP-CGI only."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_php import start_php_cgi

    # P2 启动前配置布局保障小收口：单组件启动前也需要保障配置布局
    ok, msg = _ensure_config_layout_before_component_start(root_dir, cfg, logger)
    if not ok:
        return 1

    log_info(logger, "=== Starting PHP-CGI ===")
    ok, result = start_php_cgi(root_dir, cfg, logger)
    if ok:
        print("PHP-CGI: started")
        return 0
    else:
        log_error(logger, "PHP-CGI start failed: " + str(result))
        print("ERROR: PHP-CGI start failed: " + str(result))
        return 1


def cmd_stop_php(root_dir, cfg, logger):
    """Handle stop-php command - stop PHP-CGI only."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_php import stop_php_cgi
    log_info(logger, "=== Stopping PHP-CGI ===")
    ok, result = stop_php_cgi(root_dir, cfg, logger)
    if ok:
        print("PHP-CGI: stopped")
        return 0
    else:
        log_error(logger, "PHP-CGI stop failed: " + str(result))
        print("ERROR: PHP-CGI stop failed: " + str(result))
        return 1


def cmd_restart_php(root_dir, cfg, logger):
    """Handle restart-php command - stop then start PHP-CGI."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_php import stop_php_cgi, start_php_cgi
    log_info(logger, "=== Restarting PHP-CGI ===")
    ok, result = stop_php_cgi(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "PHP-CGI stop failed: " + str(result))

    # P2 启动前配置布局保障小收口：restart 时 start 前也需要保障
    ok, msg = _ensure_config_layout_before_component_start(root_dir, cfg, logger)
    if not ok:
        return 1

    ok, result = start_php_cgi(root_dir, cfg, logger)
    if ok:
        print("PHP-CGI: restarted")
        return 0
    else:
        log_error(logger, "PHP-CGI start failed: " + str(result))
        print("ERROR: PHP-CGI restart failed: " + str(result))
        return 1


def cmd_start_mysql(root_dir, cfg, logger):
    """Handle start-mysql command - start MySQL only."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_mysql import start_mysql

    # P2 启动前配置布局保障小收口：单组件启动前也需要保障配置布局
    ok, msg = _ensure_config_layout_before_component_start(root_dir, cfg, logger)
    if not ok:
        return 1

    log_info(logger, "=== Starting MySQL ===")
    ok, result = start_mysql(root_dir, cfg, logger)
    if ok:
        print("MySQL: started")
        return 0
    else:
        # 日志脱敏：只打印 error 字段，不泄露 mysql_init_password
        if isinstance(result, dict):
            log_error(logger, "MySQL start failed: " + str(result.get("error", "unknown error")))
        else:
            log_error(logger, "MySQL start failed: " + str(result))
        print("ERROR: MySQL start failed")
        return 1


def cmd_stop_mysql(root_dir, cfg, logger):
    """Handle stop-mysql command - stop MySQL only."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_mysql import stop_mysql
    log_info(logger, "=== Stopping MySQL ===")
    ok, result = stop_mysql(root_dir, cfg, logger)
    if ok:
        print("MySQL: stopped")
        return 0
    else:
        log_error(logger, "MySQL stop failed: " + str(result))
        print("ERROR: MySQL stop failed: " + str(result))
        return 1


def cmd_restart_mysql(root_dir, cfg, logger):
    """Handle restart-mysql command - stop then start MySQL."""
    from runtime.wnmp_log import log_info, log_error
    from runtime.wnmp_mysql import stop_mysql, start_mysql
    log_info(logger, "=== Restarting MySQL ===")
    ok, result = stop_mysql(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "MySQL stop failed: " + str(result))

    # P2 启动前配置布局保障小收口：restart 时 start 前也需要保障
    ok, msg = _ensure_config_layout_before_component_start(root_dir, cfg, logger)
    if not ok:
        return 1

    ok, result = start_mysql(root_dir, cfg, logger)
    if ok:
        print("MySQL: restarted")
        return 0
    else:
        # 日志脱敏：只打印 error 字段，不泄露 mysql_init_password
        if isinstance(result, dict):
            log_error(logger, "MySQL start failed: " + str(result.get("error", "unknown error")))
        else:
            log_error(logger, "MySQL start failed: " + str(result))
        print("ERROR: MySQL restart failed")
        return 1


def cmd_install_autostart(root_dir, cfg, logger):
    """Handle install-autostart command."""
    from runtime.wnmp_autostart import install_autostart
    return install_autostart(root_dir, cfg, logger)


def cmd_uninstall_autostart(root_dir, cfg, logger):
    """Handle uninstall-autostart command."""
    from runtime.wnmp_autostart import uninstall_autostart
    return uninstall_autostart(root_dir, cfg, logger)


def cmd_autostart_status(root_dir, cfg, logger):
    """Handle autostart-status command."""
    from runtime.wnmp_autostart import autostart_status
    result = autostart_status(root_dir, cfg, logger)
    # autostart_status 现在返回 dict
    if isinstance(result, dict):
        enabled = result.get("enabled", False)
        msg = result.get("message", "")
        if enabled:
            print("Auto-start: enabled - " + msg)
        else:
            print("Auto-start: " + msg)
        return 0 if enabled else 1
    # 兼容旧返回值
    return result


def cmd_install_env(root_dir, cfg, logger):
    """Handle install-env command - add tool paths to system PATH."""
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime import wnmp_config
    from runtime.wnmp_env import is_admin, add_tool_paths_to_system_path, get_tool_paths
    from runtime.wnmp_state import mark_env_path_configured

    print("=" * 50)
    print("  WNMP Runtime - Install Environment PATH")
    print("=" * 50)
    print()

    if not is_admin():
        print("ERROR: Administrator privileges required.")
        print()
        print("Please run WNMPPanel.exe as administrator, or use:")
        print("  bin\\python\\python.exe runtime\\wnmpctl.py install-path")
        print()
        return 1

    add_openssl = wnmp_config.get_int(cfg, "ADD_OPENSSL_TO_SYSTEM_PATH", 0) == 1
    tool_paths = get_tool_paths(root_dir, add_openssl)

    print("Tool paths to be added:")
    for path in tool_paths:
        print("  " + path)
    print()

    ok, added, skipped, err = add_tool_paths_to_system_path(root_dir, add_openssl)

    if not ok:
        log_error(logger, "Failed to add to system PATH: " + str(err))
        print("ERROR: " + str(err))
        return 1

    mark_env_path_configured(root_dir, configured=True, items=tool_paths)

    log_success(logger, "System PATH updated successfully")
    print("System PATH updated successfully.")
    print("  Added: {} new paths".format(added))
    print("  Already existed: {} paths".format(skipped))
    print()
    print("Please close and reopen your terminal (CMD/PowerShell) to use the new PATH.")
    print()

    return 0


def cmd_uninstall_env(root_dir, cfg, logger):
    """Handle uninstall-env command - remove tool paths from system PATH."""
    from runtime.wnmp_log import log_info, log_error, log_success, log_warn
    from runtime import wnmp_config
    from runtime.wnmp_env import is_admin, remove_tool_paths_from_system_path, get_tool_paths
    from runtime.wnmp_state import load_state

    print("=" * 50)
    print("  WNMP Runtime - Uninstall Environment PATH")
    print("=" * 50)
    print()

    if not is_admin():
        print("ERROR: Administrator privileges required.")
        print()
        print("Please run WNMPPanel.exe as administrator, or use:")
        print("  bin\\python\\python.exe runtime\\wnmpctl.py install-path")
        print()
        return 1

    state = load_state(root_dir)
    add_openssl = state.get("ENV_PATH_ITEMS") and any("openssl" in p for p in state.get("ENV_PATH_ITEMS", []))
    if not add_openssl:
        add_openssl = wnmp_config.get_int(cfg, "ADD_OPENSSL_TO_SYSTEM_PATH", 0) == 1

    tool_paths = get_tool_paths(root_dir, add_openssl)

    print("Tool paths to be removed:")
    for path in tool_paths:
        print("  " + path)
    print()

    ok, removed, err = remove_tool_paths_from_system_path(root_dir, add_openssl)

    if not ok:
        log_error(logger, "Failed to remove from system PATH: " + str(err))
        print("ERROR: " + str(err))
        return 1

    print("System PATH updated successfully.")
    print("  Removed: {} paths".format(removed))
    print()
    print("Please close and reopen your terminal (CMD/PowerShell) to see the changes.")
    print()

    return 0


def cmd_env_status(root_dir, cfg, logger):
    """Handle env-status command - show system PATH status."""
    from runtime.wnmp_log import log_info
    from runtime import wnmp_config
    from runtime.wnmp_env import is_admin, get_tool_paths, get_path_items_status, format_path_status_summary
    from runtime.wnmp_env import get_current_path_list, normalize_path
    from runtime.wnmp_state import load_state, get_env_path_items, is_env_path_configured
    from runtime.wnmp_env import check_path_migration

    print("=" * 50)
    print("  WNMP Runtime - Environment PATH Status")
    print("=" * 50)
    print()

    admin = is_admin()
    print("Admin privileges: {}".format("YES" if admin else "NO"))
    print()

    add_openssl = wnmp_config.get_int(cfg, "ADD_OPENSSL_TO_SYSTEM_PATH", 0) == 1
    tool_paths = get_tool_paths(root_dir, add_openssl)

    print("Tool paths in system PATH:")
    status_items = get_path_items_status(root_dir, add_openssl)
    for path, in_path in status_items:
        name = os.path.basename(os.path.dirname(path))
        if os.path.basename(path) != "bin":
            name = os.path.basename(path)
        print("  {}: {}".format(name, "IN PATH" if in_path else "NOT IN PATH"))

    print()
    print("Verification commands:")
    for path in tool_paths:
        exe_name = os.path.basename(path)
        if exe_name == "bin":
            exe_name = os.path.basename(os.path.dirname(path))
        print("  {} -v (from {})".format(exe_name, path))

    print()
    configured = is_env_path_configured(root_dir)
    print("PATH configured by WNMP: {}".format("YES" if configured else "NO"))

    state = load_state(root_dir)
    if state.get("ENV_PATH_CONFIGURED_AT"):
        print("  Configured at: {}".format(state.get("ENV_PATH_CONFIGURED_AT")))
    if state.get("ENV_PATH_SKIP_REASON"):
        print("  Skipped reason: {}".format(state.get("ENV_PATH_SKIP_REASON")))

    state_items = get_env_path_items(root_dir)
    if state_items and check_path_migration(root_dir, state_items):
        print()
        print("WARNING: Tool directory has been moved!")
        print("  Current tool paths: {}".format(", ".join([normalize_path(p) for p in tool_paths])))
        print("  Previously configured paths: {}".format(", ".join([normalize_path(p) for p in state_items])))
        print()
        print("Please run:")
        print("  1. bin\\python\\python.exe runtime\\wnmpctl.py uninstall-env (as admin) - to remove old paths")
        print("  2. bin\\python\\python.exe runtime\\wnmpctl.py install-env (as admin) - to add new paths")

    print()
    return 0


def cmd_cert(root_dir, cfg, logger):
    """Handle cert command with subcommands."""
    from runtime.wnmp_openssl import cmd_cert_status, cmd_cert_generate
    from runtime.wnmp_log import setup_logging

    if logger is None:
        logger = setup_logging(root_dir, safe_mode=False)

    if len(sys.argv) < 3:
        print("Usage: python runtime/wnmpctl.py cert [--status|--force]")
        print()
        print("Subcommands:")
        print("  (none)           Check and generate missing certificates")
        print("  --status         Show certificate status")
        print("  --force          Force regenerate certificates")
        return 1

    subcmd = sys.argv[2].lower()
    if subcmd == "--status":
        return cmd_cert_status(root_dir, cfg, logger)
    elif subcmd == "--force":
        return cmd_cert_generate(root_dir, cfg, logger, force=True)
    else:
        print("Unknown cert subcommand: " + subcmd)
        print("Usage: python runtime/wnmpctl.py cert [--status|--force]")
        return 1


def main():
    """Main entry point."""
    root_dir = get_root_dir()

    # Load config
    from runtime.wnmp_config import load_config
    cfg = load_config(root_dir)

    # Determine command
    if len(sys.argv) < 2:
        print("Usage: python runtime/wnmpctl.py <command>")
        print()
        print("Commands:")
        print("  start               Start all services (auto-init on first run)")
        print("  stop                Stop all services")
        print("  restart             Restart all services")
        print("  status              Show configuration summary and service status")
        print("  open                Open browser to default page")
        print("  reset-config        Reset component config files to defaults (use --force)")
        print("  cert                Manage certificates (see cert --help)")
        print("  safe-start          Start with English console summary")
        print("  install-autostart   Install auto-start scheduled task (Admin)")
        print("  uninstall-autostart Remove auto-start scheduled task (Admin)")
        print("  autostart-status    Query auto-start scheduled task status")
        return 1

    command = sys.argv[1].lower()

    # Handle cert subcommands
    if command == "cert":
        return cmd_cert(root_dir, cfg, None)

    # Setup logging
    from runtime.wnmp_log import setup_logging

    autostart_mode = "--autostart" in sys.argv
    safe_mode = (command == "safe-start")
    logger = setup_logging(root_dir, safe_mode=safe_mode, autostart_mode=autostart_mode)

    # Dispatch command
    commands = {
        "init": cmd_init,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "status": cmd_status,
        "open": cmd_open,
        "reset-config": cmd_reset_config,
        "safe-start": cmd_safe_start,
        "start-nginx": cmd_start_nginx,
        "stop-nginx": cmd_stop_nginx,
        "restart-nginx": cmd_restart_nginx,
        "reload-nginx": cmd_reload_nginx,
        "start-php": cmd_start_php,
        "stop-php": cmd_stop_php,
        "restart-php": cmd_restart_php,
        "start-mysql": cmd_start_mysql,
        "stop-mysql": cmd_stop_mysql,
        "restart-mysql": cmd_restart_mysql,
        "install-autostart": cmd_install_autostart,
        "uninstall-autostart": cmd_uninstall_autostart,
        "autostart-status": cmd_autostart_status,
        "install-env": cmd_install_env,
        "uninstall-env": cmd_uninstall_env,
        "env-status": cmd_env_status,
    }

    handler = commands.get(command)
    if handler is None:
        print("Unknown command: " + command)
        print("Run without arguments to see available commands")
        return 1

    try:
        return handler(root_dir, cfg, logger)
    except Exception as e:
        from runtime.wnmp_log import log_error
        log_error(logger, "Unhandled error: " + str(e))
        print("Error: " + str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
