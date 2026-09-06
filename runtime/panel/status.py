# -*- coding: utf-8 -*-
"""
WNMP Panel Status - Real status snapshot.

状态判断模型：三组件统一返回 running/stopped/external/unknown 四类状态：
- running: 配置端口已监听且 listener path 属于当前 rootDir
- stopped: 配置端口未监听，且没有发现本项目对应进程
- external: 配置端口已监听但 listener path 不属于当前 rootDir
- unknown: 端口已监听但无法确认 listener path（权限不足或系统查询失败）

PID 文件降级为缓存：runtime/pids/*.pid 只用于显示 PID、辅助诊断、辅助停止；
PID 文件不存在或 stale 不影响 running/stopped 判断。

两个接口：
- /api/status: 前端每秒轮询，必须快速稳定。
  端口 listener 查询使用短超时，不做 PowerShell 全路径扫描。
- /api/status/debug: 仅用于手动排查，前端定时轮询不得调用。
  包含完整 listener 列表、进程路径、owner、scan_ms 等详细诊断。
"""
import os
import sys
import time

from runtime.panel.paths import get_root_dir

_root_dir = get_root_dir()
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

# 状态探测无额外线程级硬超时，依赖底层 netstat/WinAPI/tasklist 调用自身 timeout 参数
# 每个模块独立 try/except，单模块探测异常只影响自身，不会导致其它模块 unknown

# listener 查询短超时（秒），/api/status 热路径使用
_LISTENER_QUERY_TIMEOUT = 1  # 热路径超时（秒），宁可快速返回 unknown 也不能卡住状态检测

# 组件 PID 文件名映射
_PID_FILENAME_MAP = {
    "nginx": "nginx.pid",
    "php": "php-cgi.pid",
    "mysql": "mysqld.pid",
}

# 组件可执行文件路径（相对于 root_dir）
_EXECUTABLE_PATH_MAP = {
    "nginx": os.path.join("bin", "nginx", "nginx.exe"),
    "php": os.path.join("bin", "php", "php-cgi.exe"),
    "mysql": os.path.join("bin", "mysql", "bin", "mysqld.exe"),
}


def _load_config():
    from runtime.wnmp_config import load_config
    return load_config(_root_dir)


def _load_state():
    from runtime.wnmp_state import load_state
    return load_state(_root_dir)


def _read_pid(component):
    """读取组件 PID 文件，返回 pid 或 None。PID 文件仅作为缓存/辅助。"""
    from runtime.wnmp_process import read_pid_file
    pid_dir = os.path.join(_root_dir, "runtime", "pids")
    filename = _PID_FILENAME_MAP.get(component)
    if not filename:
        return None
    return read_pid_file(pid_dir, filename)


def _is_port_open(host, port):
    """端口检测，0.3s 短超时。"""
    from runtime.wnmp_process import is_port_listening
    return is_port_listening(host, port, timeout=0.3)


def _status_log(message):
    """写入诊断日志到 panel_server.log，仅用于状态变化时记录。"""
    try:
        log_dir = os.path.join(_root_dir, "logs", "panel")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "panel_server.log")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("[{}] [status] {}\n".format(timestamp, message))
    except Exception:
        pass


def _check_port_ownership(port, host, root_dir, expected_exe_rel):
    """检查单个端口的 listener 归属，返回 check_listener_ownership 结果。

    使用短超时，适合 /api/status 热路径。
    不查询 owner（include_owner 默认 False），避免热路径执行 tasklist /V。
    """
    from runtime.wnmp_process import check_listener_ownership
    return check_listener_ownership(
        port, expected_exe_rel,
        host=host, root_dir=root_dir,
        timeout=_LISTENER_QUERY_TIMEOUT, logger=None
    )


def _get_nginx_status_fast(cfg):
    """Nginx 状态检测：基于配置 listen + listener path（使用 is_expected 精确匹配）。

    状态判定逻辑：
    - 所有配置 listen 端口均由 nginx.exe 精确监听且 config_dirty=false → running
    - 所有配置端口均由 nginx.exe 监听但 config_dirty=true → pending_reload
    - 所有配置端口未监听且无本项目 nginx 运行 → stopped
      （stopped+dirty 时 message 提示"已停止，配置已修改，启动 Nginx 后生效"）
    - 任一端口被外部程序监听 → external
    - 任一端口已监听但无法确认 path → unknown
    - 部分端口 running 部分异常 → partial
    - 配置文件已解析但没有任何 listen → error
    - 配置文件无法解析 → unknown + warning
    PID 文件只显示，不决定状态。
    """
    from runtime.wnmp_config import get_effective_nginx_listens
    from runtime.wnmp_state import is_component_config_dirty, get_component_config_apply_state

    result = get_effective_nginx_listens(_root_dir, cfg)
    http_ports = result["http"]
    https_ports = result["https"]
    parsed = result["parsed"]
    fallback = result["fallback"]
    config_dirty = is_component_config_dirty(_root_dir, "nginx")

    # 配置文件已解析但没有任何 listen → 配置错误
    if parsed and not http_ports and not https_ports:
        return {
            "running": False,
            "state": "error",
            "pid": None,
            "stale_pid": None,
            "port": None,
            "port_open": False,
            "ports": [],
            "enable_https": False,
            "message": "Nginx 配置文件中未解析到任何 listen 指令",
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": os.path.join(_root_dir, "bin", "nginx", "nginx.exe"),
            "config_dirty": config_dirty,
        }

    # 配置文件无法解析 → unknown + warning
    if not parsed and fallback:
        return {
            "running": False,
            "state": "unknown",
            "pid": None,
            "stale_pid": None,
            "port": None,
            "port_open": False,
            "ports": [],
            "enable_https": False,
            "message": "无法解析 Nginx 配置端口，请查看状态诊断",
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": os.path.join(_root_dir, "bin", "nginx", "nginx.exe"),
            "config_dirty": config_dirty,
        }

    # 读取 PID 文件（仅用于显示和 stale 标记，不决定状态）
    pid = _read_pid("nginx")
    from runtime.wnmp_process import is_process_running
    pid_alive = is_process_running(pid) if pid else False
    if pid_alive is None:
        pid_alive = True  # 权限不足，保守视为运行中
    stale_pid = pid if (pid and not pid_alive) else None

    nginx_exe = os.path.join(_root_dir, "bin", "nginx", "nginx.exe")
    all_ports = http_ports + https_ports

    # 使用批量查询：一次 netstat + 一次 WinAPI 批量获取路径
    port_expected_map = {p: nginx_exe for p in all_ports}
    from runtime.wnmp_process import get_listening_processes_for_ports
    batch_listeners = get_listening_processes_for_ports(
        port_expected_map, host="127.0.0.1", root_dir=_root_dir,
        timeout=_LISTENER_QUERY_TIMEOUT, logger=None
    )

    # 逐项检测端口开放状态
    ports = []
    for p in http_ports:
        open_flag = bool(batch_listeners.get(p, []))
        ports.append({"name": "HTTP", "port": p, "open": open_flag, "enabled": True, "ssl": False})
    for p in https_ports:
        open_flag = bool(batch_listeners.get(p, []))
        ports.append({"name": "HTTPS", "port": p, "open": open_flag, "enabled": True, "ssl": True})

    # 首个端口用于兼容旧字段
    first_port = None
    if http_ports:
        first_port = http_ports[0]
    elif https_ports:
        first_port = https_ports[0]

    enable_https = len(https_ports) > 0

    # 快速短路：所有 desired 端口都未开放
    any_open = any(pt["open"] for pt in ports)
    if not any_open:
        # 检查是否有本项目 nginx.exe 仍在旧端口（applied_ports 或 runtime_ports）上运行
        from runtime.wnmp_state import get_component_config_apply_state, is_component_config_dirty
        apply_state = get_component_config_apply_state(_root_dir, "nginx")
        applied_ports = apply_state.get("applied_ports", [])
        config_dirty = is_component_config_dirty(_root_dir, "nginx")

        runtime_ports = []
        runtime_listener_pid = None

        # 1) 先检查 applied_ports 是否仍由本项目 nginx 监听
        if applied_ports:
            for ap in applied_ports:
                ownership = _check_port_ownership(ap, "127.0.0.1", _root_dir, nginx_exe)
                if ownership["status"] == "running":
                    runtime_ports.append(ap)
                    if runtime_listener_pid is None:
                        runtime_listener_pid = ownership.get("pid")

        # 2) applied_ports 未命中时，通过进程扫描检测本项目 nginx.exe 实际监听端口
        if not runtime_ports:
            # 检查 stale PID 文件线索
            stale_pid_path = os.path.join(_root_dir, "runtime", "nginx.pid")
            pids_dir_path = os.path.join(_root_dir, "runtime", "pids", "nginx.pid")
            has_pid_clue = os.path.isfile(stale_pid_path) or os.path.isfile(pids_dir_path)

            if config_dirty or has_pid_clue:
                try:
                    from runtime.wnmp_nginx import detect_nginx_runtime_ports
                    detected_ports = detect_nginx_runtime_ports(_root_dir, nginx_exe, fast_mode=True, timeout=2)
                    for dp in detected_ports:
                        if dp not in all_ports:  # 排除 desired_ports 中已有的
                            ownership = _check_port_ownership(dp, "127.0.0.1", _root_dir, nginx_exe)
                            if ownership["status"] == "running":
                                runtime_ports.append(dp)
                                if runtime_listener_pid is None:
                                    runtime_listener_pid = ownership.get("pid")
                except Exception:
                    pass

        if runtime_ports:
            # 本项目 nginx 仍在旧端口运行，配置已修改未生效
            if runtime_listener_pid and runtime_listener_pid != pid:
                _write_pid_cache("nginx", runtime_listener_pid)
            # 计算 stale_ports：runtime_ports 中不属于 desired_ports 的端口
            stale_ports_list = [p for p in runtime_ports if p not in all_ports]
            # 统一 pending_reload 语义：服务运行中但配置未应用
            pr_msg = "运行中，配置已修改，待重载/重启生效"
            if stale_ports_list:
                pr_msg = "运行中，旧端口 {} 仍由本项目 Nginx 占用，需重载/重启生效".format(stale_ports_list)
            return {
                "running": True,
                "state": "pending_reload",
                "pid": runtime_listener_pid or pid,
                "stale_pid": stale_pid,
                "port": first_port,
                "port_open": True,
                "ports": ports,
                "enable_https": enable_https,
                "message": pr_msg,
                "parsed": parsed,
                "fallback": fallback,
                "listener_pid": runtime_listener_pid,
                "listener_path": None,
                "expected_path": nginx_exe,
                "config_dirty": True,
                "config_pending_reload": True,
                "desired_ports": all_ports,
                "runtime_ports": runtime_ports,
                "stale_ports": stale_ports_list,
                "applied_ports": applied_ports,
            }

        # 无 applied_ports 或旧端口也无本项目 nginx → 确认 stopped
        # stopped+dirty 时提示"配置已修改，启动后生效"，不显示 pending_reload
        stopped_msg = "已停止，配置已修改，启动 Nginx 后生效" if config_dirty else "已停止"
        return {
            "running": False,
            "state": "stopped",
            "pid": None,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": False,
            "ports": ports,
            "enable_https": enable_https,
            "message": stopped_msg,
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
        }

    # 逐端口判定归属
    running_count = 0
    stopped_count = 0
    external_count = 0
    unknown_count = 0
    listener_pid = None
    listener_path = None

    for p in all_ports:
        listeners = batch_listeners.get(p, [])
        if not listeners:
            stopped_count += 1
            continue
        # 查找是否有 is_expected=True 的 listener
        found_expected = False
        found_external = False
        found_unknown = False
        for ln in listeners:
            if ln.get("is_expected") is True:
                found_expected = True
                if listener_pid is None:
                    listener_pid = ln.get("pid")
                    listener_path = ln.get("path")
            elif ln.get("is_expected") is False:
                found_external = True
            elif ln.get("is_expected") is None:
                found_unknown = True

        if found_expected:
            running_count += 1
        elif found_external:
            external_count += 1
        elif found_unknown:
            unknown_count += 1
        else:
            stopped_count += 1

    # 状态判定（优先级：external > unknown > partial > running > stopped）
    total = len(all_ports)
    if running_count == total:
        # 所有端口均由本项目 nginx 精确监听
        if listener_pid and listener_pid != pid:
            _write_pid_cache("nginx", listener_pid)
        effective_pid = listener_pid or pid
        result = {
            "running": True,
            "state": "pending_reload" if config_dirty else "running",
            "pid": effective_pid,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": True,
            "ports": ports,
            "enable_https": enable_https,
            "message": "运行中，配置已修改，待重载/重启生效" if config_dirty else "正常运行",
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": listener_pid,
            "listener_path": listener_path,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
            "config_pending_reload": config_dirty,
            "decision": "confirmed_by_listener_path",
        }
        # config_dirty 时附加 desired_ports、runtime_ports 和 stale_ports
        if config_dirty:
            result["desired_ports"] = all_ports
            # 通过进程扫描检测本项目 nginx.exe 实际监听的所有端口（含旧端口）
            runtime_ports_list = []
            try:
                from runtime.wnmp_nginx import detect_nginx_runtime_ports
                runtime_ports_list = detect_nginx_runtime_ports(_root_dir, nginx_exe, fast_mode=True, timeout=2)
            except Exception:
                pass
            # 如果 detect 未命中，从 batch_listeners 中提取本项目 nginx 监听的端口
            if not runtime_ports_list:
                for p in all_ports:
                    listeners = batch_listeners.get(p, [])
                    for ln in listeners:
                        if ln.get("is_expected") is True:
                            if p not in runtime_ports_list:
                                runtime_ports_list.append(p)
            result["runtime_ports"] = runtime_ports_list
            # 计算 stale_ports：runtime_ports 中不属于 desired_ports 的端口
            stale_ports_list = [p for p in runtime_ports_list if p not in all_ports]
            result["stale_ports"] = stale_ports_list
            # 如果存在 stale_ports，更新 message 为更明确的提示
            if stale_ports_list:
                result["message"] = "运行中，旧端口 {} 仍由本项目 Nginx 占用，需重载/重启生效".format(stale_ports_list)
        return result
    elif external_count > 0 and running_count == 0:
        # 所有开放端口都是外部程序
        # config_dirty 时优先扫描本项目 nginx 是否仍在旧端口运行
        if config_dirty:
            runtime_ports_list = []
            try:
                from runtime.wnmp_nginx import detect_nginx_runtime_ports
                runtime_ports_list = detect_nginx_runtime_ports(_root_dir, nginx_exe, fast_mode=True, timeout=2)
            except Exception:
                pass
            if runtime_ports_list:
                # 旧 Nginx 仍在运行，不能误判为已停止
                stale_ports_list = [p for p in runtime_ports_list if p not in all_ports]
                # 只列出真正被外部占用的端口，而非所有 desired_ports
                ext_ports = [p for p in all_ports if any(
                    ln.get("is_expected") is False for ln in batch_listeners.get(p, [])
                )]
                if not ext_ports:
                    ext_ports = all_ports  # 兜底：无法精确判断时列出全部
                ext_msg = "新配置端口 {} 被外部程序占用，当前 Nginx 仍按旧配置运行，请释放端口后重载".format(ext_ports)
                return {
                    "running": True,
                    "state": "pending_reload",
                    "pid": pid,
                    "stale_pid": stale_pid,
                    "port": first_port,
                    "port_open": True,
                    "ports": ports,
                    "enable_https": enable_https,
                    "message": ext_msg,
                    "parsed": parsed,
                    "fallback": fallback,
                    "listener_pid": listener_pid,
                    "listener_path": listener_path,
                    "expected_path": nginx_exe,
                    "config_dirty": True,
                    "config_pending_reload": True,
                    "desired_ports": all_ports,
                    "runtime_ports": runtime_ports_list,
                    "stale_ports": stale_ports_list,
                }
        return {
            "running": False,
            "state": "external",
            "pid": None,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": True,
            "ports": ports,
            "enable_https": enable_https,
            "message": "端口被外部程序占用",
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
        }
    elif unknown_count > 0 and running_count == 0:
        # 所有开放端口都无法确认归属
        # 修复：尝试通过 recorded_pid + 进程名组合确认归属
        # Nginx 的 recorded_pid 来源：runtime/pids/nginx.pid 和 runtime/nginx.pid
        nginx_recorded_pids = []
        pids_dir_pid = _read_pid("nginx")
        if pids_dir_pid:
            nginx_recorded_pids.append(pids_dir_pid)
        # 也读取 runtime/nginx.pid（Nginx 自己的 pid 文件）
        nginx_own_pid_path = os.path.join(_root_dir, "runtime", "nginx.pid")
        if os.path.isfile(nginx_own_pid_path):
            try:
                with open(nginx_own_pid_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content.isdigit():
                        own_pid = int(content)
                        if own_pid not in nginx_recorded_pids:
                            nginx_recorded_pids.append(own_pid)
            except (IOError, ValueError):
                pass

        # 收集所有 unknown 端口的 listener_pid，尝试逐个确认
        nginx_confirmed = False
        nginx_confirmed_pid = None
        nginx_decision = None
        if nginx_recorded_pids:
            for p in all_ports:
                listeners = batch_listeners.get(p, [])
                for ln in listeners:
                    if ln.get("is_expected") is None:
                        ln_pid = ln.get("pid")
                        if ln_pid:
                            # Nginx listener_pid 不一定等于 master_pid，
                            # 但如果 listener_pid 本身就是 recorded_pid，可以直接确认
                            confirmation = confirm_running_by_recorded_pid(
                                "nginx", ln_pid, nginx_recorded_pids, ["nginx.exe"])
                            if confirmation:
                                nginx_confirmed = True
                                nginx_confirmed_pid = confirmation["confirmed_pid"]
                                nginx_decision = confirmation["decision"]
                                break
                if nginx_confirmed:
                    break

        if nginx_confirmed:
            # 通过 recorded_pid 确认归属
            if nginx_confirmed_pid and nginx_confirmed_pid != pid:
                _write_pid_cache("nginx", nginx_confirmed_pid)
            effective_pid = nginx_confirmed_pid or pid
            state_val = "pending_reload" if config_dirty else "running"
            msg_val = "运行中（通过记录 PID 确认）" if not config_dirty else "运行中，配置已修改，待重载/重启生效（通过记录 PID 确认）"
            return {
                "running": True,
                "state": state_val,
                "pid": effective_pid,
                "stale_pid": stale_pid,
                "port": first_port,
                "port_open": True,
                "ports": ports,
                "enable_https": enable_https,
                "message": msg_val,
                "parsed": parsed,
                "fallback": fallback,
                "listener_pid": nginx_confirmed_pid,
                "listener_path": None,
                "expected_path": nginx_exe,
                "config_dirty": config_dirty,
                "decision": nginx_decision,
            }

        # recorded_pid 也无法确认，尝试 config_dirty 时扫描旧端口
        if config_dirty:
            runtime_ports_list = []
            try:
                from runtime.wnmp_nginx import detect_nginx_runtime_ports
                runtime_ports_list = detect_nginx_runtime_ports(_root_dir, nginx_exe, fast_mode=True, timeout=2)
            except Exception:
                pass
            if runtime_ports_list:
                stale_ports_list = [p for p in runtime_ports_list if p not in all_ports]
                return {
                    "running": True,
                    "state": "pending_reload",
                    "pid": pid,
                    "stale_pid": stale_pid,
                    "port": first_port,
                    "port_open": True,
                    "ports": ports,
                    "enable_https": enable_https,
                    "message": "新配置端口归属未知，旧配置仍在运行，需重载/重启生效",
                    "parsed": parsed,
                    "fallback": fallback,
                    "listener_pid": listener_pid,
                    "listener_path": listener_path,
                    "expected_path": nginx_exe,
                    "config_dirty": True,
                    "config_pending_reload": True,
                    "desired_ports": all_ports,
                    "runtime_ports": runtime_ports_list,
                    "stale_ports": stale_ports_list,
                    "decision": "pending_reload_by_runtime_scan",
                }
        return {
            "running": False,
            "state": "unknown",
            "pid": None,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": True,
            "ports": ports,
            "enable_https": enable_https,
            "message": "端口已开放，但无法确认进程归属，请查看状态诊断",
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
            "decision": "unknown_unconfirmed",
        }
    elif running_count > 0 and (stopped_count > 0 or external_count > 0 or unknown_count > 0):
        # 部分端口 running，部分异常
        if listener_pid and listener_pid != pid:
            _write_pid_cache("nginx", listener_pid)
        effective_pid = listener_pid or pid

        # config_dirty + 本项目 nginx 仍在运行 → pending_reload 语义，不显示普通 partial 异常
        if config_dirty and running_count > 0:
            # 收集当前实际运行端口（含旧端口），用于前端区分配置端口和运行端口
            runtime_ports_list = []
            try:
                from runtime.wnmp_nginx import detect_nginx_runtime_ports
                detected = detect_nginx_runtime_ports(_root_dir, nginx_exe, fast_mode=True, timeout=2)
                runtime_ports_list = detected
            except Exception:
                pass
            # 如果 detect 未命中，从 batch_listeners 中提取本项目 nginx 监听的端口
            if not runtime_ports_list:
                for p in all_ports:
                    listeners = batch_listeners.get(p, [])
                    for ln in listeners:
                        if ln.get("is_expected") is True:
                            if p not in runtime_ports_list:
                                runtime_ports_list.append(p)
            # 构建明确 message：端口占用 + 旧配置仍运行
            partial_msg = "运行中，配置已修改，待重载/重启生效"
            stale_ports_list = [p for p in runtime_ports_list if p not in all_ports]
            if stale_ports_list:
                partial_msg = "运行中，旧端口 {} 仍由本项目 Nginx 占用，需重载/重启生效".format(stale_ports_list)
            elif external_count > 0:
                ext_ports = [p for p in all_ports if any(
                    ln.get("is_expected") is False for ln in batch_listeners.get(p, [])
                )]
                if ext_ports:
                    partial_msg = "新配置端口 {} 被外部程序占用，当前 Nginx 仍按旧配置运行，请释放端口后重载".format(ext_ports)
            return {
                "running": True,
                "state": "pending_reload",
                "pid": effective_pid,
                "stale_pid": stale_pid,
                "port": first_port,
                "port_open": True,
                "ports": ports,
                "enable_https": enable_https,
                "message": partial_msg,
                "parsed": parsed,
                "fallback": fallback,
                "listener_pid": listener_pid,
                "listener_path": listener_path,
                "expected_path": nginx_exe,
                "config_dirty": True,
                "config_pending_reload": True,
                "desired_ports": all_ports,
                "runtime_ports": runtime_ports_list,
                "stale_ports": stale_ports_list,
            }

        # 非 config_dirty 的普通 partial 异常
        details = []
        if stopped_count > 0:
            details.append("{}个端口未监听".format(stopped_count))
        if external_count > 0:
            details.append("{}个端口被外部占用".format(external_count))
        if unknown_count > 0:
            details.append("{}个端口归属未知".format(unknown_count))
        return {
            "running": True,
            "state": "partial",
            "pid": effective_pid,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": True,
            "ports": ports,
            "enable_https": enable_https,
            "message": "部分端口异常: " + "，".join(details),
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": listener_pid,
            "listener_path": listener_path,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
        }
    elif external_count > 0:
        # 有外部端口也有 running 端口（已在 partial 中处理）
        return {
            "running": False,
            "state": "external",
            "pid": None,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": True,
            "ports": ports,
            "enable_https": enable_https,
            "message": "端口被外部程序占用",
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
        }
    elif unknown_count > 0:
        # 兜底：尝试通过 recorded_pid 确认（理论上已被上方 unknown_count > 0 and running_count == 0 处理）
        return {
            "running": False,
            "state": "unknown",
            "pid": None,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": True,
            "ports": ports,
            "enable_https": enable_https,
            "message": "端口已开放，但无法确认进程归属，请查看状态诊断",
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
            "decision": "unknown_unconfirmed",
        }
    else:
        # 全部 stopped（不应该到这里，因为 any_open=True）
        stopped_msg = "已停止，配置已修改，启动 Nginx 后生效" if config_dirty else "已停止"
        return {
            "running": False,
            "state": "stopped",
            "pid": None,
            "stale_pid": stale_pid,
            "port": first_port,
            "port_open": False,
            "ports": ports,
            "enable_https": enable_https,
            "message": stopped_msg,
            "parsed": parsed,
            "fallback": fallback,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": nginx_exe,
            "config_dirty": config_dirty,
        }


def _get_php_status_fast(cfg):
    """PHP-CGI 状态检测：基于 php-cgi.ini 端口 + listener path（使用 is_expected 精确匹配）。

    状态判定逻辑：
    - 端口监听且 listener path 精确等于 php-cgi.exe → running
    - 端口未监听 → stopped
    - 端口被其它路径监听 → external
    - 端口已监听但无法确认 path → unknown
    PID 文件只显示，不决定状态。
    """
    from runtime.wnmp_config import get_effective_php_cgi_host_port, parse_php_cgi_config

    host, port = get_effective_php_cgi_host_port(_root_dir, cfg)
    php_cgi_exe = os.path.join(_root_dir, "bin", "php", "php-cgi.exe")

    # 检查 PHP 配置是否 dirty（保存后未重启）
    from runtime.wnmp_state import is_component_config_dirty
    config_dirty = is_component_config_dirty(_root_dir, "php")

    # 检查配置文件是否成功解析
    cgi_cfg = parse_php_cgi_config(_root_dir)
    config_parsed = cgi_cfg is not None

    # 配置解析失败时提示 warning
    config_warning = ""
    if not config_parsed:
        config_warning = "无法解析 php-cgi.ini 配置端口，已回退 runtime.ini 默认值"

    # 读取 PID 文件（仅用于显示和 stale 标记）
    pid = _read_pid("php")
    from runtime.wnmp_process import is_process_running
    pid_alive = is_process_running(pid) if pid else False
    if pid_alive is None:
        pid_alive = True
    stale_pid = pid if (pid and not pid_alive) else None

    # 快速短路：端口未开放 → stopped
    port_open = _is_port_open(host, port)
    if not port_open:
        return {
            "running": False, "state": "stopped", "pid": None,
            "stale_pid": stale_pid, "port": port, "port_open": False,
            "message": "已停止", "config_parsed": config_parsed,
            "config_warning": config_warning,
            "listener_pid": None, "listener_path": None,
            "expected_path": php_cgi_exe,
            "config_dirty": config_dirty,
            "config_pending_reload": config_dirty,
        }

    # 端口开放，查询 listener path 归属（传入 expected_path 做精确匹配）
    ownership = _check_port_ownership(port, host, _root_dir, php_cgi_exe)

    # 公共字段
    base = {
        "stale_pid": stale_pid, "port": port, "port_open": True,
        "config_parsed": config_parsed, "config_warning": config_warning,
        "expected_path": php_cgi_exe,
        "config_dirty": config_dirty,
        "config_pending_reload": config_dirty,
    }

    if ownership["status"] == "running":
        listener_pid = ownership["pid"]
        listener_path = ownership.get("path")
        # 注意：php-cgi.pid 永远表示 Master PID，不得把 listener/Worker PID 回写覆盖。
        # 启用 Master+Worker 模型后，listener PID 可能随 Worker 轮换变化，
        # 只要 9000 持续由本项目 php-cgi.exe 监听即视为 running。
        return dict(base, running=True, state="running",
                    pid=pid,  # 显示 Master PID（来自 PID 文件），无 PID 文件时为 None
                    message="正常运行",
                    listener_pid=listener_pid, listener_path=listener_path,
                    decision="confirmed_by_listener_path")
    elif ownership["status"] == "external":
        msg = ownership.get("message", "端口被外部程序占用")
        return dict(base, running=False, state="external",
                    pid=None, message=msg,
                    listener_pid=None, listener_path=ownership.get("path"),
                    decision="external")
    elif ownership["status"] == "unknown":
        # path=None 时，通过 Master+Worker 祖先关系确认归属。
        # 旧单进程模型要求 listener_pid == recorded_pid，Master+Worker 模型下
        # listener_pid 是 Worker，recorded_pid 是 Master，二者不相等，不能用 PID 相等判断。
        unknown_listener_pid = ownership.get("pid")
        confirmation = confirm_php_running_by_master_worker(unknown_listener_pid, pid)
        if confirmation:
            # 端口监听 + listener Worker 属于 recorded Master 进程树 → 确认 running
            # 注意：php-cgi.pid 永远表示 Master PID，不因状态查询回写 Worker/listener PID
            return dict(base, running=True, state="running",
                        pid=pid,  # 显示 Master PID（来自 PID 文件）
                        message="正常运行（通过 Master 进程树确认）",
                        listener_pid=unknown_listener_pid,
                        listener_path=ownership.get("path"),
                        decision=confirmation["decision"])
        # 无法确认归属，保持 unknown
        return dict(base, running=False, state="unknown",
                    pid=None,
                    message="端口已开放，但无法确认进程归属，请查看状态诊断",
                    listener_pid=unknown_listener_pid, listener_path=ownership.get("path"),
                    decision="unknown_unconfirmed")
    else:
        # stopped（不应该到这里，因为 port_open=True）
        return dict(base, running=False, state="unknown",
                    pid=None,
                    message="端口已开放，但无法确认进程归属，请查看状态诊断",
                    listener_pid=None, listener_path=None,
                    decision="unknown_unexpected")


def _get_mysql_status_fast(cfg):
    """MySQL 状态检测：基于 my.ini 端口 + listener path（使用 is_expected 精确匹配）。

    状态判定逻辑：
    - 端口监听且 listener path 精确等于 mysqld.exe → running
    - 端口未监听 → stopped
    - 端口被其它路径监听 → external
    - 端口已监听但无法确认 path → unknown
    不依赖 root-password.txt，不依赖 PID 文件作为状态真相。
    """
    from runtime.wnmp_config import get_effective_mysql_port, parse_mysql_port

    host = cfg.get("MYSQL_HOST", "127.0.0.1")
    port = get_effective_mysql_port(_root_dir, cfg)
    mysqld_exe = os.path.join(_root_dir, "bin", "mysql", "bin", "mysqld.exe")

    # 检查 MySQL 配置是否 dirty（保存后未重启）
    from runtime.wnmp_state import is_component_config_dirty
    config_dirty = is_component_config_dirty(_root_dir, "mysql")

    # 检查配置文件是否成功解析
    config_parsed = parse_mysql_port(_root_dir) is not None

    # 配置解析失败时提示 warning
    config_warning = ""
    if not config_parsed:
        config_warning = "无法解析 my.ini 配置端口，已回退 runtime.ini 默认值"

    # 读取 PID 文件（仅用于显示和 stale 标记）
    pid = _read_pid("mysql")
    from runtime.wnmp_process import is_process_running
    pid_alive = is_process_running(pid) if pid else False
    if pid_alive is None:
        pid_alive = True
    stale_pid = pid if (pid and not pid_alive) else None

    # 快速短路：端口未开放 → stopped
    port_open = _is_port_open(host, port)
    if not port_open:
        return {
            "running": False,
            "state": "stopped",
            "pid": None,
            "stale_pid": stale_pid,
            "port": port,
            "port_open": False,
            "message": "已停止",
            "config_parsed": config_parsed,
            "config_warning": config_warning,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": mysqld_exe,
            "config_dirty": config_dirty,
            "config_pending_reload": config_dirty,
        }

    # 端口开放，查询 listener path 归属（传入 expected_path 做精确匹配）
    ownership = _check_port_ownership(port, host, _root_dir, mysqld_exe)

    if ownership["status"] == "running":
        listener_pid = ownership["pid"]
        listener_path = ownership.get("path")
        # 回写 PID 缓存
        if listener_pid and listener_pid != pid:
            _write_pid_cache("mysql", listener_pid)
        return {
            "running": True,
            "state": "running",
            "pid": listener_pid or pid,
            "stale_pid": stale_pid,
            "port": port,
            "port_open": True,
            "message": "正常运行",
            "config_parsed": config_parsed,
            "config_warning": config_warning,
            "listener_pid": listener_pid,
            "listener_path": listener_path,
            "expected_path": mysqld_exe,
            "decision": "confirmed_by_listener_path",
            "config_dirty": config_dirty,
            "config_pending_reload": config_dirty,
        }
    elif ownership["status"] == "external":
        msg = ownership.get("message", "端口被外部程序占用")
        return {
            "running": False,
            "state": "external",
            "pid": None,
            "stale_pid": stale_pid,
            "port": port,
            "port_open": True,
            "message": msg,
            "config_parsed": config_parsed,
            "config_warning": config_warning,
            "listener_pid": None,
            "listener_path": ownership.get("path"),
            "expected_path": mysqld_exe,
            "decision": "external",
            "config_dirty": config_dirty,
            "config_pending_reload": False,
        }
    elif ownership["status"] == "unknown":
        # 修复：path=None 时，尝试通过 recorded_pid + 进程名组合确认归属
        unknown_listener_pid = ownership.get("pid")
        confirmation = confirm_running_by_recorded_pid(
            "mysql", unknown_listener_pid, [pid] if pid else [],
            ["mysqld.exe", "mysqld"])
        if confirmation:
            # 端口监听 + listener_pid == recorded_pid + 进程名匹配 → 确认 running
            confirmed_pid = confirmation["confirmed_pid"]
            # 回写 PID 缓存
            if confirmed_pid != pid:
                _write_pid_cache("mysql", confirmed_pid)
            return {
                "running": True,
                "state": "running",
                "pid": confirmed_pid,
                "stale_pid": stale_pid,
                "port": port,
                "port_open": True,
                "message": "正常运行（通过记录 PID 确认）",
                "config_parsed": config_parsed,
                "config_warning": config_warning,
                "listener_pid": unknown_listener_pid,
                "listener_path": ownership.get("path"),
                "expected_path": mysqld_exe,
                "decision": confirmation["decision"],
                "config_dirty": config_dirty,
                "config_pending_reload": config_dirty,
            }
        # 无法确认归属，保持 unknown
        return {
            "running": False,
            "state": "unknown",
            "pid": None,
            "stale_pid": stale_pid,
            "port": port,
            "port_open": True,
            "message": "端口已开放，但无法确认进程归属，请查看状态诊断",
            "config_parsed": config_parsed,
            "config_warning": config_warning,
            "listener_pid": unknown_listener_pid,
            "listener_path": ownership.get("path"),
            "expected_path": mysqld_exe,
            "decision": "unknown_unconfirmed",
            "config_dirty": config_dirty,
            "config_pending_reload": False,
        }
    else:
        return {
            "running": False,
            "state": "unknown",
            "pid": None,
            "stale_pid": stale_pid,
            "port": port,
            "port_open": True,
            "message": "端口已开放，但无法确认进程归属，请查看状态诊断",
            "config_parsed": config_parsed,
            "config_warning": config_warning,
            "listener_pid": None,
            "listener_path": None,
            "expected_path": mysqld_exe,
            "decision": "unknown_unexpected",
            "config_dirty": config_dirty,
            "config_pending_reload": False,
        }


def _write_pid_cache(component, pid):
    """回写 PID 缓存文件。状态检测发现 listener PID 与 PID 文件不一致时调用。

    PID 文件是缓存，不是状态真相。回写失败不影响状态显示。
    """
    try:
        from runtime.wnmp_process import write_pid_file
        pid_dir = os.path.join(_root_dir, "runtime", "pids")
        filename = _PID_FILENAME_MAP.get(component)
        if filename and pid:
            write_pid_file(pid_dir, filename, pid)
    except Exception:
        pass


def _clear_adopted_cache(component=None):
    """清理进程收养缓存（兼容旧接口）。"""
    pass


# 状态归属确认上次结果缓存，用于降噪（仅状态变化或 unknown 被兜底确认时输出日志）
_last_confirmation_log = {}


def confirm_running_by_recorded_pid(component, listener_pid, recorded_pid_candidates, expected_process_names):
    """当 listener_path=None（ownership=unknown）时，通过 recorded_pid + 进程名组合确认归属。

    确认条件必须同时满足：
      1. listener_pid 有值
      2. recorded_pid 存在且仍存活
      3. listener_pid 与某个 recorded_pid 匹配
      4. 进程名匹配 expected_process_names 中的任一项

    任何一个条件不满足则返回 None，表示无法确认，应保持 unknown/external。
    recorded_pid 不能单独证明服务运行，必须结合端口监听和 listener_pid。

    Args:
        component: 组件名 "nginx"/"php"/"mysql"
        listener_pid: 端口监听进程 PID（来自 netstat）
        recorded_pid_candidates: list[int]，PID 文件/state.json 中记录的 PID 候选列表
        expected_process_names: list[str]，期望的进程名列表，如 ["php-cgi.exe"]

    Returns:
        dict or None: 确认成功返回 {"confirmed_pid": int, "process_name": str, "decision": "confirmed_by_recorded_pid"}，
                      确认失败返回 None
    """
    from runtime.wnmp_process import is_process_running, get_process_name_fast

    # 条件 1：listener_pid 必须有值
    if not listener_pid:
        return None

    # 条件 2+3：listener_pid 必须与某个 recorded_pid 匹配
    matched_recorded_pid = None
    for rpid in recorded_pid_candidates:
        if rpid and listener_pid == rpid:
            matched_recorded_pid = rpid
            break

    if matched_recorded_pid is None:
        return None

    # 条件 2 补充：recorded_pid 进程必须仍存活
    alive = is_process_running(matched_recorded_pid)
    if alive is False:
        return None  # 进程已退出，pid 文件残留
    # alive is None（权限不足）时保守视为存活，继续检查进程名

    # 条件 4：进程名必须匹配（使用 fast 版本，仅 WinAPI+tasklist，不触发 PowerShell）
    proc_name = get_process_name_fast(listener_pid, timeout=1)
    if not proc_name:
        return None  # 无法获取进程名，不能确认

    proc_name_lower = proc_name.lower()
    expected_lower = [n.lower() for n in expected_process_names]
    if proc_name_lower not in expected_lower:
        return None  # 进程名不匹配

    # 所有条件满足，确认归属
    result = {
        "confirmed_pid": matched_recorded_pid,
        "process_name": proc_name,
        "decision": "confirmed_by_recorded_pid",
    }

    # 降噪日志：仅状态变化或 unknown 被兜底确认时输出
    log_key = component
    last_log = _last_confirmation_log.get(log_key)
    current_log = "decision=confirmed_by_recorded_pid listener_pid={} recorded_pid={} proc_name={}".format(
        listener_pid, matched_recorded_pid, proc_name)
    if last_log != current_log:
        _status_log("component={} {} path_readable=false source=autostart_or_recorded_pid".format(
            component, current_log))
        _last_confirmation_log[log_key] = current_log

    return result


def confirm_php_running_by_master_worker(listener_pid, master_pid):
    """PHP Master+Worker 模型下，通过祖先关系确认 listener Worker 属于 recorded Master。

    适用场景：listener path=None（SYSTEM/开机计划任务启动导致 path 不可读），
    无法通过 executable path 精确匹配 php-cgi.exe 时。

    确认条件（必须全部满足）：
      1. listener_pid 有值
      2. master_pid 有值且仍存活（php-cgi.pid 记录的 Master）
      3. listener_pid 是 php-cgi.exe（进程名匹配，仅作辅助）
      4. listener_pid 的父进程/祖先进程链能回溯到 master_pid（属于同一进程树）

    不依赖 listener_pid == master_pid（旧单进程模型的判断），
    也不回写 php-cgi.pid（Worker/listener PID 永不覆盖 Master PID）。

    Args:
        listener_pid: 端口监听进程 PID（来自 netstat，可能是某个 Worker）
        master_pid: php-cgi.pid 记录的 Master PID

    Returns:
        dict or None: 确认成功返回 {"decision": "confirmed_by_master_worker"}，失败返回 None
    """
    from runtime.wnmp_process import is_process_running, get_process_name_fast, is_process_in_ancestry

    # 条件 1 + 2：listener_pid / master_pid 必须有值
    if not listener_pid or not master_pid:
        return None

    # 条件 2 补充：Master 必须仍存活
    master_alive = is_process_running(master_pid)
    if master_alive is False:
        return None  # Master 已退出，PID 文件残留
    # master_alive is None（权限不足）时保守视为存活，继续检查

    # 条件 3：listener 进程名必须是 php-cgi.exe（辅助判断，不单独作为归属依据）
    proc_name = get_process_name_fast(listener_pid, timeout=1)
    if not proc_name:
        return None  # 无法获取进程名，不能确认
    if proc_name.lower() != "php-cgi.exe":
        return None  # 进程名不匹配

    # 条件 4：listener Worker 必须属于 recorded Master 的进程树（祖先关系）
    if not is_process_in_ancestry(listener_pid, master_pid, max_depth=16, timeout=2):
        return None

    # 降噪日志：仅状态变化时输出
    log_key = "php_master_worker"
    last_log = _last_confirmation_log.get(log_key)
    current_log = "decision=confirmed_by_master_worker listener_pid={} master_pid={} proc_name={}".format(
        listener_pid, master_pid, proc_name)
    if last_log != current_log:
        _status_log("component=php {} path_readable=false source=master_worker_ancestry".format(current_log))
        _last_confirmation_log[log_key] = current_log

    return {"decision": "confirmed_by_master_worker"}


# 组件状态探测函数映射，供 get_component_status 使用
_COMPONENT_PROBE_MAP = {
    "nginx": _get_nginx_status_fast,
    "php": _get_php_status_fast,
    "mysql": _get_mysql_status_fast,
}

def get_component_status(component, cfg=None):
    """获取单个组件的状态，互相隔离。

    Args:
        component: 组件名 "nginx" | "php" | "mysql"
        cfg: 可选配置，不传则自动加载
    Returns:
        dict: 组件状态字典，异常时返回 unknown
    """
    if component not in _COMPONENT_PROBE_MAP:
        return {"running": False, "state": "unknown", "message": "未知组件: " + component}

    if cfg is None:
        cfg = _load_config()

    try:
        return _COMPONENT_PROBE_MAP[component](cfg)
    except Exception:
        return {"running": False, "state": "unknown", "message": "检测异常"}


# 初始化阶段对应的前端展示文案
_INIT_PHASE_MESSAGES = {
    "preparing_config": "正在生成配置文件",
    "mysql_secure_init": "正在初始化 MySQL",
    "starting_php_cgi": "正在启动 PHP-CGI",
    "starting_nginx": "正在启动 Nginx",
    "verifying_services": "正在确认服务端口",
    "failed": "初始化失败",
}


_START_PHASE_MESSAGES = {
    "starting_mysql": "正在启动 MySQL",
    "starting_php_cgi": "正在启动 PHP-CGI",
    "starting_nginx": "正在启动 Nginx",
    "verifying_services": "正在确认服务端口",
    "failed": "启动失败",
}


def get_full_status():
    """Return full status snapshot dict.

    三组件互相隔离 try/except，单组件异常只影响自身返回 unknown。

    状态机语义：
    - INIT_PHASE: 仅用于首次初始化流程（INITIALIZED=false 时）
    - START_PHASE: 用于已初始化环境的普通启动流程
    - INITIALIZED=true 时，INIT_PHASE 残留不影响状态判断
    """
    root_dir = _root_dir
    cfg = _load_config()
    state = _load_state()

    initialized = bool(state.get("INITIALIZED", False))
    init_phase = state.get("INIT_PHASE")
    start_phase = state.get("START_PHASE")

    # INITIALIZED=true 时，归一化残留 INIT_PHASE
    if initialized and init_phase not in ("completed", None):
        # 残留的 INIT_PHASE 不影响已初始化状态，忽略
        init_phase = "completed"

    initializing = not initialized and init_phase is not None and init_phase not in ("completed", "failed")
    init_failed = not initialized and init_phase == "failed"

    # 普通启动状态（仅已初始化环境）
    starting = initialized and start_phase is not None and start_phase not in ("completed", "failed")
    start_failed = initialized and start_phase == "failed"

    # 初始化失败：显示失败状态，而非普通未初始化
    if init_failed:
        return {
            "initialized": False,
            "initializing": False,
            "init_phase": init_phase,
            "starting": False,
            "start_phase": None,
            "overall": "failed",
            "message": state.get("INIT_ERROR") or state.get("LAST_INIT_ERROR") or _INIT_PHASE_MESSAGES.get("failed", "初始化失败"),
        }

    if not initialized and not initializing:
        return {
            "initialized": False,
            "initializing": False,
            "init_phase": init_phase,
            "starting": False,
            "start_phase": None,
            "overall": "uninitialized",
            "message": "环境尚未初始化",
        }

    # 正在初始化中但尚未完成（仅 INITIALIZED=false 时）
    if initializing:
        return {
            "initialized": False,
            "initializing": True,
            "init_phase": init_phase,
            "starting": False,
            "start_phase": None,
            "overall": "initializing",
            "message": _INIT_PHASE_MESSAGES.get(init_phase, "正在初始化环境"),
        }

    # 已初始化环境：三组件独立探测（采集组件级耗时）
    import time as _time
    _t0 = _time.time()
    nginx_st = get_component_status("nginx", cfg)
    _nginx_ms = int((_time.time() - _t0) * 1000)
    _t1 = _time.time()
    php_st = get_component_status("php", cfg)
    _php_ms = int((_time.time() - _t1) * 1000)
    _t2 = _time.time()
    mysql_st = get_component_status("mysql", cfg)
    _mysql_ms = int((_time.time() - _t2) * 1000)

    # Compute overall
    components = [nginx_st, php_st, mysql_st]
    running_count = sum(1 for c in components if c.get("state") in ("running", "pending_reload"))
    stopped_count = sum(1 for c in components if c.get("state") == "stopped")
    external_count = sum(1 for c in components if c.get("state") == "external")
    error_count = sum(1 for c in components if c.get("state") == "error")
    unknown_count = sum(1 for c in components if c.get("state") == "unknown")
    pending_reload_count = sum(1 for c in components if c.get("state") == "pending_reload")

    if start_failed:
        overall = "error"
    elif starting:
        overall = "starting"
    elif error_count > 0:
        overall = "error"
    elif external_count > 0:
        overall = "external"
    elif unknown_count > 0:
        overall = "unknown"
    elif running_count == 3:
        # 全部运行中（含 pending_reload）
        overall = "pending_reload" if pending_reload_count > 0 else "running"
    elif stopped_count == 3:
        overall = "stopped"
    else:
        overall = "partial"

    # 端口解析 warning
    port_warnings = []
    try:
        from runtime.wnmp_config import get_effective_nginx_listens, parse_mysql_port, parse_php_cgi_config
        eff = get_effective_nginx_listens(root_dir, cfg)
        if eff["fallback"]:
            port_warnings.append(eff["warning"])
        if parse_mysql_port(root_dir) is None:
            port_warnings.append("MySQL 端口无法从 my.ini 解析，已回退 runtime.ini 默认值")
        if parse_php_cgi_config(root_dir) is None:
            port_warnings.append("PHP-CGI 端口无法从 php-cgi.ini 解析，已回退 runtime.ini 默认值")
    except Exception:
        pass

    result = {
        "initialized": True,
        "initializing": False,
        "init_phase": init_phase,
        "starting": starting,
        "start_phase": start_phase,
        "nginx": nginx_st,
        "php": php_st,
        "mysql": mysql_st,
        "overall": overall,
    }
    if starting:
        result["message"] = _START_PHASE_MESSAGES.get(start_phase, "正在启动环境")
    elif start_failed:
        result["message"] = _START_PHASE_MESSAGES.get("failed", "启动失败")
    if port_warnings:
        result["port_warnings"] = port_warnings
    # 组件级耗时（用于慢查询日志定位，不暴露给前端）
    result["_timing_ms"] = {
        "nginx": _nginx_ms,
        "php": _php_ms,
        "mysql": _mysql_ms,
    }
    return result


def get_full_status_debug():
    """Return detailed debug status for /api/status/debug.

    包含完整诊断信息：pid_file、pid_alive、port_open、listener 列表、
    进程 owner、owned_process_pids、scan_ms 等。
    仅用于手动排错，前端每秒轮询不得调用。
    """
    root_dir = _root_dir
    cfg = _load_config()
    state = _load_state()

    from runtime.wnmp_config import (
        get_effective_nginx_listens,
        get_effective_php_cgi_host_port, get_effective_mysql_port
    )

    initialized = bool(state.get("INITIALIZED", False))

    debug_info = {
        "initialized": initialized,
        "root_dir": root_dir,
    }

    if not initialized:
        debug_info["overall"] = "uninitialized"
        return debug_info

    def _get_listeners_for_port(port, host, root_dir, expected_path=None):
        """查询指定端口的所有 listener，返回结构化列表（包含 is_expected/is_in_root/path_query_error）。"""
        listeners = []
        try:
            from runtime.wnmp_process import get_listening_processes, is_system_process
            ln_list = get_listening_processes(port, host=host, root_dir=root_dir, timeout=5,
                                              expected_path=expected_path, fast_mode=False,
                                              include_owner=True)
            for ln in ln_list:
                ln_pid = ln.get("pid")
                ln_owner = "unknown"
                if ln_pid:
                    try:
                        ln_is_sys = is_system_process(ln_pid, timeout=3)
                        ln_owner = "SYSTEM" if ln_is_sys else "user"
                    except Exception:
                        pass
                listeners.append({
                    "pid": ln_pid,
                    "local_address": ln.get("local_address"),
                    "path": ln.get("path"),
                    "owner": ln.get("owner") or ln_owner,
                    "is_ours": ln.get("is_ours"),
                    "is_expected": ln.get("is_expected"),
                    "is_in_root": ln.get("is_in_root"),
                    "path_query_error": ln.get("path_query_error"),
                })
        except Exception as e:
            listeners = [{"error": str(e)}]
        return listeners

    def _get_owned_processes(component, root_dir):
        """查询本项目进程（完整路径扫描）。"""
        owned_pids = []
        owned_paths = []
        try:
            from runtime.wnmp_process import find_processes_by_executable_path
            rel_path = _EXECUTABLE_PATH_MAP.get(component)
            if rel_path:
                expected_path = os.path.join(root_dir, rel_path)
                owned_pids = find_processes_by_executable_path(expected_path, timeout=5, fast_mode=False)
                if owned_pids:
                    owned_paths = [expected_path] * len(owned_pids)
        except Exception:
            pass
        return owned_pids, owned_paths

    def _build_debug_entry(component, status_func, pid_filename, host, port, expected_path=None):
        """构建单个组件的 debug 信息（PHP/MySQL 用）。"""
        t0 = time.time()
        try:
            st = status_func(cfg)
            ms = int((time.time() - t0) * 1000)
            pid_file = os.path.join(root_dir, "runtime", "pids", pid_filename)
            pid_file_exists = os.path.isfile(pid_file)
            pid_file_pid = _read_pid(component)
            listeners = _get_listeners_for_port(port, host, root_dir, expected_path=expected_path)
            owned_pids, owned_paths = _get_owned_processes(component, root_dir)

            return {
                **st,
                "pid_file": pid_file,
                "pid_file_exists": pid_file_exists,
                "pid_file_pid": pid_file_pid,
                "stale_pid": st.get("stale_pid"),
                "port_open": st.get("port_open"),
                "listeners": listeners,
                "owned_process_pids": owned_pids,
                "owned_process_paths": owned_paths,
                "expected_path": expected_path,
                "listener_pid": st.get("listener_pid"),
                "listener_path": st.get("listener_path"),
                "scan_ms": ms,
            }
        except Exception as e:
            return {"state": "error", "debug_error": str(e), "scan_ms": int((time.time() - t0) * 1000)}

    def _build_nginx_debug_entry():
        """构建 Nginx debug 信息，基于实际 listen 列表构建 ports_debug。"""
        t0 = time.time()
        try:
            st = _get_nginx_status_fast(cfg)
            ms = int((time.time() - t0) * 1000)
            pid_file = os.path.join(root_dir, "runtime", "pids", "nginx.pid")
            pid_file_exists = os.path.isfile(pid_file)
            pid_file_pid = _read_pid("nginx")
            nginx_exe = os.path.join(root_dir, "bin", "nginx", "nginx.exe")

            # 基于 get_effective_nginx_listens 构建 ports_debug
            from runtime.wnmp_config import get_effective_nginx_listens
            eff = get_effective_nginx_listens(root_dir, cfg)

            ports_debug = []
            for p in eff["http"]:
                listeners = _get_listeners_for_port(p, "127.0.0.1", root_dir, expected_path=nginx_exe)
                ports_debug.append({
                    "name": "HTTP",
                    "port": p,
                    "ssl": False,
                    "enabled": True,
                    "port_open": _is_port_open("127.0.0.1", p),
                    "listeners": listeners,
                })
            for p in eff["https"]:
                listeners = _get_listeners_for_port(p, "127.0.0.1", root_dir, expected_path=nginx_exe)
                ports_debug.append({
                    "name": "HTTPS",
                    "port": p,
                    "ssl": True,
                    "enabled": True,
                    "port_open": _is_port_open("127.0.0.1", p),
                    "listeners": listeners,
                })

            # fallback 标记
            if eff["fallback"]:
                ports_debug.append({
                    "name": "FALLBACK",
                    "port": None,
                    "ssl": None,
                    "enabled": False,
                    "port_open": False,
                    "listeners": [],
                    "warning": eff["warning"],
                })

            owned_pids, owned_paths = _get_owned_processes("nginx", root_dir)

            # 兼容：默认返回第一个端口的 listeners
            first_listeners = ports_debug[0]["listeners"] if ports_debug else []

            return {
                **st,
                "pid_file": pid_file,
                "pid_file_exists": pid_file_exists,
                "pid_file_pid": pid_file_pid,
                "stale_pid": st.get("stale_pid"),
                "port_open": st.get("port_open"),
                "listeners": first_listeners,
                "ports_debug": ports_debug,
                "owned_process_pids": owned_pids,
                "owned_process_paths": owned_paths,
                "expected_path": nginx_exe,
                "listener_pid": st.get("listener_pid"),
                "listener_path": st.get("listener_path"),
                "scan_ms": ms,
            }
        except Exception as e:
            return {"state": "error", "debug_error": str(e), "scan_ms": int((time.time() - t0) * 1000)}

    php_host, php_port = get_effective_php_cgi_host_port(root_dir, cfg)
    mysql_host = cfg.get("MYSQL_HOST", "127.0.0.1")
    mysql_port = get_effective_mysql_port(root_dir, cfg)

    # 端口解析来源 warning
    from runtime.wnmp_config import get_effective_nginx_listens, parse_mysql_port, parse_php_cgi_config
    port_warnings = []
    eff = get_effective_nginx_listens(root_dir, cfg)
    if eff["fallback"]:
        port_warnings.append(eff["warning"])
    if parse_mysql_port(root_dir) is None:
        port_warnings.append("MySQL 端口无法从 my.ini 解析，已回退 runtime.ini 默认值")
    if parse_php_cgi_config(root_dir) is None:
        port_warnings.append("PHP-CGI 端口无法从 php-cgi.ini 解析，已回退 runtime.ini 默认值")
    if port_warnings:
        debug_info["port_warnings"] = port_warnings

    debug_info["nginx"] = _build_nginx_debug_entry()
    php_cgi_exe = os.path.join(root_dir, "bin", "php", "php-cgi.exe")
    mysqld_exe = os.path.join(root_dir, "bin", "mysql", "bin", "mysqld.exe")
    debug_info["php"] = _build_debug_entry("php", _get_php_status_fast, "php-cgi.pid", php_host, php_port,
                                            expected_path=php_cgi_exe)
    debug_info["mysql"] = _build_debug_entry("mysql", _get_mysql_status_fast, "mysqld.pid", mysql_host, mysql_port,
                                              expected_path=mysqld_exe)

    # Overall（与 get_full_status 保持一致：pending_reload 视为运行中）
    components = [debug_info.get("nginx", {}), debug_info.get("php", {}), debug_info.get("mysql", {})]
    running_count = sum(1 for c in components if c.get("state") == "running")
    pending_reload_count = sum(1 for c in components if c.get("state") == "pending_reload")
    stopped_count = sum(1 for c in components if c.get("state") == "stopped")
    external_count = sum(1 for c in components if c.get("state") == "external")
    error_count = sum(1 for c in components if c.get("state") == "error")
    unknown_count = sum(1 for c in components if c.get("state") == "unknown")
    # running + pending_reload 都算"运行中"
    active_count = running_count + pending_reload_count

    if error_count > 0:
        overall = "error"
    elif external_count > 0:
        overall = "external"
    elif unknown_count > 0:
        overall = "unknown"
    elif active_count == 3:
        overall = "pending_reload" if pending_reload_count > 0 else "running"
    elif stopped_count == 3:
        overall = "stopped"
    else:
        overall = "partial"

    debug_info["overall"] = overall
    return debug_info
