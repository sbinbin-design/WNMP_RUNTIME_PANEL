# -*- coding: utf-8 -*-
"""
WNMP Nginx Module - Nginx 启停控制
使用 Python 标准库实现，不依赖第三方包
"""
import os
import time
import subprocess
from runtime.wnmp_process import (
    write_pid_file, read_pid_file, remove_pid_file,
    is_process_running, kill_process, start_process,
    wait_for_port_open, wait_for_port_close, is_port_listening,
    find_processes_by_path, kill_processes_by_path,
    cleanup_residual_processes,
    terminate_pids, wait_ports_closed, find_port_listener_path,
    get_listening_processes,
    get_process_path, _normalize_path, is_system_process, is_current_admin,
    get_process_image_path, get_process_name, get_pid_detail
)
from runtime.wnmp_path import to_forward_slash
from runtime.wnmp_component_paths import get_nginx_conf_path, get_nginx_vhosts_dir


def test_nginx_config(root_dir, cfg, logger):
    """执行 nginx -t 测试配置。增加 timeout=15 和 cwd=root_dir，防止无限等待。"""
    nginx_exe = os.path.join(root_dir, "bin", "nginx", "nginx.exe")
    # 路径收敛：通过统一路径模块获取 nginx 配置路径
    nginx_conf = get_nginx_conf_path(root_dir)
    cmd = [nginx_exe, "-p", root_dir, "-t", "-c", nginx_conf]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=root_dir,
            timeout=15
        )
        if result.returncode == 0:
            return True, result.stderr or result.stdout
        else:
            error_msg = result.stderr or result.stdout
            if "vhosts" in error_msg.lower():
                import re
                match = re.search(r'vhosts[\\/][^"\s:]+', error_msg)
                if match:
                    bad_file = match.group(0).replace("\\", "/")
                    # P2：vhosts 目录已切换到 bin/nginx/conf/vhosts/
                    vhosts_display = os.path.relpath(get_nginx_vhosts_dir(root_dir), root_dir).replace(os.sep, "/")
                    error_msg = "检测到虚拟主机配置错误！\n"
                    error_msg += "请检查: {}/{}\n".format(vhosts_display, os.path.basename(bad_file))
                    error_msg += "Nginx 错误详情:\n" + (result.stderr or result.stdout)
            # 返回原始 nginx -t 错误摘要，不含前缀，由调用方统一加前缀
            return False, error_msg
    except subprocess.TimeoutExpired:
        return False, "Nginx 配置校验超时"
    except Exception as e:
        return False, str(e)


def start_nginx(root_dir, cfg, logger):
    """启动 Nginx。基于 listener path 确认启动成功，path=None 时使用组合确认。

    启动前预检端口：如果启用端口启动前已被未知外部程序占用，直接阻断。
    启动后确认逻辑：
      1. listener path 精确匹配 nginx.exe → confirmed_by_path
      2. path=None 但 launched_pid 存活 + 端口已监听 + listener_pid==launched_pid
         或进程名匹配 nginx.exe → confirmed_by_combination

    幂等规则：只有 config_dirty=false 且 desired_ports 已由本项目 nginx.exe 监听且无 stale_ports 时，
    才返回幂等成功并 mark applied；config_dirty=true 时即使端口已监听也不得清除 dirty，
    返回 False + need_action message。
    防重复：如果本项目 nginx.exe 正在按旧配置运行但 desired_ports 未监听，
    不启动第二个 Nginx，返回提示需 reload/restart。
    启动成功后记录 applied config hash 和端口。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn
    from runtime.wnmp_config import get_effective_nginx_listens
    from runtime.wnmp_process import wait_for_port_listener, check_listener_ownership
    from runtime.wnmp_state import mark_component_config_applied, compute_component_config_hash

    nginx_exe = os.path.join(root_dir, "bin", "nginx", "nginx.exe")
    # 路径收敛：通过统一路径模块获取 nginx 配置路径
    nginx_conf = get_nginx_conf_path(root_dir)
    pid_dir = os.path.join(root_dir, "runtime", "pids")

    # 获取实际 listen 列表（desired ports）
    eff = get_effective_nginx_listens(root_dir, cfg)
    all_nginx_ports = eff["http"] + eff["https"]

    # 配置解析失败处理
    if not all_nginx_ports:
        if eff["parsed"]:
            log_error(logger, "Nginx 配置文件中未解析到任何 listen 指令")
            return False, "Nginx 配置文件中未解析到任何 listen 指令"
        else:
            log_error(logger, "无法解析 Nginx 配置端口: " + eff.get("warning", ""))
            return False, "无法解析 Nginx 配置端口"

    log_info(logger, "Nginx 配置解析: http_ports={} https_ports={}".format(eff["http"], eff["https"]))

    # 幂等检查：desired_ports 已经由本项目 nginx.exe 监听？
    all_desired_confirmed = True
    confirmed_pid = None
    for p in all_nginx_ports:
        ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                              root_dir=root_dir, timeout=2, logger=logger)
        if ownership["status"] == "running":
            if confirmed_pid is None:
                confirmed_pid = ownership["pid"]
        else:
            all_desired_confirmed = False

    if all_desired_confirmed and confirmed_pid is not None:
        log_info(logger, "Nginx already running with desired config, PID={}".format(confirmed_pid))
        # 幂等分支：必须检查 config_dirty，端口正常不等于配置已应用
        from runtime.wnmp_state import get_component_config_apply_state, is_component_config_dirty
        config_dirty = is_component_config_dirty(root_dir, "nginx")
        if config_dirty:
            # 配置已修改但尚未应用，即使端口未变也不能 mark applied
            log_info(logger, "Nginx desired ports confirmed but config_dirty=true, need reload/restart")
            write_pid_file(pid_dir, "nginx.pid", confirmed_pid)
            return False, "Nginx 正在运行，但配置已修改尚未应用，请执行重载或重启 Nginx 生效"
        # config_dirty=false，仍需检查旧端口残留
        apply_state = get_component_config_apply_state(root_dir, "nginx")
        old_applied_ports = apply_state.get("applied_ports", [])
        old_runtime_ports = detect_nginx_runtime_ports(root_dir, nginx_exe, logger, fast_mode=False)
        old_ports = list(dict.fromkeys(old_applied_ports + old_runtime_ports))
        stale_ports = [p for p in old_ports if p not in all_nginx_ports]
        if stale_ports:
            # 检查旧端口是否仍由本项目 nginx 监听
            still_held = []
            for p in stale_ports:
                ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                      root_dir=root_dir, timeout=2, logger=logger)
                if ownership["status"] == "running":
                    still_held.append(p)
            if still_held:
                log_warn(logger, "Nginx desired ports confirmed but stale ports {} still held by project nginx".format(still_held))
                write_pid_file(pid_dir, "nginx.pid", confirmed_pid)
                return False, "新端口已运行但旧端口 {} 仍被本项目 Nginx 占用，请先停止旧进程或执行完整重启".format(still_held)
        write_pid_file(pid_dir, "nginx.pid", confirmed_pid)
        # 刷新 applied config
        mark_component_config_applied(root_dir, "nginx", ports=all_nginx_ports)
        return True, confirmed_pid

    # 防重复：检查是否有本项目 nginx.exe 正在按旧配置运行
    # 查找 applied_ports 或按路径查找 nginx.exe 进程
    from runtime.wnmp_state import get_component_config_apply_state
    apply_state = get_component_config_apply_state(root_dir, "nginx")
    applied_ports = apply_state.get("applied_ports", [])

    # 检查 applied_ports 是否仍由本项目 nginx 监听
    if applied_ports:
        for p in applied_ports:
            ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                  root_dir=root_dir, timeout=2, logger=logger)
            if ownership["status"] == "running":
                log_warn(logger, "Nginx is running with old config on ports {}, desired ports {} not all listening".format(
                    applied_ports, all_nginx_ports))
                return False, "当前 Nginx 正在旧配置下运行，请执行重载或重启生效"

    # applied_ports 缺失时，通过运行时端口检测补充
    if not applied_ports:
        runtime_ports = _detect_nginx_runtime_ports(root_dir, nginx_exe, logger, fast_mode=False)
        if runtime_ports:
            for p in runtime_ports:
                ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                      root_dir=root_dir, timeout=2, logger=logger)
                if ownership["status"] == "running":
                    log_warn(logger, "Nginx is running on runtime-detected ports {}, desired ports {} not all listening".format(
                        runtime_ports, all_nginx_ports))
                    return False, "当前 Nginx 正在旧配置下运行，请执行重载或重启生效"

    # 按路径查找是否有本项目 nginx.exe 进程
    tool_pids = find_processes_by_path(root_dir, "nginx.exe", logger)
    if tool_pids:
        log_warn(logger, "Found {} project nginx.exe processes (PIDs: {}) but desired ports not listening".format(
            len(tool_pids), tool_pids))
        return False, "当前 Nginx 正在旧配置下运行，请执行重载或重启生效"

    # 启动前清理旧 PID 文件，避免 stale PID 干扰
    nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
    if os.path.isfile(nginx_pid_path):
        try:
            os.remove(nginx_pid_path)
        except OSError:
            pass
    remove_pid_file(pid_dir, "nginx.pid")

    # 启动前预检：检查启用端口是否已被外部/未知程序占用，同时记录启动前端口状态
    # precheck_ports_open 必须在 start_process 之前记录，否则启动后端口已监听会误判
    precheck_ports_open = {}
    for p in all_nginx_ports:
        port_open = is_port_listening("127.0.0.1", p)
        precheck_ports_open[p] = port_open
        if port_open:
            precheck_ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                          root_dir=root_dir, timeout=2, logger=logger)
            log_info(logger, "  Precheck port {}: open={} status={} pid={} path={}".format(
                p, port_open, precheck_ownership["status"],
                precheck_ownership.get("pid"), precheck_ownership.get("path")))
            if precheck_ownership["status"] == "external":
                log_error(logger, "Port {} is preoccupied by external process: path={}".format(
                    p, precheck_ownership.get("path")))
                return False, "port_preoccupied: 端口 {} 已被外部程序{}占用".format(
                    p, " " + precheck_ownership.get("path") if precheck_ownership.get("path") else "")
            elif precheck_ownership["status"] == "unknown":
                log_error(logger, "Port {} is preoccupied by unknown process".format(p))
                return False, "port_preoccupied: 端口 {} 已被未知进程占用，为避免误操作已阻断启动".format(p)
        else:
            log_info(logger, "  Precheck port {}: open=false".format(p))
    log_info(logger, "  Precheck summary: precheck_ports_open={}".format(precheck_ports_open))

    log_info(logger, "Testing Nginx configuration...")
    ok, output = test_nginx_config(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "Nginx configuration test failed:")
        for line in output.strip().split("\n"):
            log_error(logger, "  " + line)
        # 把 nginx -t 错误摘要拼到返回 message，保留前 1200 字符
        err_summary = output.strip()[:1200] if output else "未知错误"
        return False, "Nginx 配置校验失败: " + err_summary

    log_info(logger, "Nginx configuration test passed")

    cmd = [nginx_exe, "-p", root_dir, "-c", nginx_conf]
    log_info(logger, "Starting Nginx...")
    log_info(logger, "  Command: {}".format(" ".join(cmd)))
    proc = start_process(cmd, cwd=root_dir, logger=logger)
    if proc is None:
        log_error(logger, "Failed to start Nginx process")
        return False, "Nginx 进程启动失败"

    launched_pid = proc.pid

    # 等待所有配置端口被本项目 nginx 监听
    log_info(logger, "Waiting for Nginx listen ports to be confirmed...")
    start_time = time.time()
    timeout = 30  # 总等待超时

    confirmed_pid = None
    decision = None
    while time.time() - start_time < timeout:
        all_confirmed = True
        has_unknown = False
        unknown_pids = {}  # port -> ownership dict
        for p in all_nginx_ports:
            ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                  root_dir=root_dir, timeout=2, logger=logger)
            if ownership["status"] == "running":
                if confirmed_pid is None:
                    confirmed_pid = ownership["pid"]
            elif ownership["status"] == "external":
                log_error(logger, "Port {} is occupied by external process: path={}".format(p, ownership.get("path")))
                return False, "端口 {} 已被外部程序{}占用".format(
                    p, " " + ownership.get("path") if ownership.get("path") else "占用，无法确认路径")
            elif ownership["status"] == "unknown":
                # 端口已开放但 path=None，记录用于组合确认
                has_unknown = True
                unknown_pids[p] = ownership
                all_confirmed = False
            else:
                # stopped: 端口未开放，继续等待
                all_confirmed = False

        if all_confirmed and confirmed_pid is not None:
            decision = "confirmed_by_path"
            break

        # 组合确认：所有启用端口均已监听（running 或 unknown），且启动前端口未被占用
        if has_unknown:
            all_ports_listening = all(
                is_port_listening("127.0.0.1", p) for p in all_nginx_ports)
            if all_ports_listening:
                # 收集所有 unknown listener 的 PID，逐个确认进程名为 nginx.exe
                all_nginx_listeners = True
                listener_pids = []
                for port, own in unknown_pids.items():
                    lpid = own.get("pid")
                    if lpid is not None:
                        proc_name = get_process_name(lpid, timeout=2)
                        if proc_name and proc_name.lower() == "nginx.exe":
                            listener_pids.append(lpid)
                        else:
                            all_nginx_listeners = False
                            log_info(logger, "  Port {} listener PID={} process name '{}' is not nginx.exe, combination confirm failed".format(
                                port, lpid, proc_name))
                    else:
                        all_nginx_listeners = False
                        log_info(logger, "  Port {} has no listener PID, combination confirm failed".format(port))

                if all_nginx_listeners and listener_pids:
                    # 确认启动前所有端口均未被占用（precheck 已排除外部/unknown，这里确认启动前未监听）
                    # 检查所有端口（含 running 和 unknown），确保启动前均未监听
                    precheck_safe = all(not precheck_ports_open.get(p, False) for p in all_nginx_ports)
                    if precheck_safe:
                        # 优先使用 Nginx 自己生成的 pid 文件
                        nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
                        nginx_file_pid = None
                        if os.path.isfile(nginx_pid_path):
                            try:
                                with open(nginx_pid_path, "r", encoding="utf-8") as f:
                                    nginx_file_pid = int(f.read().strip())
                            except (ValueError, IOError):
                                pass
                        # confirmed_pid 优先级：nginx.pid 文件 > listener PID
                        if nginx_file_pid and is_process_running(nginx_file_pid) is not False:
                            proc_name = get_process_name(nginx_file_pid, timeout=2)
                            if proc_name and proc_name.lower() == "nginx.exe":
                                confirmed_pid = nginx_file_pid
                            else:
                                confirmed_pid = listener_pids[0]
                        else:
                            confirmed_pid = listener_pids[0]
                        decision = "confirmed_by_combination"
                        log_info(logger, "  Combination confirm: launched_pid={} confirmed_pid={} listener_pids={} "
                                 "precheck_safe={} proc_name_match=true".format(
                                     launched_pid, confirmed_pid, listener_pids, precheck_safe))
                        break
                    else:
                        log_info(logger, "  Combination confirm skipped: some ports were already listening before launch")
                else:
                    log_info(logger, "  Combination confirm skipped: not all listeners are nginx.exe")

        time.sleep(0.5)

    if confirmed_pid is not None:
        # 写入 PID 缓存
        write_pid_file(pid_dir, "nginx.pid", confirmed_pid)
        current_admin = is_current_admin()
        if decision == "confirmed_by_combination":
            log_info(logger, "Nginx started: decision={} confirmed_pid={} launched_pid={} listener_pids={} "
                     "precheck_ports_open={} proc_name_match=true panel_elevated={}".format(
                         decision, confirmed_pid, launched_pid, listener_pids,
                         precheck_ports_open, current_admin))
            log_info(logger, "  当前系统无法读取监听进程路径，已使用端口监听状态与进程名进行组合确认；"
                     "权限链路正常，路径不可读可能来自 Windows 查询接口限制")
        else:
            log_info(logger, "Nginx started: decision={} confirmed_pid={} panel_elevated={}".format(
                decision, confirmed_pid, current_admin))

        # 确认旧端口已释放：旧 applied_ports/runtime_ports 中不属于 desired_ports 的不再由本项目 nginx 监听
        old_applied_ports = apply_state.get("applied_ports", [])
        old_runtime_ports_before = _detect_nginx_runtime_ports(root_dir, nginx_exe, logger, fast_mode=False)
        old_ports = list(dict.fromkeys(old_applied_ports + old_runtime_ports_before))
        ports_to_release = [p for p in old_ports if p not in all_nginx_ports]
        if ports_to_release:
            still_held = []
            for p in ports_to_release:
                ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                      root_dir=root_dir, timeout=2, logger=logger)
                if ownership["status"] == "running":
                    still_held.append(p)
            if still_held:
                log_warn(logger, "Nginx started on new ports, but old ports {} still held by project nginx".format(still_held))
                # 旧端口残留时不标记 applied，不返回成功
                log_info(logger, "Nginx started on new ports but old ports still held, not marking applied: PID={}".format(confirmed_pid))
                return False, "新端口已启动但旧端口 {} 仍被本项目 Nginx 占用，请先停止旧进程或执行完整重启".format(still_held)

        # 记录 applied config hash 和端口
        mark_component_config_applied(root_dir, "nginx", ports=all_nginx_ports)
        return True, confirmed_pid

    # 超时：检查是否有端口开放但无法确认归属
    any_open = any(is_port_listening("127.0.0.1", p) for p in all_nginx_ports)
    if any_open:
        log_error(logger, "Nginx port is open but cannot confirm ownership")
        return False, "path_unreadable: 端口已开放但无法确认属于当前 WNMP Runtime"

    log_error(logger, "Nginx failed to start, no listen port is open")
    return False, "Nginx 启动后端口未监听"


def stop_nginx(root_dir, cfg, logger):
    """停止 Nginx。

    优化停止流程：优先使用 recorded_pid 快速停止，避免耗时全量扫描。
    当 nginx -s quit 因 Access denied 失败时，立即 fallback 到 recorded_pid 强制终止。

    停止安全边界：
      - 允许停止 recorded_pid（进程名匹配 nginx.exe）
      - 允许停止 listener path 精确匹配 nginx.exe 的进程
      - path=None 且不是 recorded_pid 的未知 listener，禁止 kill
      - 外部进程不允许 kill

    停止成功判定：必须同时确认本项目 nginx.exe 进程已退出，不能仅依赖端口释放。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn
    from runtime.wnmp_config import get_effective_nginx_listens
    from runtime.wnmp_state import get_component_config_apply_state

    # 权限上下文日志
    current_admin = is_current_admin()
    log_info(logger, "Permission context: current_process_is_admin={}".format(current_admin))

    nginx_exe = os.path.join(root_dir, "bin", "nginx", "nginx.exe")
    # 路径收敛：通过统一路径模块获取 nginx 配置路径
    nginx_conf = get_nginx_conf_path(root_dir)
    pid_dir = os.path.join(root_dir, "runtime", "pids")

    # ---- 收集所有可能需要检查的端口 ----
    # 1) desired_ports：当前配置解析出的端口
    eff = get_effective_nginx_listens(root_dir, cfg)
    desired_ports = eff["http"] + eff["https"]
    # 2) applied_ports：state 中记录的已应用端口
    apply_state = get_component_config_apply_state(root_dir, "nginx")
    applied_ports = apply_state.get("applied_ports", [])

    # 3) runtime_detected_ports：使用 fast_mode 短扫描，避免 PowerShell 慢兜底
    runtime_detected_ports = _detect_nginx_runtime_ports(root_dir, nginx_exe, logger, fast_mode=True)

    # 合并去重所有已知端口
    all_nginx_ports = list(dict.fromkeys(desired_ports + applied_ports + runtime_detected_ports))
    log_info(logger, "  Port collection: desired={}, applied={}, runtime_detected={}, merged={}".format(
        desired_ports, applied_ports, runtime_detected_ports, all_nginx_ports))

    stopped_pids = []

    # 步骤 0：读取 nginx 自己的 pid 文件，校验 PID 对应路径
    nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
    nginx_master_pid = None
    recorded_pid = None  # 记录 PID 文件中的 PID，用于 path=None 时的组合确认
    if os.path.isfile(nginx_pid_path):
        try:
            with open(nginx_pid_path, "r", encoding="utf-8") as f:
                nginx_master_pid = int(f.read().strip())
            recorded_pid = nginx_master_pid
            # 校验 PID 对应路径是否为本项目 nginx.exe
            pid_path = get_process_path(nginx_master_pid)
            if pid_path:
                normalized_exe = _normalize_path(nginx_exe)
                normalized_pid_path = _normalize_path(pid_path)
                if normalized_pid_path != normalized_exe:
                    log_warn(logger, "  nginx.pid PID={} path={} does not match project nginx.exe {}, may be stale".format(
                        nginx_master_pid, pid_path, nginx_exe))
                    nginx_master_pid = None
                    recorded_pid = None
                else:
                    log_info(logger, "  nginx.pid PID={} path confirmed: {}".format(nginx_master_pid, pid_path))
            else:
                # path=None 时，检查进程名是否匹配 nginx.exe
                proc_name = get_process_name(nginx_master_pid, timeout=2)
                if proc_name and proc_name.lower() == "nginx.exe":
                    log_info(logger, "  nginx.pid PID={} path unknown but process name matches nginx.exe, allowing stop".format(
                        nginx_master_pid))
                else:
                    log_warn(logger, "  nginx.pid PID={} path unknown and process name '{}' does not match nginx.exe, may be stale".format(
                        nginx_master_pid, proc_name))
                    nginx_master_pid = None
                    recorded_pid = None
        except (ValueError, IOError) as e:
            log_warn(logger, "  Failed to read nginx.pid: {}".format(e))
            nginx_master_pid = None
    else:
        log_info(logger, "  nginx.pid not found at {}".format(nginx_pid_path))

    # 也检查 runtime/pids/nginx.pid
    pids_dir_pid = read_pid_file(pid_dir, "nginx.pid")
    if pids_dir_pid and not nginx_master_pid:
        nginx_master_pid = pids_dir_pid
        recorded_pid = pids_dir_pid

    # SYSTEM 进程提前返回：检查 nginx master PID 或端口 listener 的 owner
    target_pid_for_perm_check = nginx_master_pid
    if not target_pid_for_perm_check and all_nginx_ports:
        # 无 pid 文件时，查第一个开放端口的 listener
        for port in all_nginx_ports:
            if is_port_listening("127.0.0.1", port):
                listeners = get_listening_processes(port, host="127.0.0.1", root_dir=root_dir,
                                                    logger=logger, expected_path=nginx_exe)
                for ln in listeners:
                    if ln.get("pid") and ln.get("is_expected") is True:
                        target_pid_for_perm_check = ln.get("pid")
                        break
                break
    if target_pid_for_perm_check and is_system_process(target_pid_for_perm_check) and not current_admin:
        msg = "该组件由 SYSTEM/高权限启动，停止需要以管理员权限运行 WNMPanel.exe"
        log_info(logger, "Permission denied: target_pid={} owner=SYSTEM, current_admin=False. {}".format(
            target_pid_for_perm_check, msg))
        return False, msg

    # 步骤 1：nginx -s quit（必须带 -p 和 -c，与启动命令一致，确保能找到 pid 和配置）
    quit_access_denied = False
    log_info(logger, "Stopping Nginx gracefully via nginx -s quit...")
    log_info(logger, "  Command: {} -p {} -s quit -c {}".format(nginx_exe, root_dir, nginx_conf))
    try:
        result = subprocess.run(
            [nginx_exe, "-p", root_dir, "-s", "quit", "-c", nginx_conf],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=root_dir,
            timeout=10
        )
        stderr_text = result.stderr.strip() if result.stderr else ""
        log_info(logger, "  nginx -s quit rc={} stdout={} stderr={}".format(
            result.returncode,
            result.stdout.strip()[:300],
            stderr_text[:300]))
        # 检测 Access denied 错误，立即标记需要强制终止
        if result.returncode != 0 and ("Access is denied" in stderr_text or "OpenEvent" in stderr_text):
            quit_access_denied = True
            log_info(logger, "  nginx graceful stop failed access_denied, fallback to recorded_pid force kill")
    except subprocess.TimeoutExpired:
        log_warn(logger, "  nginx -s quit timed out")
    except Exception as e:
        log_warn(logger, "  nginx -s quit error: " + str(e))

    # 步骤 2：如果 nginx -s quit 成功（rc=0），短等待端口释放 + 确认进程退出
    if not quit_access_denied:
        if wait_ports_closed("127.0.0.1", all_nginx_ports, timeout=5, interval=0.3, logger=logger):
            if _confirm_nginx_stopped_fast(root_dir, nginx_exe, recorded_pid, logger):
                log_info(logger, "Nginx stopped: ports released and no project nginx.exe running")
                remove_pid_file(pid_dir, "nginx.pid")
                _cleanup_nginx_pid_file(root_dir)
                return True, stopped_pids
            else:
                log_warn(logger, "Ports released but project nginx.exe still running, continuing...")
        # 优雅停止后进程仍运行，进入强制终止

    # 步骤 3：优先使用 recorded_pid 强制终止（避免全量扫描）
    if recorded_pid and is_process_running(recorded_pid) is not False:
        log_info(logger, "Force killing recorded_pid {} (nginx master)...".format(recorded_pid))
        kill_process(recorded_pid, timeout=10, logger=logger)
        stopped_pids.append(recorded_pid)
        # taskkill /T 会处理 worker 子进程，短等待确认
        if wait_ports_closed("127.0.0.1", all_nginx_ports, timeout=5, interval=0.3, logger=logger):
            if _confirm_nginx_stopped_fast(root_dir, nginx_exe, recorded_pid, logger):
                log_info(logger, "Nginx stopped successfully via recorded_pid force kill")
                remove_pid_file(pid_dir, "nginx.pid")
                _cleanup_nginx_pid_file(root_dir)
                return True, stopped_pids

    # 步骤 4：recorded_pid 不可用或终止失败，按完整路径查找本项目 Nginx 进程（master+worker）
    # 这是最后兆底，仅在 recorded_pid 缺失/不存活/不可信时执行
    log_warn(logger, "recorded_pid unavailable or ineffective, finding all project Nginx processes by path...")
    tool_pids = find_processes_by_path(root_dir, "nginx.exe", logger)
    # 如果按路径找不到但 recorded_pid 存活且进程名匹配，也加入停止列表
    if recorded_pid and is_process_running(recorded_pid) is not False:
        if recorded_pid not in tool_pids:
            proc_name = get_process_name(recorded_pid, timeout=2)
            if proc_name and proc_name.lower() == "nginx.exe":
                log_info(logger, "  Adding recorded_pid {} (process name confirmed) to stop list".format(recorded_pid))
                tool_pids.append(recorded_pid)
    if tool_pids:
        log_info(logger, "  Found {} project Nginx processes: {}".format(len(tool_pids), tool_pids))
        terminated, failed = terminate_pids(tool_pids, timeout=10, tree=True, logger=logger)
        stopped_pids.extend(tool_pids)
        log_info(logger, "  Terminated: {}, failed: {}".format(terminated, failed))

    # 步骤 5：终止后等待端口释放 + 确认进程退出
    if wait_ports_closed("127.0.0.1", all_nginx_ports, timeout=8, interval=0.3, logger=logger):
        if _confirm_nginx_stopped_fast(root_dir, nginx_exe, recorded_pid, logger):
            log_info(logger, "Nginx stopped successfully after forced termination")
            remove_pid_file(pid_dir, "nginx.pid")
            _cleanup_nginx_pid_file(root_dir)
            return True, stopped_pids
        else:
            log_warn(logger, "Ports released but project nginx.exe still running after forced termination")

    # 步骤 6：最终确认——检查本项目 nginx.exe 是否仍在运行
    remaining_pids = find_processes_by_path(root_dir, "nginx.exe", logger)
    if not remaining_pids:
        # 无进程残留，端口可能由外部进程占用
        log_info(logger, "No project nginx.exe running, checking if ports are occupied by external processes...")
        still_listening = []
        system_hint = False
        for port in all_nginx_ports:
            if is_port_listening("127.0.0.1", port):
                listeners = get_listening_processes(port, host="127.0.0.1", root_dir=root_dir,
                                                    logger=logger, expected_path=nginx_exe)
                for ln in listeners:
                    if ln.get("is_expected") is True:
                        still_listening.append("port {} (project nginx PID={} path={})".format(
                            port, ln.get("pid"), ln.get("path", "?")))
                    elif ln.get("is_expected") is False:
                        detail = "port {} (external PID={} path={})".format(
                            port, ln.get("pid"), ln.get("path", "?"))
                        if ln.get("pid") and is_system_process(ln["pid"]):
                            system_hint = True
                        still_listening.append(detail)
                    elif ln.get("is_expected") is None:
                        # path=None 的未知监听进程，禁止 kill
                        detail = "port {} (unknown PID={} path=None, cannot confirm ownership)".format(
                            port, ln.get("pid", "?"))
                        still_listening.append(detail)
        if not still_listening:
            remove_pid_file(pid_dir, "nginx.pid")
            _cleanup_nginx_pid_file(root_dir)
            return True, stopped_pids
        msg = "本项目 Nginx 已停止，但端口被外部进程占用: " + "; ".join(still_listening)
        if system_hint:
            msg += " | 部分端口由 SYSTEM 进程占用"
        log_warn(logger, msg)
        # 本项目 Nginx 已停止，返回成功（端口被外部占用不是本项目的责任）
        remove_pid_file(pid_dir, "nginx.pid")
        _cleanup_nginx_pid_file(root_dir)
        return True, stopped_pids

    # 仍有本项目 nginx.exe 残留
    msg = "无法停止本项目 Nginx 进程，残留 PID: {}".format(remaining_pids)
    log_error(logger, msg)
    return False, msg


def detect_nginx_runtime_ports(root_dir, nginx_exe, logger=None, fast_mode=True, timeout=2):
    """通过进程扫描检测本项目 nginx.exe 当前实际监听的端口（公共 helper）。

    补充 desired_ports 和 applied_ports 可能遗漏的端口（如 applied_ports 缺失时）。
    通过 netstat 查找本项目 nginx.exe PID 的 LISTENING 端口。
    不查询 owner，适合 /api/status 热路径调用。

    fast_mode=True: 仅使用 find_processes_by_executable_path 短扫描，不走 PowerShell 兜底。
    fast_mode=False: 使用 find_processes_by_path 完整扫描（WinAPI + PowerShell），适合非热路径。
    timeout: 进程扫描超时秒数，热路径建议 2 秒。
    """
    ports = []
    try:
        # 查找本项目 nginx.exe 的所有 PID
        if fast_mode:
            # 热路径：仅短扫描，不走 PowerShell 慢兜底
            from runtime.wnmp_process import find_processes_by_executable_path
            pids = find_processes_by_executable_path(nginx_exe, timeout=timeout, logger=logger, fast_mode=True)
        else:
            # 非热路径：完整扫描（WinAPI + PowerShell）
            pids = find_processes_by_path(root_dir, "nginx.exe", logger)
        if not pids:
            return ports
        pid_set = set(str(p) for p in pids)

        # 通过 netstat 查找这些 PID 的 LISTENING 端口
        import subprocess
        netstat_timeout = min(max(timeout, 1), 5)  # 使用调用方 timeout，限制在 1-5 秒
        ns = subprocess.run(
            ["netstat", "-aon", "-p", "TCP"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=netstat_timeout
        )
        if ns.returncode == 0:
            for line in ns.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) < 5 or parts[3] != "LISTENING":
                    continue
                pid_str = parts[4]
                if pid_str not in pid_set:
                    continue
                local_addr = parts[1]
                addr_port = local_addr.rsplit(":", 1)
                if len(addr_port) == 2:
                    try:
                        ports.append(int(addr_port[1]))
                    except ValueError:
                        pass
    except Exception as e:
        try:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  detect_nginx_runtime_ports failed: {}".format(str(e)))
        except Exception:
            pass
    return sorted(set(ports))


# 保留旧名作为别名，兼容已有调用
_detect_nginx_runtime_ports = detect_nginx_runtime_ports


def _confirm_nginx_stopped_fast(root_dir, nginx_exe, recorded_pid, logger):
    """快速确认本项目 nginx.exe 已停止（避免全量 find_processes_by_path 扫描）。

    优先检查 recorded_pid 是否已退出，再检查 PID 文件中的进程。
    仅在 recorded_pid 和 PID 文件都不可用时才 fallback 到 find_processes_by_path。
    """
    # 检查 1：recorded_pid 是否已退出
    if recorded_pid:
        if is_process_running(recorded_pid) is not False:
            return False  # 进程仍存活
    else:
        # recorded_pid 缺失时，检查 PID 文件
        nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
        if os.path.isfile(nginx_pid_path):
            try:
                with open(nginx_pid_path, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip())
                if is_process_running(pid):
                    return False
            except (ValueError, IOError):
                pass

        pid_dir = os.path.join(root_dir, "runtime", "pids")
        pids_dir_pid = read_pid_file(pid_dir, "nginx.pid")
        if pids_dir_pid and is_process_running(pids_dir_pid):
            return False

    # 检查 2：快速扫描本项目 nginx.exe（仅 WinAPI+tasklist，不走 PowerShell）
    from runtime.wnmp_process import find_processes_by_executable_path
    remaining = find_processes_by_executable_path(nginx_exe, timeout=2, logger=logger, fast_mode=True)
    if remaining:
        return False

    return True


def _confirm_nginx_stopped(root_dir, nginx_exe, logger):
    """确认本项目 nginx.exe 已完全停止。

    检查：PID 文件对应进程已退出 + 按路径扫描不到本项目 nginx.exe。
    """
    from runtime.wnmp_log import log_info

    # 检查 1：按路径查找本项目 nginx.exe 进程
    remaining = find_processes_by_path(root_dir, "nginx.exe", logger)
    if remaining:
        return False

    # 检查 2：nginx.pid 文件中的进程是否仍在运行
    nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
    if os.path.isfile(nginx_pid_path):
        try:
            with open(nginx_pid_path, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            if is_process_running(pid):
                return False
        except (ValueError, IOError):
            pass

    # 检查 3：runtime/pids/nginx.pid
    pid_dir = os.path.join(root_dir, "runtime", "pids")
    pids_dir_pid = read_pid_file(pid_dir, "nginx.pid")
    if pids_dir_pid and is_process_running(pids_dir_pid):
        return False

    return True


def _cleanup_nginx_pid_file(root_dir):
    """清理 Nginx 自己的 pid 文件 runtime/nginx.pid。"""
    nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
    if os.path.isfile(nginx_pid_path):
        try:
            os.remove(nginx_pid_path)
        except OSError:
            pass


def _all_nginx_ports_free(http_port, https_port):
    """检查所有 Nginx 端口是否都已释放。"""
    if is_port_listening("127.0.0.1", http_port):
        return False
    if https_port and is_port_listening("127.0.0.1", https_port):
        return False
    return True


def reload_nginx(root_dir, cfg, logger):
    """重载 Nginx 配置（nginx -s reload）。

    仅影响 Nginx，不影响 PHP/MySQL。
    流程：nginx -t 校验 → nginx -s reload → 等待 desired_ports 由本项目 nginx 监听
    → 确认旧端口不再由本项目 nginx 监听
    → 成功后 mark_component_config_applied("nginx") 清除 dirty。
    失败时保留旧运行状态，不得启动第二个 Nginx。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn
    from runtime.wnmp_config import get_effective_nginx_listens
    from runtime.wnmp_process import check_listener_ownership
    from runtime.wnmp_state import mark_component_config_applied, get_component_config_apply_state

    nginx_exe = os.path.join(root_dir, "bin", "nginx", "nginx.exe")
    # 路径收敛：通过统一路径模块获取 nginx 配置路径
    nginx_conf = get_nginx_conf_path(root_dir)
    pid_dir = os.path.join(root_dir, "runtime", "pids")

    # 获取 desired ports
    eff = get_effective_nginx_listens(root_dir, cfg)
    desired_ports = eff["http"] + eff["https"]

    if not desired_ports:
        if eff["parsed"]:
            log_error(logger, "Nginx 配置文件中未解析到任何 listen 指令")
            return False, "Nginx 配置文件中未解析到任何 listen 指令"
        else:
            log_error(logger, "无法解析 Nginx 配置端口: " + eff.get("warning", ""))
            return False, "无法解析 Nginx 配置端口"

    # 收集旧端口：applied_ports + runtime_ports，用于 reload 后确认旧端口释放
    apply_state = get_component_config_apply_state(root_dir, "nginx")
    old_applied_ports = apply_state.get("applied_ports", [])
    old_runtime_ports = detect_nginx_runtime_ports(root_dir, nginx_exe, logger, fast_mode=False)
    old_ports = list(dict.fromkeys(old_applied_ports + old_runtime_ports))  # 去重保序
    # 需要确认释放的端口 = 旧端口中不属于 desired_ports 的
    ports_to_release = [p for p in old_ports if p not in desired_ports]

    # reload 前端口预检：新增 desired port 如果已被外部/未知进程占用，必须阻断
    # 旧端口（old_ports 中属于本项目 Nginx 的）可以继续 reload，不阻断
    new_desired_ports = [p for p in desired_ports if p not in old_ports]
    if new_desired_ports:
        log_info(logger, "Reload precheck: new desired ports not in old_ports: {}".format(new_desired_ports))
        for p in new_desired_ports:
            if is_port_listening("127.0.0.1", p):
                ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                      root_dir=root_dir, timeout=2, logger=logger)
                if ownership["status"] == "external":
                    log_error(logger, "Reload precheck: new desired port {} is occupied by external process: path={}".format(
                        p, ownership.get("path")))
                    return False, "port_preoccupied: 新配置端口 {} 已被外部程序{}占用，无法 reload".format(
                        p, " " + ownership.get("path") if ownership.get("path") else "")
                elif ownership["status"] == "unknown":
                    # unknown：尝试通过 recorded_pid/nginx.pid 确认是否属于本项目
                    # 如果无法确认归属，阻断 reload
                    is_our_process = False
                    lpid = ownership.get("pid")
                    if lpid is not None:
                        # 检查是否是 recorded_pid
                        recorded_pid = read_pid_file(pid_dir, "nginx.pid")
                        nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
                        if os.path.isfile(nginx_pid_path):
                            try:
                                with open(nginx_pid_path, "r", encoding="utf-8") as f:
                                    recorded_pid = int(f.read().strip())
                            except (ValueError, IOError):
                                pass
                        if recorded_pid and lpid == recorded_pid:
                            is_our_process = True
                        else:
                            # 检查进程名是否为 nginx.exe 且路径属于本项目
                            proc_name = get_process_name(lpid, timeout=2)
                            if proc_name and proc_name.lower() == "nginx.exe":
                                # 进程名匹配但无法确认路径归属，仍需谨慎
                                # 只有当 recorded_pid 存在且该 PID 是其子进程时才放行
                                log_info(logger, "Reload precheck: new port {} listener PID={} is nginx.exe but path=None, cannot confirm ownership".format(p, lpid))
                    if not is_our_process:
                        log_error(logger, "Reload precheck: new desired port {} is occupied by unknown process (PID={})".format(p, lpid))
                        return False, "port_preoccupied: 新配置端口 {} 已被未知进程占用，为避免误操作已阻断 reload".format(p)
                # ownership["status"] == "running"：属于本项目 Nginx，放行
        log_info(logger, "Reload precheck: all new desired ports are safe")

    # nginx -t 校验
    log_info(logger, "Testing Nginx configuration before reload...")
    ok, output = test_nginx_config(root_dir, cfg, logger)
    if not ok:
        log_error(logger, "Nginx configuration test failed, aborting reload:")
        for line in output.strip().split("\n"):
            log_error(logger, "  " + line)
        # 把 nginx -t 错误摘要拼到返回 message，保留前 1200 字符
        err_summary = output.strip()[:1200] if output else "未知错误"
        return False, "Nginx 配置校验失败: " + err_summary

    # 执行 nginx -s reload
    log_info(logger, "Reloading Nginx...")
    log_info(logger, "  Command: {} -p {} -s reload -c {}".format(nginx_exe, root_dir, nginx_conf))
    try:
        result = subprocess.run(
            [nginx_exe, "-p", root_dir, "-s", "reload", "-c", nginx_conf],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=root_dir,
            timeout=10
        )
        log_info(logger, "  nginx -s reload rc={} stdout={} stderr={}".format(
            result.returncode,
            result.stdout.strip()[:300],
            result.stderr.strip()[:300]))
        if result.returncode != 0:
            log_error(logger, "nginx -s reload returned non-zero: {}".format(result.stderr or result.stdout))
            return False, "Nginx 重载命令失败: " + (result.stderr or result.stdout).strip()[:200]
    except subprocess.TimeoutExpired:
        log_error(logger, "nginx -s reload timed out")
        return False, "Nginx 重载命令超时"
    except Exception as e:
        log_error(logger, "nginx -s reload error: " + str(e))
        return False, "Nginx 重载异常: " + str(e)

    # 等待 desired_ports 由本项目 nginx 监听
    log_info(logger, "Waiting for Nginx desired ports after reload...")
    start_time = time.time()
    timeout = 15
    confirmed_pid = None
    decision = None

    while time.time() - start_time < timeout:
        all_confirmed = True
        has_unknown = False
        unknown_pids = {}  # port -> ownership dict
        for p in desired_ports:
            ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                  root_dir=root_dir, timeout=2, logger=logger)
            if ownership["status"] == "running":
                if confirmed_pid is None:
                    confirmed_pid = ownership["pid"]
            elif ownership["status"] == "external":
                log_error(logger, "Port {} is occupied by external process after reload: path={}".format(
                    p, ownership.get("path")))
                return False, "重载后端口 {} 被外部程序{}占用".format(
                    p, " " + ownership.get("path") if ownership.get("path") else "占用，无法确认路径")
            elif ownership["status"] == "unknown":
                # 端口已监听但 path=None，记录用于组合确认
                has_unknown = True
                unknown_pids[p] = ownership
                all_confirmed = False
            else:
                # stopped: 端口未开放，继续等待
                all_confirmed = False

        if all_confirmed and confirmed_pid is not None:
            decision = "confirmed_by_path"
            break

        # 组合确认 reload：所有 desired_ports 已监听，unknown 的 listener PID 进程名为 nginx.exe
        if has_unknown:
            all_ports_listening = all(
                is_port_listening("127.0.0.1", p) for p in desired_ports)
            if all_ports_listening:
                all_nginx_listeners = True
                listener_pids = []
                for port, own in unknown_pids.items():
                    lpid = own.get("pid")
                    if lpid is not None:
                        proc_name = get_process_name(lpid, timeout=2)
                        if proc_name and proc_name.lower() == "nginx.exe":
                            listener_pids.append(lpid)
                        else:
                            all_nginx_listeners = False
                            log_info(logger, "  Reload port {} listener PID={} process name '{}' is not nginx.exe".format(
                                port, lpid, proc_name))
                    else:
                        all_nginx_listeners = False
                        log_info(logger, "  Reload port {} has no listener PID".format(port))

                if all_nginx_listeners and listener_pids:
                    # 优先使用 nginx.pid 文件
                    nginx_pid_path = os.path.join(root_dir, "runtime", "nginx.pid")
                    nginx_file_pid = None
                    if os.path.isfile(nginx_pid_path):
                        try:
                            with open(nginx_pid_path, "r", encoding="utf-8") as f:
                                nginx_file_pid = int(f.read().strip())
                        except (ValueError, IOError):
                            pass
                    if nginx_file_pid and is_process_running(nginx_file_pid) is not False:
                        proc_name = get_process_name(nginx_file_pid, timeout=2)
                        if proc_name and proc_name.lower() == "nginx.exe":
                            confirmed_pid = nginx_file_pid
                        else:
                            confirmed_pid = listener_pids[0]
                    else:
                        confirmed_pid = listener_pids[0]
                    decision = "confirmed_by_combination_reload"
                    log_info(logger, "  Reload combination confirm: confirmed_pid={} listener_pids={}".format(
                        confirmed_pid, listener_pids))
                    break
                else:
                    log_info(logger, "  Reload combination confirm skipped: not all listeners are nginx.exe")

        time.sleep(0.5)

    if confirmed_pid is None:
        # 超时但旧 Nginx 仍在运行（reload 可能部分成功）
        log_warn(logger, "Nginx reload timed out waiting for desired ports, old Nginx may still be running")
        return False, "Nginx reload 超时，新端口未全部就绪，旧 Nginx 仍在运行"

    # 确认旧端口已释放：旧端口中不属于 desired_ports 的不再由本项目 nginx 监听
    if ports_to_release:
        log_info(logger, "Checking old ports {} are released after reload...".format(ports_to_release))
        still_held = []
        for p in ports_to_release:
            ownership = check_listener_ownership(p, nginx_exe, host="127.0.0.1",
                                                  root_dir=root_dir, timeout=2, logger=logger)
            if ownership["status"] == "running":
                still_held.append(p)
                log_warn(logger, "  Old port {} still held by project nginx after reload".format(p))
        if still_held:
            log_warn(logger, "Nginx reload succeeded on new ports, but old ports {} still held".format(still_held))
            # 不标记 applied，因为旧端口未释放
            write_pid_file(pid_dir, "nginx.pid", confirmed_pid)
            return False, "Nginx reload 后旧端口 {} 仍由本项目 nginx 监听，请尝试重启".format(still_held)

    write_pid_file(pid_dir, "nginx.pid", confirmed_pid)
    # 标记配置已应用，清除 dirty
    mark_component_config_applied(root_dir, "nginx", ports=desired_ports)
    log_info(logger, "Nginx reloaded: decision={} confirmed_pid={} config applied".format(
        decision, confirmed_pid))
    return True, confirmed_pid


def get_nginx_status(root_dir, cfg, logger):
    """获取 Nginx 运行状态。

    复用 panel/status.py 的 get_component_status 统一状态语义，
    CLI 和 Panel 不再得出不同结论。
    """
    try:
        from runtime.panel.status import get_component_status
        st = get_component_status("nginx", cfg)
        return {
            "running": st.get("running", False),
            "pid": st.get("listener_pid") or st.get("pid"),
            "port_listening": st.get("port_open", False),
            "state": st.get("state", "unknown"),
        }
    except Exception:
        # 回退到旧逻辑（兼容异常场景）
        from runtime.wnmp_config import get_effective_nginx_listens
        pid_dir = os.path.join(root_dir, "runtime", "pids")
        pid = read_pid_file(pid_dir, "nginx.pid")
        running = is_process_running(pid) if pid else False
        if running is None:
            running = True
        eff = get_effective_nginx_listens(root_dir, cfg)
        all_ports = eff["http"] + eff["https"]
        port_listening = any(is_port_listening("127.0.0.1", p) for p in all_ports) if all_ports else False
        return {"running": running, "pid": pid, "port_listening": port_listening}