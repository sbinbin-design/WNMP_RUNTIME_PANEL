# -*- coding: utf-8 -*-
"""
WNMP Panel Server - Minimal Python Panel using standard library.

Usage (normal user):
    Double-click WNMPanel.exe  (recommended, auto-starts this server)

Usage (development / debugging only):
    bin/python/python.exe runtime/panel_server.py
    bin/python/python.exe runtime/panel_server.py --port 8788 --no-browser

Endpoints:
    GET / - Panel UI (index.html)
    GET  /api/ping          - Lightweight health check for launcher
    GET  /api/status         - Real status snapshot (JSON)
    GET  /api/status/debug   - Detailed debug info (manual use only)
    GET  /api/panel/config   - Panel config for frontend (heartbeat etc.)
    GET  /api/versions       - On-demand version query (cached)
    POST /api/action         - Execute white-listed action (JSON)
    POST /api/panel/heartbeat- Client heartbeat
    POST /api/panel/client-close - Client close notification
    GET  /api/logs/runtime   - Read runtime.log last N lines (JSON)
    GET  /api/logs/action    - Read action_output.log last N lines (JSON)
    GET  /api/config-file    - Read config file by whitelist name (JSON)
    POST /api/config-file    - Save config file with auto-backup (JSON)
    GET  /api/environment-info - Environment info data source (JSON)
    POST /api/open-directory  - Open directory by whitelist key (JSON)
    GET  /assets/app.js      - JS asset
    GET  /assets/i18n.js      - i18n JS asset
    GET  /assets/style.css   - CSS asset
"""
import os
import sys
import json
import time
import threading
import mimetypes
import webbrowser
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

# ---- path setup -------------------------------------------------------------
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.normpath(os.path.join(_script_dir, ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from runtime.panel.paths import get_root_dir, get_panel_dir
from runtime.wnmp_component_paths import (
    get_nginx_conf_path, get_nginx_site_conf_path,
    get_nginx_vhosts_dir,
    get_php_ini_path, get_php_cgi_ini_path, get_mysql_ini_path,
    get_runtime_ini_path,
)

_root_dir = get_root_dir()
_panel_dir = get_panel_dir()

from runtime.wnmp_stdio import configure_stdio_utf8
configure_stdio_utf8()

# ---- panel module imports ---------------------------------------------------
from runtime.panel.actions import execute_action, is_valid_action
from runtime.panel.status import get_full_status, get_component_status

# ---- global action lock & action-in-progress flag ---------------------------
_action_lock = threading.RLock()
_action_in_progress = False  # 动作执行中标记，防止自动退出打断动作


def _log_panel_error(root_dir, message):
    """写入 panel_server.log 错误日志。"""
    try:
        log_dir = os.path.join(root_dir, "logs", "panel")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "panel_server.log")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("[{}] ERROR: {}\n".format(timestamp, message))
    except Exception:
        pass

# ---- client session management (heartbeat + auto-exit) ----------------------
_active_clients = {}  # {client_id: last_seen_timestamp}
_clients_lock = threading.Lock()
_server_instance = None  # 全局 server 引用，用于 shutdown
_exit_monitor_thread = None


def _panel_log(message):
    """安全写入 Panel Server 日志，失败不抛异常。"""
    try:
        log_dir = os.path.join(_root_dir, "logs", "panel")
        log_path = os.path.join(log_dir, "panel_server.log")
        os.makedirs(log_dir, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("[{}] {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass


def _register_client(client_id):
    """Register or refresh a client heartbeat."""
    with _clients_lock:
        is_new = client_id not in _active_clients
        _active_clients[client_id] = time.time()
        count = len(_active_clients)
    if is_new:
        _panel_log("heartbeat register client_id={}, active_clients={}".format(client_id, count))


def _unregister_client(client_id):
    """Remove a client (page close)."""
    with _clients_lock:
        _active_clients.pop(client_id, None)
        count = len(_active_clients)
    _panel_log("client-close remove client_id={}, active_clients={}".format(client_id, count))


def _get_active_client_count():
    """Return number of active clients."""
    with _clients_lock:
        return len(_active_clients)


def _cleanup_stale_clients(timeout_sec):
    """Remove clients not seen within timeout_sec."""
    now = time.time()
    with _clients_lock:
        stale = [cid for cid, ts in _active_clients.items() if now - ts > timeout_sec]
        for cid in stale:
            del _active_clients[cid]
        count = len(_active_clients)
    if stale:
        _panel_log("cleanup stale clients: removed={}, active_clients={}".format(len(stale), count))


def _exit_monitor_loop():
    """Background thread: monitor active clients, auto-exit when none remain."""
    global _action_in_progress

    cfg = _load_runtime_config()
    exit_on_close = cfg.get("PANEL_EXIT_ON_CLOSE", "1") == "1"
    if not exit_on_close:
        return

    no_client_seconds = 60
    try:
        no_client_seconds = int(cfg.get("PANEL_NO_CLIENT_EXIT_SECONDS", "60"))
    except (ValueError, TypeError):
        pass

    grace_seconds = 2
    try:
        grace_seconds = int(cfg.get("PANEL_SHUTDOWN_GRACE_SECONDS", "2"))
    except (ValueError, TypeError):
        pass

    heartbeat_interval = 5
    try:
        heartbeat_interval = int(cfg.get("PANEL_HEARTBEAT_INTERVAL", "5"))
    except (ValueError, TypeError):
        pass

    # 心跳失联清理超时：浏览器后台标签页可能限速心跳，因此不能太短
    client_stale_seconds = 300
    try:
        client_stale_seconds = int(cfg.get("PANEL_CLIENT_STALE_SECONDS", "300"))
    except (ValueError, TypeError):
        pass

    # stale_timeout 取三者最大值，避免浏览器后台限速导致误判
    stale_timeout = max(client_stale_seconds, heartbeat_interval * 12, 120)

    no_client_since = None  # 记录从何时起没有活跃客户端

    _panel_log("exit monitor started: no_client_seconds={}, stale_timeout={}, client_stale_seconds={}".format(
        no_client_seconds, stale_timeout, client_stale_seconds))

    while True:
        time.sleep(2)

        # 清理过期客户端
        _cleanup_stale_clients(stale_timeout)

        client_count = _get_active_client_count()

        if client_count > 0:
            if no_client_since is not None:
                _panel_log("client reconnected, resetting no_client timer, active_clients={}".format(client_count))
            no_client_since = None
            continue

        # 没有活跃客户端
        if no_client_since is None:
            no_client_since = time.time()
            _panel_log("no active clients, starting no_client timer, active_clients=0")

        elapsed = time.time() - no_client_since
        if elapsed < no_client_seconds:
            continue

        # 超时到达，检查是否有动作执行中
        if _action_in_progress:
            _panel_log("action in progress, delaying auto-exit")
            no_client_since = None  # 重置计时，动作结束后重新计算
            continue

        # 满足退出条件，记录详细原因
        _panel_log("auto-exit condition met: no_client_seconds={}, elapsed={:.1f}s, active_clients={}, stale_timeout={}, client_stale_seconds={}, waiting grace_seconds={}".format(
            no_client_seconds, elapsed, _get_active_client_count(), stale_timeout, client_stale_seconds, grace_seconds))
        time.sleep(grace_seconds)

        # 再次确认
        if _get_active_client_count() > 0 or _action_in_progress:
            _panel_log("client reconnected or action in progress after grace, aborting shutdown")
            no_client_since = None
            continue

        # 优雅退出：使用安全日志，确保 shutdown 不受日志失败影响
        _panel_log("triggering Panel Server shutdown (all Panel pages closed for {}s)".format(no_client_seconds))
        if _server_instance:
            try:
                _server_instance.shutdown()
            except Exception:
                pass
            return


# ---- config helpers ---------------------------------------------------------

def _load_runtime_config():
    from runtime.wnmp_config import load_config
    return load_config(_root_dir)


def _get_panel_config():
    """Return (host, port) from runtime.ini or defaults."""
    cfg = _load_runtime_config()
    host = cfg.get("PANEL_HOST", "127.0.0.1") or "127.0.0.1"
    try:
        port = int(cfg.get("PANEL_PORT", "8787"))
    except (ValueError, TypeError):
        port = 8787
    return host, port


# ---- version query with cache -----------------------------------------------
_version_cache = {}  # {component: {"version": str, "cached_at": float}}
_version_cache_lock = threading.Lock()


def _get_version_cache_ttl():
    cfg = _load_runtime_config()
    try:
        return int(cfg.get("PANEL_VERSION_CACHE_TTL", "600"))
    except (ValueError, TypeError):
        return 600


def _query_version(component):
    """Query component version via subprocess. Returns version string or error message."""
    import subprocess
    CREATE_NO_WINDOW = 0x08000000

    if component == "nginx":
        exe = os.path.join(_root_dir, "bin", "nginx", "nginx.exe")
        if not os.path.isfile(exe):
            return None, "未找到 nginx.exe"
        try:
            result = subprocess.run(
                [exe, "-v"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW, shell=False
            )
            # nginx -v outputs to stderr
            output = (result.stderr or "").strip()
            if not output:
                output = (result.stdout or "").strip()
            return output, None
        except subprocess.TimeoutExpired:
            return None, "版本查询超时"
        except Exception as e:
            return None, "查询失败: " + str(e)

    elif component == "php":
        # 优先查询 php-cgi.exe（PHP-CGI 卡片语义），fallback 到 php.exe
        exe = os.path.join(_root_dir, "bin", "php", "php-cgi.exe")
        fallback = False
        if not os.path.isfile(exe):
            exe = os.path.join(_root_dir, "bin", "php", "php.exe")
            fallback = True
        if not os.path.isfile(exe):
            return None, "未找到 php-cgi.exe 或 php.exe"
        try:
            result = subprocess.run(
                [exe, "-v"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW, shell=False
            )
            output = (result.stdout or "").strip()
            # 只取第一行
            if output:
                output = output.split("\n")[0]
            if fallback and output:
                output += " (via php.exe)"
            return output, None
        except subprocess.TimeoutExpired:
            return None, "版本查询超时"
        except Exception as e:
            return None, "查询失败: " + str(e)

    elif component == "mysql":
        exe = os.path.join(_root_dir, "bin", "mysql", "bin", "mysqld.exe")
        if not os.path.isfile(exe):
            return None, "未找到 mysqld.exe"
        try:
            result = subprocess.run(
                [exe, "--version"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW, shell=False
            )
            output = (result.stdout or "").strip()
            if output:
                output = output.split("\n")[0]
            return output, None
        except subprocess.TimeoutExpired:
            return None, "版本查询超时"
        except Exception as e:
            return None, "查询失败: " + str(e)

    return None, "未知组件"


def _get_version(component, force=False):
    """Get version with cache. Returns dict with version/error."""
    ttl = _get_version_cache_ttl()
    now = time.time()

    if not force:
        with _version_cache_lock:
            if component in _version_cache:
                entry = _version_cache[component]
                if now - entry["cached_at"] < ttl:
                    return {"component": component, "version": entry["version"], "error": entry.get("error")}

    version, error = _query_version(component)
    entry = {"version": version, "error": error, "cached_at": now}
    with _version_cache_lock:
        _version_cache[component] = entry

    return {"component": component, "version": version, "error": error}


# ---- HTTP request handler ---------------------------------------------------

def _safe_parse_int(value, default):
    """安全解析 int，非法值回退默认值。"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class PanelHandler(BaseHTTPRequestHandler):
    """HTTP request handler for WNMP Panel."""

    def log_message(self, format, *args):
        """Suppress default logging to stderr."""
        pass

    def send_json(self, data, status=200):
        """Send JSON response. Safe against client disconnect."""
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def send_file(self, path, content_type=None, no_cache=False):
        """Send a file with auto Content-Type detection. Safe against client disconnect.

        no_cache=True 时添加 Cache-Control: no-store, Pragma: no-cache, Expires: 0，
        确保浏览器不缓存 index.html/app.js/style.css 等关键资源。
        """
        if not os.path.isfile(path):
            self.send_error(404, "File not found")
            return
        try:
            with open(path, "rb") as f:
                body = f.read()
        except Exception:
            self.send_error(500, "Read error")
            return

        if content_type is None:
            content_type, _ = mimetypes.guess_type(path)
            if content_type is None:
                content_type = "application/octet-stream"

        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            # IE11 兼容：text/html 响应增加 X-UA-Compatible 头，降低兼容视图降级风险
            if content_type and content_type.lower().startswith("text/html"):
                self.send_header("X-UA-Compatible", "IE=edge")
            self.send_header("Content-Length", str(len(body)))
            if no_cache:
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def _read_json_body(self, allow_text_plain=False):
        """Read and parse JSON body. Returns (data, error_response).

        默认只接受 application/json。
        allow_text_plain=True 时容忍 text/plain（用于 /api/panel/client-close 的 sendBeacon）。
        非 application/json 的业务 POST 返回 415。
        """
        ct = self.headers.get("Content-Type", "")
        ct_lower = ct.lower()
        if "application/json" not in ct_lower:
            if not (allow_text_plain and "text/plain" in ct_lower):
                return None, {"success": False, "message": "Content-Type must be application/json"}
        cl = int(self.headers.get("Content-Length", 0))
        if cl == 0:
            return None, {"success": False, "message": "empty body"}
        try:
            body = self.rfile.read(cl)
            return json.loads(body.decode("utf-8")), None
        except Exception:
            return None, {"success": False, "message": "invalid JSON"}

    # ---- route dispatch -----------------------------------------------------

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            self._serve_index()
        elif path == "/api/ping":
            self._api_ping()
        elif path == "/api/status":
            self._api_status()
        elif path == "/api/status/debug":
            self._api_status_debug()
        elif path == "/api/autostart/status":
            self._api_autostart_status()
        elif path == "/api/panel/config":
            self._api_panel_config()
        elif path == "/api/panel-version":
            self._api_panel_version()
        elif path == "/api/versions":
            self._api_versions()
        elif path.startswith("/api/logs/"):
            self._api_logs(path)
        elif path == "/api/config-file":
            self._api_config_file_get()
        elif path == "/api/environment-info":
            self._api_environment_info()
        elif path == "/assets/compat.js":
            self._serve_asset("compat.js", "application/javascript")
        elif path == "/assets/app.js":
            self._serve_asset("app.js", "application/javascript")
        elif path == "/assets/i18n.js":
            self._serve_asset("i18n.js", "application/javascript")
        elif path == "/assets/style.css":
            self._serve_asset("style.css", "text/css")
        elif path == "/favicon.ico" or path == "/assets/wnmp-panel.ico":
            self._serve_asset("wnmp-panel.ico", "image/x-icon")
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/action":
            self._api_action_route()
        elif path == "/api/panel/heartbeat":
            self._api_heartbeat()
        elif path == "/api/panel/client-close":
            self._api_client_close()
        elif path == "/api/config-file":
            self._api_config_file_post()
        elif path == "/api/open-directory":
            self._api_open_directory()
        else:
            self.send_error(404, "Not found")

    # ---- route handlers -----------------------------------------------------

    def _serve_index(self):
        path = os.path.join(_panel_dir, "templates", "index.html")
        self.send_file(path, "text/html; charset=utf-8", no_cache=True)  # index.html 不缓存，确保引用最新 app.js

    def _serve_asset(self, filename, content_type):
        path = os.path.join(_panel_dir, "assets", filename)
        self.send_file(path, content_type, no_cache=True)  # app.js/style.css 不缓存，避免旧版 JS 残留

    def _api_ping(self):
        """GET /api/ping - lightweight health check."""
        self.send_json({
            "success": True,
            "app": "WNMP Panel",
            "ready": True,
            "timestamp": int(time.time()),
        })

    def _api_panel_config(self):
        """GET /api/panel/config - return panel config for frontend."""
        cfg = _load_runtime_config()
        self.send_json({
            "success": True,
            "panel_exit_on_close": cfg.get("PANEL_EXIT_ON_CLOSE", "1") == "1",
            "heartbeat_interval": _safe_parse_int(cfg.get("PANEL_HEARTBEAT_INTERVAL"), 5),
            "no_client_exit_seconds": _safe_parse_int(cfg.get("PANEL_NO_CLIENT_EXIT_SECONDS"), 60),
            "client_stale_seconds": _safe_parse_int(cfg.get("PANEL_CLIENT_STALE_SECONDS"), 300),
            "version_cache_ttl": _safe_parse_int(cfg.get("PANEL_VERSION_CACHE_TTL"), 600),
            "panel_port": _safe_parse_int(cfg.get("PANEL_PORT"), 8787),
        })

    def _api_panel_version(self):
        """GET /api/panel-version - 返回 Panel 自身版本信息。

        不执行 subprocess，不启动/停止任何服务，不返回敏感凭据。
        版本号来源：runtime/version.py（集中维护，优先读 VERSION 文件）。
        同时返回关键路径信息（vhosts 等），前端优先使用后端返回值，
        避免 P2 路径切换时前端硬编码路径漏改。
        """
        try:
            from runtime.version import get_panel_info
            info = get_panel_info()
            # 路径收敛：通过统一路径模块获取 vhosts 路径，前端不再硬编码
            vhosts_abs = get_nginx_vhosts_dir(_root_dir)
            vhosts_rel = os.path.relpath(vhosts_abs, _root_dir).replace(os.sep, "/")
            info["vhosts_rel"] = vhosts_rel
            info["vhosts_abs"] = os.path.normpath(vhosts_abs)
            self.send_json({"success": True, **info})
        except Exception as e:
            # version.py 或 VERSION 文件异常时兜底返回
            self.send_json({
                "success": True,
                "panel_name": "WNMP Runtime Panel",
                "panel_version": "unknown",
                "build_date": "",
                "root_dir": _root_dir,
            })

    def _api_autostart_status(self):
        """GET /api/autostart/status - 查询开机自启动状态。

        不走动作锁，不写 action_output.log。
        直接调用 wnmp_autostart.autostart_status 获取状态。
        返回 success=true/false 基于 query_ok，前端必须看 state 字段。
        """
        try:
            from runtime.wnmp_autostart import autostart_status
            from runtime.wnmp_log import setup_logging
            logger = setup_logging(_root_dir)
            cfg = _load_runtime_config()
            result = autostart_status(_root_dir, cfg, logger)
            # query_ok=false 时 success=false，前端可区分 state
            success = result.get("query_ok", False)
            self.send_json({"success": success, **result})
        except Exception as e:
            self.send_json({"success": False, "query_ok": False, "exists": False,
                            "enabled": False, "state": "error", "message": "查询失败: " + str(e),
                            "error": str(e)})

    def _api_versions(self):
        """GET /api/versions?component=nginx|php|mysql|all&force=1"""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        component = qs.get("component", ["all"])[0].lower()
        force = qs.get("force", ["0"])[0] == "1"

        allowed = {"nginx", "php", "mysql", "all"}
        if component not in allowed:
            self.send_json({"success": False, "message": "无效组件，允许: nginx, php, mysql, all"}, 400)
            return

        if component == "all":
            results = {}
            for comp in ["nginx", "php", "mysql"]:
                results[comp] = _get_version(comp, force=force)
            self.send_json({"success": True, "versions": results})
        else:
            result = _get_version(component, force=force)
            self.send_json({"success": True, "versions": {component: result}})

    def _api_heartbeat(self):
        """POST /api/panel/heartbeat - client heartbeat."""
        data, err = self._read_json_body()
        if err:
            status = 415 if "Content-Type" in err.get("message", "") else 400
            self.send_json(err, status)
            return
        client_id = data.get("client_id")
        if not client_id:
            self.send_json({"success": False, "message": "missing client_id"}, 400)
            return
        _register_client(client_id)
        self.send_json({"success": True})

    def _api_client_close(self):
        """POST /api/panel/client-close - client close notification.

        对 sendBeacon 容错：即使 JSON 解析失败也尽量从原始 body 解析 client_id。
        """
        try:
            cl = int(self.headers.get("Content-Length", 0))
            if cl > 0:
                raw_body = self.rfile.read(cl)
            else:
                raw_body = b""
        except Exception:
            raw_body = b""

        client_id = None

        # 尝试 JSON 解析
        try:
            data = json.loads(raw_body.decode("utf-8"))
            client_id = data.get("client_id")
        except Exception:
            # JSON 解析失败，尝试从原始 body 中提取 client_id
            try:
                text = raw_body.decode("utf-8", errors="replace")
                # 尝试简单查找 "client_id":"xxx" 模式
                import re
                m = re.search(r'"client_id"\s*:\s*"([^"]+)"', text)
                if m:
                    client_id = m.group(1)
            except Exception:
                pass

        if client_id:
            _unregister_client(client_id)
        # 无论是否解析到 client_id，都返回 success
        self.send_json({"success": True})

    def _api_status(self):
        """GET /api/status - return real status snapshot.

        不再返回 mysql_root_password_file 或明文密码。
        超过 1000ms 时写入慢查询日志。
        出现 unknown 状态时写入诊断日志。
        """
        t0 = time.time()
        try:
            status = get_full_status()
        except Exception as e:
            status = {"success": False, "message": str(e)}
        total_ms = int((time.time() - t0) * 1000)

        # 慢查询日志：超过 1 秒时记录总耗时和各组件 state/port/耗时
        if total_ms > 1000:
            try:
                parts = []
                timing = status.get("_timing_ms", {})
                for comp in ("nginx", "php", "mysql"):
                    comp_st = status.get(comp, {})
                    comp_ms = timing.get(comp, "?")
                    if comp_st:
                        parts.append("{}={}/port={}/{}ms".format(comp, comp_st.get("state", "?"), comp_st.get("port", "?"), comp_ms))
                    else:
                        parts.append("{}=n/a/{}ms".format(comp, comp_ms))
                _panel_log("status slow total={}ms {}".format(total_ms, " ".join(parts)))
            except Exception:
                _panel_log("status slow total={}ms".format(total_ms))

        # 诊断日志：任何组件 unknown 时记录详情
        try:
            for comp in ("nginx", "php", "mysql"):
                comp_st = status.get(comp, {})
                if comp_st and comp_st.get("state") == "unknown":
                    _panel_log("status {} unknown port={} port_open={} pid={} stale_pid={} adopted={} message={}".format(
                        comp,
                        comp_st.get("port", "?"),
                        comp_st.get("port_open"),
                        comp_st.get("pid"),
                        comp_st.get("stale_pid"),
                        comp_st.get("adopted"),
                        comp_st.get("message", ""),
                    ))
        except Exception:
            pass

        self.send_json(status)

    def _api_status_debug(self):
        """GET /api/status/debug - detailed debug info for manual troubleshooting.

        仅手动访问排错用，前端每秒轮询不调用此接口。
        """
        try:
            from runtime.panel.status import get_full_status_debug
            debug = get_full_status_debug()
        except Exception as e:
            debug = {"success": False, "message": str(e)}
        self.send_json(debug)

    def _api_action_route(self):
        """POST /api/action - execute action with global lock."""
        data, err = self._read_json_body()
        if err:
            # Content-Type 不匹配返回 415，其它错误返回 400
            status = 415 if "Content-Type" in err.get("message", "") else 400
            self.send_json(err, status)
            return

        action = data.get("action")
        if not action:
            self.send_json({"success": False, "message": "no action"}, 400)
            return

        if not is_valid_action(action):
            self.send_json({"success": False, "message": "unknown action"}, 400)
            return

        self._api_action(action)

    def _api_action(self, action):
        """Execute action with global lock."""
        global _action_in_progress

        acquired = _action_lock.acquire(blocking=False)
        if not acquired:
            self.send_json({
                "success": False,
                "busy": True,
                "action": action,
                "message": "已有操作正在执行，请稍候"
            })
            return

        _action_in_progress = True
        started_at = time.time()
        # 记录 action 执行前的日志文件大小 offset，用于限制错误提取范围
        _action_log_path = os.path.join(_root_dir, "logs", "panel", "action_output.log")
        _log_offset_before = 0
        try:
            if os.path.isfile(_action_log_path):
                _log_offset_before = os.path.getsize(_action_log_path)
        except Exception:
            pass
        action_result = None
        error_msg = ""
        try:
            action_result = execute_action(action)
        except Exception as e:
            error_msg = str(e)
        finally:
            _action_in_progress = False
            _action_lock.release()

        # execute_action 现在返回 dict: {"exit_code", "panel_result_file", "message"}
        if isinstance(action_result, dict):
            exit_code = action_result.get("exit_code", 1)
            panel_result_file = action_result.get("panel_result_file")
        else:
            # 兼容旧返回 int
            exit_code = action_result if action_result is not None else 1
            panel_result_file = None

        duration_ms = int((time.time() - started_at) * 1000)
        finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

        # Build message based on exit_code
        # 优先级：action_result.message > CLI stdout/stderr > 日志匹配块 > 通用文案
        action_result_message = ""
        if isinstance(action_result, dict):
            action_result_message = action_result.get("message", "")

        if exit_code == -1:
            message = action_result_message or "环境尚未初始化，请先初始化环境"
        elif exit_code == -2:
            message = action_result_message or "缺少项目内置 Python：bin\\python\\python.exe，请检查程序包是否完整"
        elif exit_code == -3:
            message = action_result_message or "执行超时，请查看动作输出日志"
        elif exit_code == -4:
            message = action_result_message or "执行异常，请查看动作输出日志"
        elif exit_code == 0:
            # 优先使用 action_result 返回的 message（如 open_site 的提示信息）
            message = action_result_message or "执行完成"
            if action == "stop_env":
                from runtime.wnmp_state import is_initialized
                if not is_initialized(_root_dir):
                    message = "环境尚未初始化，无需停止"
        else:
            # 优先使用 action_result 返回的 message（如非 CLI 动作的错误信息）
            if action_result_message:
                message = action_result_message
            elif error_msg:
                message = "执行失败: " + error_msg
            else:
                message = "执行失败，请查看运行日志"
            # 从 action_output.log 提取本次动作的错误摘要，防止旧错误污染
            # 只有 CLI 执行的 action 才从日志提取，且日志 Action 名称必须匹配
            # 仅当上面没有获得明确 message 时才用日志覆盖
            # 只从本次 action 执行前的日志 offset 之后读取，避免读取旧错误
            if message in ("执行失败，请查看运行日志", "执行失败: " + error_msg):
                try:
                    from runtime.panel.actions import CLI_ACTION_MAP
                    cli_action_name = CLI_ACTION_MAP.get(action)
                    if cli_action_name:
                        log_path = os.path.join(_root_dir, "logs", "panel", "action_output.log")
                        if os.path.isfile(log_path):
                            # 只读取本次 action 执行后的新增日志
                            with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                                if _log_offset_before > 0:
                                    lf.seek(_log_offset_before)
                                all_lines = lf.readlines()
                            # 定位最近一次动作分隔块：从最后一个 "Action:" 行开始
                            action_start = -1
                            for i in range(len(all_lines) - 1, -1, -1):
                                stripped_line = all_lines[i].strip()
                                if stripped_line.startswith("Action:"):
                                    # 验证 Action 名称匹配当前 action 对应的 cli_action
                                    logged_action = stripped_line[len("Action:"):].strip()
                                    # 匹配：完全相同或以 cli_action_name 开头（兼容 reset-config --force 等）
                                    if logged_action == cli_action_name or logged_action.startswith(cli_action_name + " "):
                                        action_start = i
                                    break  # 只看最近一条 Action 行
                            if action_start >= 0:
                                # 本次动作范围：从 action_start 到文件末尾
                                action_lines = all_lines[action_start:]
                                # 提取关键错误行，支持多种格式
                                error_lines = []
                                _ERROR_KEYWORDS = [
                                    "ERROR:", "[ERROR]", "nginx: [emerg]", "nginx: [error]",
                                    "failed", "invalid", "cannot", "bind()", "Address already in use",
                                    # 中文业务提示关键字
                                    "配置尚未应用", "旧配置下运行", "请执行重载或重启",
                                    "环境未完全启动", "状态不受影响", "端口被外部程序占用",
                                    "旧端口仍由本项目", "配置校验失败", "仍被本项目 Nginx 占用",
                                    "仍由本项目 nginx 监听", "被外部程序",
                                ]
                                for line in action_lines:
                                    stripped = line.strip()
                                    if not stripped:
                                        continue
                                    low = stripped.lower()
                                    # 跳过分隔线和元数据行
                                    if stripped.startswith("=") or stripped.startswith("Action:") or stripped.startswith("Command:") or stripped.startswith("CWD:") or stripped.startswith("Started:") or stripped.startswith("Exit code:") or stripped.startswith("Timed out:") or stripped.startswith("Finished:") or stripped.startswith("WARNING:"):
                                        continue
                                    for kw in _ERROR_KEYWORDS:
                                        if kw.lower() in low:
                                            error_lines.append(stripped)
                                            break
                                if error_lines:
                                    # 取关键错误行，最多 6 行，截取前 1200 字符
                                    cli_error = "\n".join(error_lines[:6])
                                    # 去掉 "ERROR: " 前缀（如果所有行都有）
                                    if all(l.startswith("ERROR: ") for l in error_lines):
                                        cli_error = "\n".join(l[7:] for l in error_lines[:6])
                                    if len(cli_error) > 1200:
                                        cli_error = cli_error[:1200] + "..."
                                    message = cli_error
                except Exception:
                    pass
            # 自启动动作失败时，检测管理员权限问题
            if action in ("install_autostart", "uninstall_autostart") and exit_code != 0:
                try:
                    log_path = os.path.join(_root_dir, "logs", "panel", "action_output.log")
                    if os.path.isfile(log_path):
                        with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                            tail_lines = lf.readlines()[-20:]
                        tail_text = "".join(tail_lines)
                        if "Administrator" in tail_text or "Access is denied" in tail_text:
                            message = "启用/关闭开机自启动需要管理员权限，请以管理员权限运行 WNMPanel.exe"
                except Exception:
                    pass

        # MySQL 初始密码：start_env/init_env 时先从临时结果文件读取（必须在 get_full_status 之前）
        # 即使 get_full_status 失败，也必须返回 mysql_root_password
        mysql_root_password = None
        if action in ("start_env", "init_env") and panel_result_file:
            mysql_root_password = _read_panel_result_file(panel_result_file)

        # 动作执行后清理进程收养缓存，确保状态快照反映最新进程状态
        try:
            from runtime.panel.status import _clear_adopted_cache
            _clear_adopted_cache()
        except Exception:
            pass

        # 单组件动作：只返回 affected_component 和该组件的 component_status
        # 环境级动作：返回全量 status_snapshot
        _COMPONENT_ACTION_MAP = {
            "start_nginx": "nginx", "stop_nginx": "nginx", "restart_nginx": "nginx", "reload_nginx": "nginx",
            "start_php": "php", "stop_php": "php", "restart_php": "php",
            "start_mysql": "mysql", "stop_mysql": "mysql", "restart_mysql": "mysql",
        }
        _ENV_ACTIONS = {"start_env", "init_env", "stop_env", "restart_env"}
        # 重置配置后需刷新全量状态（config_dirty 可能变化）
        _CONFIG_ACTIONS = {"reset_config"}

        affected_component = _COMPONENT_ACTION_MAP.get(action)
        status_snapshot = None
        component_status = None

        if affected_component:
            # 单组件动作：只探测受影响组件的状态，不影响其它模块
            try:
                component_status = get_component_status(affected_component)
            except Exception:
                component_status = {"running": False, "state": "unknown", "message": "检测异常"}
        elif action in _ENV_ACTIONS:
            # 环境级动作：返回全量 status_snapshot
            try:
                status_snapshot = get_full_status()
            except Exception:
                pass
        elif action in _CONFIG_ACTIONS:
            # 配置级动作（如重置配置）：返回全量状态，前端需刷新 config_dirty 等
            try:
                status_snapshot = get_full_status()
            except Exception:
                pass

        action_log_file = os.path.join(_root_dir, "logs", "panel", "action_output.log")

        result = {
            "success": exit_code == 0,
            "busy": False,
            "action": action,
            "exit_code": exit_code,
            "message": message,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at)),
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "status_snapshot": status_snapshot,
            "affected_component": affected_component,
            "component_status": component_status,
            "action_log_file": action_log_file,
        }
        # 仅在初始化成功时附带 mysql_root_password，不附带密码文件路径
        if mysql_root_password:
            result["mysql_root_password"] = mysql_root_password

        # install_autostart 成功后附带结构化自启动状态，前端可优先使用
        if action == "install_autostart" and exit_code == 0:
            try:
                from runtime.wnmp_autostart import autostart_status
                from runtime.wnmp_config import load_config
                from runtime.wnmp_log import setup_logging
                cfg = load_config(_root_dir)
                status_logger = setup_logging(_root_dir)
                autostart_info = autostart_status(_root_dir, cfg, status_logger)
                result["autostart_status"] = {
                    "exists": autostart_info.get("exists", False),
                    "enabled": autostart_info.get("enabled", False),
                    "state": autostart_info.get("state", "unknown"),
                    "task_name": autostart_info.get("task_name", ""),
                    "working_directory": autostart_info.get("working_directory", ""),
                    "command": autostart_info.get("command", ""),
                    "arguments": autostart_info.get("arguments", ""),
                    "owned": autostart_info.get("owned"),
                    "verified": True,
                }
            except Exception:
                pass  # 附加状态失败不影响主响应

        self.send_json(result)

    def _api_logs(self, path):
        """GET /api/logs/runtime?lines=N or /api/logs/action?lines=N"""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        lines = 100
        try:
            lines = int(qs.get("lines", ["100"])[0])
            lines = max(1, min(lines, 1000))
        except Exception:
            pass

        if path.startswith("/api/logs/action"):
            log_file = os.path.join(_root_dir, "logs", "panel", "action_output.log")
        else:
            log_file = os.path.join(_root_dir, "logs", "runtime", "runtime.log")

        if not os.path.isfile(log_file):
            self.send_json({"success": False, "lines": 0, "content": "", "message": "日志文件不存在"})
            return

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            content = "".join(all_lines[-lines:])
            self.send_json({"success": True, "lines": len(all_lines[-lines:]), "content": content})
        except Exception as e:
            self.send_json({"success": False, "lines": 0, "content": "", "message": "读取日志失败: " + str(e)})

    # ---- Config file API ---------------------------------------------------

    def _api_config_file_get(self):
        """GET /api/config-file?name=nginx|nginx-site|php|php-cgi|mysql|runtime"""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        name = qs.get("name", [""])[0]

        # 白名单校验：允许 Nginx/PHP/MySQL 配置文件 + runtime.ini 运行器配置
        # 路径收敛：通过统一路径模块获取配置文件路径
        config_map = {
            "nginx": get_nginx_conf_path(_root_dir),
            "nginx-site": get_nginx_site_conf_path(_root_dir),
            "php": get_php_ini_path(_root_dir),
            "php-cgi": get_php_cgi_ini_path(_root_dir),
            "mysql": get_mysql_ini_path(_root_dir),
            "runtime": get_runtime_ini_path(_root_dir),
        }
        if name not in config_map:
            self.send_json({"success": False, "message": "无效配置名称"}, 400)
            return
        path = config_map[name]
        if not os.path.isfile(path):
            self.send_json({"success": False, "message": "配置文件不存在"}, 404)
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.send_json({"success": True, "name": name, "content": content})
        except Exception as e:
            self.send_json({"success": False, "message": "读取失败: " + str(e)})

    def _api_environment_info(self):
        """GET /api/environment-info - 返回环境信息数据源。

        委托给 runtime.panel.environment_info.get_environment_info()，
        只读接口，不涉及服务启停、配置保存、配置生成。
        """
        try:
            from runtime.panel.environment_info import get_environment_info
            info = get_environment_info()
            self.send_json({"success": True, **info})
        except Exception as e:
            import traceback
            _log_panel_error(_root_dir, "/api/environment-info exception: " + str(e) + "\n" + traceback.format_exc())
            self.send_json({"success": False, "message": "获取环境信息失败: " + str(e)})

    def _api_open_directory(self):
        """POST /api/open-directory - 安全打开目录。

        只接受白名单 open_key，不接受任意路径。
        非 localhost 访问时拒绝操作。
        """
        try:
            # 远程访问边界：非本机访问拒绝打开目录
            client_host = self.client_address[0] if hasattr(self, 'client_address') and self.client_address else ''
            if client_host and client_host not in ('127.0.0.1', '::1', 'localhost'):
                self.send_json({"success": False, "message": "打开目录仅支持本机面板访问"})
                return

            data, err = self._read_json_body()
            if err:
                self.send_json(err)
                return

            open_key = data.get("open_key", "")
            from runtime.panel.environment_info import open_directory
            result = open_directory(open_key)
            self.send_json(result)
        except Exception as e:
            import traceback
            _log_panel_error(_root_dir, "/api/open-directory exception: " + str(e) + "\n" + traceback.format_exc())
            self.send_json({"success": False, "message": "目录打开失败: " + str(e)})

    def _api_config_file_post(self):
        """POST /api/config-file - 保存配置文件，自动备份+校验+回滚。

        业务逻辑委托给 runtime.panel.config_editor.save_config_file()，
        本方法只负责 HTTP 入参解析和 JSON 返回。
        最外层异常保护：任何内部异常都返回 JSON，不允许连接中断。
        """
        try:
            data, err = self._read_json_body()
            if err:
                status = 415 if "Content-Type" in err.get("message", "") else 400
                self.send_json(err, status)
                return

            name = data.get("name", "")
            content = data.get("content", "")

            from runtime.panel.config_editor import save_config_file
            result = save_config_file(_root_dir, name, content)
            self.send_json(result)

        except Exception as e:
            # 最外层异常保护：任何未捕获异常都返回 JSON，记录完整 traceback
            import traceback
            _log_panel_error(_root_dir, "/api/config-file POST exception: " + str(e) + "\n" + traceback.format_exc())
            self.send_json({
                "success": False,
                "message": "保存配置时发生内部错误: " + str(e),
                "affected_component": None,
                "config_dirty": None,
            })


# ---- Panel result file helpers (cross-process password passing) -----------

def _read_panel_result_file(result_file):
    """读取临时结果文件中的 MySQL 初始密码，然后删除该文件。

    不在日志中记录明文密码。删除失败时写 warning 但不记录密码。
    """
    if not result_file or not os.path.isfile(result_file):
        return None

    password = None
    try:
        with open(result_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        password = data.get("mysql_root_password")
    except Exception:
        pass

    # 立即删除临时文件
    try:
        os.remove(result_file)
    except Exception:
        _panel_log("WARNING: failed to delete panel result file (password not logged)")

    return password

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server for handling concurrent requests."""
    daemon_threads = True


def run_server(host="127.0.0.1", port=8787, open_browser=True):
    """Start the Panel HTTP server."""
    global _server_instance, _exit_monitor_thread

    # Python 进程权限诊断
    try:
        import ctypes
        is_admin = False
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            pass

        user_name = os.environ.get("USERNAME", "unknown")
        pid = os.getpid()
        exe = sys.executable
        cwd = os.getcwd()

        _panel_log("权限诊断: Python PID={} exe={} cwd={}".format(pid, exe, cwd))
        _panel_log("权限诊断: is_admin={} user={}".format(is_admin, user_name))

        # 完整性级别
        try:
            import ctypes.wintypes  # 必须在使用 wintypes.DWORD 之前导入
            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32
            TOKEN_QUERY = 0x0008
            TokenIntegrityLevel = 25
            hToken = ctypes.c_void_p()
            # OpenProcessToken 属于 advapi32，不属于 kernel32
            if advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(hToken)):
                needed = ctypes.wintypes.DWORD()
                advapi32.GetTokenInformation(hToken, TokenIntegrityLevel, None, 0, ctypes.byref(needed))
                if needed.value > 0:
                    buf = (ctypes.c_byte * needed.value)()
                    if advapi32.GetTokenInformation(hToken, TokenIntegrityLevel, buf, needed, ctypes.byref(needed)):
                        import struct
                        # 读取 SID 指针（偏移 0）
                        ptr_size = ctypes.sizeof(ctypes.c_void_p)
                        sid_ptr = ctypes.c_void_p.from_buffer(buf, 0)
                        sid = sid_ptr.value
                        if sid:
                            sub_auth_count_val = ctypes.c_ubyte.from_address(sid + 1).value
                            # SubAuthority 最后一个值在偏移 12 + (sub_auth_count-1)*4
                            last_sub_offset = 12 + (sub_auth_count_val - 1) * 4
                            int_level = ctypes.c_ulong.from_address(sid + last_sub_offset).value
                            level_name = "Unknown"
                            if int_level >= 0x4000:
                                level_name = "System"
                            elif int_level >= 0x3000:
                                level_name = "High"
                            elif int_level >= 0x2000:
                                level_name = "Medium"
                            elif int_level >= 0x1000:
                                level_name = "Low"
                            _panel_log("权限诊断: integrity_level=0x{:X} ({})".format(int_level, level_name))
                    kernel32.CloseHandle(hToken)
        except Exception as e:
            _panel_log("权限诊断: integrity_level check failed: {}".format(e))
    except Exception as e:
        _panel_log("权限诊断: failed: {}".format(e))

    addr = (host, port)
    _server_instance = ThreadedHTTPServer(addr, PanelHandler)
    _panel_log("WNMP Panel listening on http://{}:{}".format(host, port))

    # 启动自动退出监控线程
    _exit_monitor_thread = threading.Thread(target=_exit_monitor_loop, daemon=True)
    _exit_monitor_thread.start()

    if open_browser:
        webbrowser.open("http://{}:{}".format(host, port))

    try:
        _server_instance.serve_forever()
    except KeyboardInterrupt:
        _panel_log("Panel shutting down (KeyboardInterrupt)")
    finally:
        # 确保 server_close 被调用，释放端口
        try:
            _server_instance.server_close()
        except Exception:
            pass
        _panel_log("Panel Server exited")


# ---- main ------------------------------------------------------------------

def main(argv=None):
    """Main entry point for Panel Server."""
    if argv is None:
        argv = sys.argv[1:]

    host, port = _get_panel_config()
    open_browser = True

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
            i += 2
        elif arg == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except (ValueError, TypeError):
                port = 8787
            i += 2
        elif arg == "--no-browser":
            open_browser = False
            i += 1
        else:
            i += 1

    os.chdir(_root_dir)
    run_server(host, port, open_browser)


if __name__ == "__main__":
    main()
