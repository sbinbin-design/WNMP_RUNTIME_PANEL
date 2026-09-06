# -*- coding: utf-8 -*-
"""
WNMP PHP Module - PHP-CGI 启停控制
使用 Python 标准库实现，不依赖第三方包

启动确认：优先 listener path 精确匹配，path=None 时使用组合确认
  (launched_pid 存活 + 端口已监听 + listener_pid==launched_pid 或进程名匹配 php-cgi.exe)
停止确认：优先 recorded_pid + 进程名匹配，path=None 时允许停止 recorded_pid
"""
import os
import time
from runtime.wnmp_process import (
    write_pid_file, read_pid_file, remove_pid_file,
    is_process_running, kill_process, start_process,
    wait_for_port_open, wait_for_port_close, is_port_listening,
    find_processes_by_path, cleanup_residual_processes,
    terminate_pids, wait_ports_closed, find_port_listener_path,
    get_listening_processes,
    get_process_path, is_system_process, is_current_admin,
    get_process_image_path, get_process_name, get_pid_detail,
    find_processes_by_executable_path, _paths_equal
)
from runtime.wnmp_component_paths import get_php_ini_path


def start_php_cgi(root_dir, cfg, logger):
    """启动 PHP-CGI。启用 PHP 原生 Master + Worker 模型。

    通过环境变量 PHP_FCGI_CHILDREN / PHP_FCGI_MAX_REQUESTS 传递给 php-cgi.exe，
    使 php-cgi 以 Master + N 个 Worker 方式运行。Worker 达到 max_requests 后正常退出，
    由 Master 自动补充新 Worker，PHP 服务不因 Worker 轮换而永久停止。

    php-cgi.pid 语义：统一表示 WNMP 启动的 PHP-CGI Master PID（proc.pid）。
    9000 listener PID（可能等于某个 Worker 或 Master）不写入 php-cgi.pid。

    启动成功确认：
      1. Master（proc.pid）仍存活；
      2. host:port 已由本项目 bin/php/php-cgi.exe 正常监听。
    两者满足即认为启动成功，listener PID 与 Master PID 不同属正常现象。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn
    from runtime.wnmp_config import get_effective_php_cgi_host_port, parse_php_cgi_config
    from runtime.wnmp_process import check_listener_ownership

    php_cgi_exe = os.path.join(root_dir, "bin", "php", "php-cgi.exe")
    # 路径收敛：通过统一路径模块获取 php.ini 路径
    php_ini = get_php_ini_path(root_dir)
    pid_dir = os.path.join(root_dir, "runtime", "pids")

    # 从 php-cgi.ini 解析 host/port/children/max_requests
    php_cgi_host, php_cgi_port = get_effective_php_cgi_host_port(root_dir, cfg)
    cgi_cfg = parse_php_cgi_config(root_dir)
    config_parsed = cgi_cfg is not None

    # 默认 children / max_requests（php-cgi.ini 缺失或字段缺失时的回退值）
    children = 5
    max_requests = 500
    if cgi_cfg:
        children = cgi_cfg.get("children", 5)
        max_requests = cgi_cfg.get("max_requests", 500)

    # 配置解析失败处理
    if not config_parsed:
        log_warn(logger, "无法解析 php-cgi.ini 配置端口，已回退 runtime.ini 默认值 host={}:{}".format(
            php_cgi_host, php_cgi_port))

    log_info(logger, "PHP-CGI 配置解析: host={} port={} children={} max_requests={} config_parsed={}".format(
        php_cgi_host, php_cgi_port, children, max_requests, config_parsed))

    # 启动前预检：端口是否已被占用
    precheck_port_open = is_port_listening(php_cgi_host, php_cgi_port)
    if precheck_port_open:
        precheck_ownership = check_listener_ownership(php_cgi_port, php_cgi_exe,
                                                       host=php_cgi_host, root_dir=root_dir,
                                                       timeout=2, logger=logger)
        log_info(logger, "  Precheck: port {}:{} status={} pid={} path={}".format(
            php_cgi_host, php_cgi_port, precheck_ownership["status"],
            precheck_ownership.get("pid"), precheck_ownership.get("path")))
        if precheck_ownership["status"] == "running":
            # 本项目 php-cgi 已在运行，幂等返回（不覆盖已有 php-cgi.pid）
            log_info(logger, "PHP-CGI already running: listener_pid={}".format(
                precheck_ownership.get("pid")))
            return True, precheck_ownership.get("pid")
        elif precheck_ownership["status"] in ("external", "unknown"):
            # 端口被外部程序或未知进程占用，阻断启动
            if precheck_ownership["status"] == "external":
                log_error(logger, "Port {}:{} is preoccupied by external process: path={}".format(
                    php_cgi_host, php_cgi_port, precheck_ownership.get("path")))
                return False, "port_preoccupied: 端口 {}:{} 已被外部程序{}占用".format(
                    php_cgi_host, php_cgi_port,
                    " " + precheck_ownership.get("path") if precheck_ownership.get("path") else "")
            else:
                log_error(logger, "Port {}:{} is preoccupied by unknown process".format(
                    php_cgi_host, php_cgi_port))
                return False, "port_preoccupied: 端口 {}:{} 已被未知进程占用，为避免误操作已阻断启动".format(
                    php_cgi_host, php_cgi_port)

    # 启动前清理旧 PID 文件，避免 stale PID 干扰
    remove_pid_file(pid_dir, "php-cgi.pid")

    cmd = [
        php_cgi_exe,
        "-b", "{}:{}".format(php_cgi_host, php_cgi_port),
        "-c", php_ini
    ]

    log_file = os.path.join(root_dir, "logs", "php", "php-cgi.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # 构造 PHP 专用环境变量（只在本次启动的 php-cgi 子进程内生效，不污染其它进程）
    env = os.environ.copy()
    env["PHP_FCGI_CHILDREN"] = str(children)
    env["PHP_FCGI_MAX_REQUESTS"] = str(max_requests)

    log_info(logger, "Starting PHP-CGI on {}:{}...".format(php_cgi_host, php_cgi_port))
    log_info(logger, "  Command: {}".format(" ".join(cmd)))
    log_info(logger, "  Env: PHP_FCGI_CHILDREN={} PHP_FCGI_MAX_REQUESTS={}".format(children, max_requests))
    proc = start_process(cmd, cwd=root_dir, logger=logger, stdout_file=log_file, stderr_file=log_file, env=env)
    if proc is None:
        log_error(logger, "Failed to start PHP-CGI process")
        return False, "Failed to start PHP-CGI process"

    # Master PID：WNMP 启动的 PHP-CGI 主进程，用于生命周期控制与 php-cgi.pid
    master_pid = proc.pid
    log_info(logger, "PHP-CGI Master started: master_pid={}".format(master_pid))

    # 等待端口被本项目 php-cgi.exe 监听
    log_info(logger, "Waiting for PHP-CGI port {}:{} to be confirmed...".format(
        php_cgi_host, php_cgi_port))
    start_time = time.time()
    timeout = 30  # 总等待超时

    listener_pid = None
    confirmed = False
    while time.time() - start_time < timeout:
        # Master 提前退出 → 启动失败
        if is_process_running(master_pid) is False:
            log_error(logger, "PHP-CGI Master exited prematurely: master_pid={}".format(master_pid))
            break

        ownership = check_listener_ownership(php_cgi_port, php_cgi_exe,
                                              host=php_cgi_host, root_dir=root_dir,
                                              timeout=2, logger=logger)
        log_info(logger, "  Port {}:{} listener: status={} pid={} path={} is_ours={}".format(
            php_cgi_host, php_cgi_port, ownership["status"],
            ownership.get("pid"), ownership.get("path"), ownership.get("is_ours")))

        if ownership["status"] == "running":
            listener_pid = ownership["pid"]
            confirmed = True
            break
        elif ownership["status"] == "external":
            log_error(logger, "Port {}:{} is occupied by external process: path={}".format(
                php_cgi_host, php_cgi_port, ownership.get("path")))
            break
        # unknown / stopped：继续等待
        time.sleep(0.5)

    # 启动成功条件：Master 仍存活 + 9000 已由本项目 php-cgi.exe 正常监听
    master_alive = is_process_running(master_pid) is not False
    if confirmed and master_alive:
        # 写入 php-cgi.pid = Master PID（listener PID 不覆盖）
        write_pid_file(pid_dir, "php-cgi.pid", master_pid)
        log_info(logger, "PHP-CGI started: master_pid={} listener_pid={} children={} max_requests={}".format(
            master_pid, listener_pid, children, max_requests))
        if listener_pid is not None and listener_pid != master_pid:
            log_info(logger, "  master_pid != listener_pid 属正常现象（Master+Worker 模型）")
        # 启动成功后清除 config_dirty 标记
        try:
            from runtime.wnmp_state import mark_component_config_applied
            mark_component_config_applied(root_dir, "php")
        except Exception:
            pass
        return True, master_pid

    # 启动失败：清理本次启动可能产生的整个 PHP 进程树，避免孤儿 Worker
    log_error(logger, "PHP-CGI start failed, cleaning up process tree...")
    try:
        from runtime.wnmp_process import terminate_pids
        terminate_pids([master_pid], timeout=10, tree=True, logger=logger)
    except Exception:
        pass
    remove_pid_file(pid_dir, "php-cgi.pid")

    if not master_alive:
        log_error(logger, "PHP-CGI Master exited, failed to start (master_pid={})".format(master_pid))
        return False, "PHP-CGI Master exited, port {}:{} not listening".format(php_cgi_host, php_cgi_port)

    if is_port_listening(php_cgi_host, php_cgi_port):
        log_error(logger, "PHP-CGI port is open but cannot confirm ownership")
        return False, "path_unreadable: 端口已开放但无法确认属于当前 WNMP Runtime"

    log_error(logger, "PHP-CGI failed to start, port {}:{} not listening".format(php_cgi_host, php_cgi_port))
    return False, "PHP-CGI port not listening after start"


def _is_project_php_pid(pid, php_cgi_exe, timeout=3):
    """判断 pid 是否属于当前 WNMP 的 php-cgi.exe（executable path 精确匹配）。

    path 不可读时返回 None（无法确认，需走保守路径），
    非 php-cgi.exe / 非本项目 path 返回 False，精确匹配返回 True。
    """
    if not pid:
        return False
    try:
        proc_path = get_process_path(pid, timeout=timeout)
    except Exception:
        proc_path = None
    if not proc_path:
        return None  # path 不可读，无法精确确认
    return _paths_equal(proc_path, php_cgi_exe)


def find_php_master_from_worker(worker_pid, php_cgi_exe, max_depth=16, timeout=2, logger=None):
    """从已确认属于当前 WNMP 的 Worker，沿父链找到最上层同路径 php-cgi.exe。

    职责：从 9000 listener（通常是 Worker）出发，逐级向上：
      - 只要父进程仍精确匹配当前 WNMP/bin/php/php-cgi.exe，就继续上溯；
      - 遇到不再匹配当前 php-cgi.exe（父进程已不是本项目 PHP）即停止；
      - 返回这一棵 PHP-CGI 进程树最上层的 php-cgi.exe PID（视为 Master）。

    任一环节 path 无法读取（父链断裂/权限不足）则返回 None（保守，不猜）。

    返回值：
      int：最上层 Master PID
      None：无法安全定位 Master（worker 自身不是本项目 php-cgi，或父链中断）
    """
    from runtime.wnmp_process import get_parent_pid
    if not worker_pid:
        return None

    # worker 自身必须是当前 WNMP php-cgi.exe
    if _is_project_php_pid(worker_pid, php_cgi_exe, timeout=timeout) is not True:
        return None

    top_pid = worker_pid
    cur = worker_pid
    for _ in range(max_depth):
        parent = get_parent_pid(cur, timeout=timeout)
        if not parent:
            # 完全无法取得 ParentProcessId：无法判断父链 → 保守返回 None
            if logger:
                from runtime.wnmp_log import log_warn
                log_warn(logger, "  Ancestry walk: pid={} parent PID unavailable, "
                                 "refusing to guess top as Master (return None)".format(cur))
            return None

        # 先判断父进程是否仍存活（关键：WNMP 的 wnmpctl start 是一次性 launcher，
        # PHP Master 的原父进程 python.exe 在长期运行后可能已退出；
        # “父 PID 对应进程已不存在”不等于“path 无法读取”，需区分处理）。
        parent_running = is_process_running(parent)
        if parent_running is False:
            # 父进程已明确不存在 → 当前 top_pid 是仍存活 PHP 树的最上层 → 视为 Master
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  Ancestry walk: parent PID={} confirmed gone, "
                                 "top master = {} (launcher exited)".format(parent, top_pid))
            return top_pid

        # parent_running is True 或 None：尝试精确读取父进程 path
        parent_match = _is_project_php_pid(parent, php_cgi_exe, timeout=timeout)

        if parent_running is True:
            if parent_match is True:
                # 父进程精确匹配当前 WNMP php-cgi.exe → 继续向上爬
                top_pid = parent
                cur = parent
                if logger:
                    from runtime.wnmp_log import log_info
                    log_info(logger, "  Ancestry walk: climbed to parent php-cgi PID={}".format(parent))
                continue
            if parent_match is False:
                # 父进程明确不是当前 WNMP php-cgi.exe（如系统/启动器）→ 越过 PHP 树边界
                if logger:
                    from runtime.wnmp_log import log_info
                    log_info(logger, "  Ancestry walk: parent PID={} not project php-cgi, top master = {}".format(
                        parent, top_pid))
                return top_pid
            # parent_match is None：父进程存活但 path 无法读取 → 不能证明 top_pid 是 Master
            if logger:
                from runtime.wnmp_log import log_warn
                log_warn(logger, "  Ancestry walk: parent PID={} alive but path unreadable, "
                                 "refusing to guess top {} as Master (return None)".format(parent, top_pid))
            return None

        # parent_running is None（无法确认父进程存活）
        if parent_match is True:
            # path 可精确确认为本项目 php-cgi → 能读到 path 说明进程实际存在，
            # parent_running None 只是查询抖动 → 继续向上爬
            top_pid = parent
            cur = parent
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  Ancestry walk: parent PID={} path confirmed project php-cgi "
                                 "(liveness None), climbed".format(parent))
            continue
        if parent_match is False:
            # path 精确确认非本项目 → 越过 PHP 树边界
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  Ancestry walk: parent PID={} path confirmed non-project, top master = {}".format(
                    parent, top_pid))
            return top_pid
        # parent_running is None 且 path 无法精确确认 → 保守返回 None，不猜测
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  Ancestry walk: parent PID={} liveness None and path unreadable, "
                             "refusing to guess (return None)".format(parent))
        return None

    return top_pid


def _get_project_listener_pid(host, port, php_cgi_exe, logger):
    """返回当前配置端口上「已精确确认属于本项目 php-cgi.exe」的 listener PID。

    只采纳 listener executable path 精确等于当前 WNMP/bin/php/php-cgi.exe 的 PID。
    若 listener path 无法读取（仅能拿到进程名 php-cgi.exe），无法证明其属于当前 WNMP，
    保守返回 None（调用方不得据此强制终止）。

    Returns:
        int or None：已确认属于本项目的 listener PID；无确认 PID 返回 None
    """
    from runtime.wnmp_log import log_warn, log_info
    listeners = get_listening_processes(port, host=host, root_dir=None,
                                        logger=logger, expected_path=php_cgi_exe)
    for ln in listeners:
        ln_pid = ln.get("pid")
        ln_is_expected = ln.get("is_expected")
        if not ln_pid:
            continue
        if ln_is_expected is True:
            # listener path 精确匹配本项目 php-cgi.exe
            log_info(logger, "  Port {}:{} listener PID={} confirmed as project php-cgi".format(
                host, port, ln_pid))
            return ln_pid
        # ln_is_expected is None（path 不可读）或 False（外部）→ 不采纳
    if listeners:
        log_warn(logger, "  Port {}:{} has no listener confirmed as project php-cgi (path unreadable/external)".format(
            host, port))
    return None


def _recorded_is_master_via_listener(host, port, php_cgi_exe, recorded_pid, logger):
    """情况 2：recorded_pid 存活但 path 不可读时，用 listener 祖先关系确认其是否为 Master。

    要求同时满足：
      1. 9000 listener 已精确确认属于当前 WNMP php-cgi.exe（path 可读）；
      2. listener PID 的父/祖先进程链能回溯到 recorded_pid（属于同一进程树）。

    满足则确认 recorded_pid 为 Master。任一不满足返回 False（保守）。

    Returns:
        bool
    """
    from runtime.wnmp_process import is_process_in_ancestry
    from runtime.wnmp_log import log_info, log_warn

    listener_pid = _get_project_listener_pid(host, port, php_cgi_exe, logger)
    if listener_pid is None:
        return False

    if not is_process_in_ancestry(listener_pid, recorded_pid, max_depth=16, timeout=2):
        log_warn(logger, "  Listener PID={} is NOT in ancestry of recorded PID={}".format(
            listener_pid, recorded_pid))
        return False

    log_info(logger, "  Listener PID={} belongs to recorded PID={} ancestry; confirming as Master".format(
        listener_pid, recorded_pid))
    return True


def stop_php_cgi(root_dir, cfg, logger):
    """停止 PHP-CGI。按 Master + Worker 完整进程树终止。

    停止安全边界：
      - 主路径：读取 php-cgi.pid 取得 Master PID，确认存活且 executable 属于本项目
        bin/php/php-cgi.exe 后，对 Master 执行进程树终止（terminate_pids tree=True）。
      - 情况 2：recorded PID 存活但 path 不可读，若 9000 listener 是 php-cgi.exe 且其
        父链回溯到 recorded PID，则允许将 recorded PID 确认为 Master 并终止整树。
      - 情况 3：PID 文件缺失/stale，从 9000 listener（Worker）沿父链找到最上层
        同路径 php-cgi.exe 作为 Master，终止其整棵进程树。
      - 安全边界：PID 缺失/stale 且 listener path 无法读取时，仅凭进程名 php-cgi.exe
        不得强制终止（无法证明属于当前 WNMP），保守返回失败，不误杀。
      - 不误杀：其它 WNMP 实例、系统中其它 PHP、用户手动启动的其它端口 php-cgi。

    成功条件：当前受管 Master + Worker tree 全部停止 + 9000 已释放。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn
    from runtime.wnmp_config import get_effective_php_cgi_host_port

    # 权限上下文日志
    current_admin = is_current_admin()
    log_info(logger, "Permission context: current_process_is_admin={}".format(current_admin))

    pid_dir = os.path.join(root_dir, "runtime", "pids")
    php_cgi_exe = os.path.join(root_dir, "bin", "php", "php-cgi.exe")
    # 从 php-cgi.ini 解析 host/port
    php_cgi_host, php_cgi_port = get_effective_php_cgi_host_port(root_dir, cfg)

    stopped_pids = []
    recorded_pid = read_pid_file(pid_dir, "php-cgi.pid")

    # ============ 阶段一：尝试确立 Master PID ============
    master_pid = None
    recorded_alive = False
    if recorded_pid:
        running = is_process_running(recorded_pid)
        if running is not False:
            recorded_alive = True

    if recorded_pid and recorded_alive:
        # recorded_pid 存活 → 情况 1 / 情况 2
        path_ok = _is_project_php_pid(recorded_pid, php_cgi_exe, timeout=3)
        if path_ok is True:
            # 情况 1：path 精确确认属于当前 WNMP → recorded_pid 即为 Master
            master_pid = recorded_pid
        elif path_ok is None:
            # 情况 2：recorded_pid 存活但 path 不可读。
            # 若 9000 listener 是 php-cgi.exe 且父链能回溯到 recorded_pid，则确认其为 Master。
            log_info(logger, "Recorded PID {} alive but path unreadable, verifying via listener ancestry...".format(recorded_pid))
            if _recorded_is_master_via_listener(php_cgi_host, php_cgi_port, php_cgi_exe,
                                                recorded_pid, logger):
                master_pid = recorded_pid
            else:
                log_warn(logger, "Recorded PID {} alive but cannot confirm as Master via listener ancestry".format(recorded_pid))
        else:
            log_warn(logger, "Recorded PID {} is not project php-cgi.exe, ignoring".format(recorded_pid))
    elif recorded_pid and not recorded_alive:
        log_info(logger, "Recorded PID {} not running (stale)".format(recorded_pid))
    else:
        log_info(logger, "php-cgi.pid missing (or empty)")

    # ============ 阶段二：Master PID 有效 → 终止整树 ============
    if master_pid is not None:
        log_info(logger, "Stopping PHP-CGI Master PID={} (process tree)...".format(master_pid))
        terminated, failed = terminate_pids([master_pid], timeout=10, tree=True, logger=logger)
        stopped_pids.append(master_pid)
        log_info(logger, "  Master tree terminate: terminated={} failed={}".format(terminated, failed))

        # 收紧 stop 成功条件：仅当 Master 确认已退出(is_process_running is False)
        # 且 9000 已释放，才返回 stop success。
        # is_process_running 三态：
        #   True  = Master 仍存活 → stop failed
        #   False = Master 已确认退出 → 满足条件之一
        #   None  = Master 状态无法确认 → 保守 failed，不得返回 success
        port_closed = wait_for_port_close(php_cgi_host, php_cgi_port, timeout=8, logger=logger)
        master_state = is_process_running(master_pid)
        if port_closed and master_state is False:
            log_info(logger, "PHP-CGI stopped successfully: master PID {} confirmed exited and port released".format(master_pid))
            remove_pid_file(pid_dir, "php-cgi.pid")
            return True, stopped_pids
        # 未满足严格 success 条件，记录原因后落入兜底
        if master_state is True:
            log_warn(logger, "Master PID {} still alive after terminate (port_closed={}); not declaring success".format(
                master_pid, port_closed))
        elif master_state is None:
            log_warn(logger, "Master PID {} state unconfirmable (is_process_running=None, port_closed={}); "
                     "conservative not declaring success".format(master_pid, port_closed))
        if not port_closed and master_state is False:
            log_warn(logger, "Master PID {} exited but port {}:{} still listening; attempting recovery".format(
                master_pid, php_cgi_host, php_cgi_port))
        log_warn(logger, "Master tree stop not confirmed, attempting listener-rooted recovery")

    # ============ 阶段三：兜底定位（PID 缺失/stale/Master 终止未释放） ============
    # 从 9000 listener 出发，尽量定位到 Master 再整树终止，绝不只杀 Worker。
    found_master = None
    listener_pid = _get_project_listener_pid(php_cgi_host, php_cgi_port, php_cgi_exe, logger)
    if listener_pid is not None:
        # 从已确认属于本项目的 listener，沿父链找到最上层同路径 php-cgi.exe 作为 Master
        found_master = find_php_master_from_worker(listener_pid, php_cgi_exe,
                                                   max_depth=16, timeout=2, logger=logger)
        if found_master is not None:
            log_info(logger, "Recovered PHP-CGI Master from listener: master_pid={}".format(found_master))
            terminated, failed = terminate_pids([found_master], timeout=10, tree=True, logger=logger)
            stopped_pids.append(found_master)
            log_info(logger, "  Recovered master tree terminate: terminated={} failed={}".format(terminated, failed))

            # 与主路径一致：Master 确认已退出(is False) 且 9000 已释放 才返回 success。
            port_closed = wait_for_port_close(php_cgi_host, php_cgi_port, timeout=10, logger=logger)
            master_state = is_process_running(found_master)
            if port_closed and master_state is False:
                log_info(logger, "PHP-CGI stopped successfully: recovered master PID {} confirmed exited and port released".format(
                    found_master))
                remove_pid_file(pid_dir, "php-cgi.pid")
                return True, stopped_pids
            if master_state is True:
                log_warn(logger, "Recovered Master PID {} still alive after terminate (port_closed={}); not declaring success".format(
                    found_master, port_closed))
            elif master_state is None:
                log_warn(logger, "Recovered Master PID {} state unconfirmable (port_closed={}); conservative not declaring success".format(
                    found_master, port_closed))
        else:
            log_warn(logger, "Listener PID {} confirmed as project php-cgi but could not locate its Master (ancestry broken)".format(listener_pid))
    else:
        # listener path 无法读取或 listener 非本项目：保守处理
        # 若仍能发现端口被监听，说明 9000 被无法确认归属的进程占用 → 保守失败
        if is_port_listening(php_cgi_host, php_cgi_port):
            log_error(logger, "PHP-CGI port {}:{} occupied by process with unreadable/unconfirmed path; "
                     "refusing to kill on process-name alone".format(php_cgi_host, php_cgi_port))
            return False, "无法安全确认 PHP-CGI 进程归属（PID 缺失且 listener path 无法读取），保持保守失败，请以管理员权限重试"

    # ============ 阶段四：端口仍开放 → 诊断 ============
    if is_port_listening(php_cgi_host, php_cgi_port):
        listeners = get_listening_processes(php_cgi_port, host=php_cgi_host, root_dir=root_dir,
                                            logger=logger, expected_path=php_cgi_exe)
        details = []
        system_hint = False
        for ln in listeners:
            ln_pid = ln.get("pid")
            ln_addr = ln.get("local_address", "?")
            ln_path = ln.get("path") or "unknown"
            if ln.get("is_expected") is True:
                detail = "local={} (project php-cgi PID={} path={}".format(ln_addr, ln_pid, ln_path)
                if ln_pid and is_system_process(ln_pid):
                    detail += ", SYSTEM process"
                    system_hint = True
                detail += ")"
                details.append(detail)
            elif ln.get("is_expected") is False:
                if ln.get("is_in_root") is True:
                    details.append("local={} (non-target PID={} path={})".format(ln_addr, ln_pid, ln_path))
                else:
                    details.append("local={} (external PID={} path={})".format(ln_addr, ln_pid, ln_path))
            else:
                details.append("local={} (PID={} path={}, cannot confirm ownership)".format(
                    ln_addr, ln_pid or "?", ln_path))
        msg = "port {}:{} still occupied: ".format(php_cgi_host, php_cgi_port) + "; ".join(details)
        if system_hint:
            msg += " | 该组件由 SYSTEM/高权限启动，停止需要以管理员权限运行 WNMPanel.exe"
        log_error(logger, "Failed to stop PHP-CGI: " + msg)
        return False, msg

    # ============ 末尾兜底：端口已不监听 ============
    # 走到这里表示端口 9000 已释放。但按严格 success 条件，若本次曾确立过 Master 目标，
    # 必须其 is_process_running 为 False（确认退出）才允许 success；
    # True（仍存活）或 None（无法确认）均保守 failed。
    if not is_port_listening(php_cgi_host, php_cgi_port):
        blocking_master = None
        blocking_state = None
        for candidate in (master_pid, found_master):
            if not candidate:
                continue
            state = is_process_running(candidate)
            if state is True or state is None:
                blocking_master = candidate
                blocking_state = state
                break
        if blocking_master is not None:
            if blocking_state is True:
                log_warn(logger, "Port {}:{} not listening but Master PID {} still alive; not declaring success".format(
                    php_cgi_host, php_cgi_port, blocking_master))
                return False, "Master PID {} 仍存活但端口已不监听，停止状态无法确认，请检查残留进程".format(blocking_master)
            else:
                log_warn(logger, "Port {}:{} not listening but Master PID {} state unconfirmable; conservative not declaring success".format(
                    php_cgi_host, php_cgi_port, blocking_master))
                return False, "Master PID {} 状态无法确认（端口已不监听），保守判定停止失败，请检查残留进程".format(blocking_master)

        log_info(logger, "PHP-CGI stopped (port not in use, no live/unconfirmed master target)")
        remove_pid_file(pid_dir, "php-cgi.pid")
        return True, stopped_pids

    # 理论上到此处端口应已在阶段四 return，此处兜底防御
    log_error(logger, "PHP-CGI stop could not be confirmed (port {}:{} in use)".format(
        php_cgi_host, php_cgi_port))
    return False, "PHP-CGI stop could not be confirmed, port {}:{} still in use".format(
        php_cgi_host, php_cgi_port)


def get_php_cgi_status(root_dir, cfg, logger):
    """获取 PHP-CGI 运行状态。

    复用 panel/status.py 的 get_component_status 统一状态语义，
    CLI 和 Panel 不再得出不同结论。
    """
    try:
        from runtime.panel.status import get_component_status
        st = get_component_status("php", cfg)
        return {
            "running": st.get("running", False),
            # pid 统一为 Master PID（php-cgi.pid 记录的 Master，非 listener/Worker）
            "pid": st.get("pid"),
            # listener/Worker PID 单独返回，不混入 pid
            "listener_pid": st.get("listener_pid"),
            "port_listening": st.get("port_open", False),
            "state": st.get("state", "unknown"),
        }
    except Exception:
        # 回退到旧逻辑（兼容异常场景）
        from runtime.wnmp_config import get_effective_php_cgi_host_port
        pid_dir = os.path.join(root_dir, "runtime", "pids")
        pid = read_pid_file(pid_dir, "php-cgi.pid")
        running = is_process_running(pid) if pid else False
        if running is None:
            running = True
        php_cgi_host, php_cgi_port = get_effective_php_cgi_host_port(root_dir, cfg)
        port_listening = is_port_listening(php_cgi_host, php_cgi_port)
        return {"running": running, "pid": pid, "listener_pid": None, "port_listening": port_listening}
