# -*- coding: utf-8 -*-
"""
WNMP MySQL Module - MySQL initialization and start/stop control
"""
import os
import re
import secrets
import string
import time
import socket
import subprocess
from runtime.wnmp_process import (
    write_pid_file, read_pid_file, remove_pid_file,
    is_process_running, kill_process, start_process,
    wait_for_port_open, wait_for_port_close, is_port_listening,
    find_processes_by_path, cleanup_residual_processes,
    terminate_pids, wait_ports_closed, find_port_listener_path,
    get_listening_processes,
    get_process_path, is_system_process, is_current_admin,
    get_process_image_path, get_process_name, get_pid_detail
)
from runtime.wnmp_path import resolve_path, to_forward_slash
from runtime.wnmp_component_paths import get_mysql_ini_path


MYSQL_PASSWORD_FILE = "root-password.txt"
MYSQL_PASSWORD_LENGTH = 32


def get_password_file_path(root_dir):
    """Get root password file path."""
    return os.path.join(root_dir, "config", "mysql", MYSQL_PASSWORD_FILE)


def generate_strong_password(length=MYSQL_PASSWORD_LENGTH):
    """Generate random strong password.

    使用相对安全的字符集：大小写字母、数字、@#$*+-_=
    避免 & % ^ ! " ' \\ / 空格 ; < > | ( ) 等 Windows 命令行高风险字符。
    """
    alphabet = string.ascii_letters + string.digits + "@#$*+-_="
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        has_lower = any(c.islower() for c in pwd)
        has_upper = any(c.isupper() for c in pwd)
        has_digit = any(c.isdigit() for c in pwd)
        has_special = any(c in "@#$*+-_=" for c in pwd)
        if has_lower and has_upper and has_digit and has_special:
            return pwd


def parse_mysql_temp_password(init_log):
    """Parse temporary password from MySQL init log.

    支持多种格式：
    - A temporary password is generated for root@localhost: abcDEF)1)w
    - temporary password is generated for root@localhost: abc
    - root@localhost: abc
    按行匹配 root@localhost: 后第一个非空白串。
    """
    for line in init_log.split("\n"):
        m = re.search(r"root@localhost:\s*(\S+)", line)
        if m:
            return m.group(1).strip()
    return None


def save_root_password(root_dir, password, host=None, port=None):
    """Save root password to config/mysql/root-password.txt.

    注意：此函数仅在初始化时由 Panel 通过临时文件传递密码，
    不再作为长期持久化存储。保留函数签名以兼容旧逻辑。
    root-password.txt 保留在 config/mysql/ 下，属于辅助文件，非组件活跃配置。
    """
    pwd_file = get_password_file_path(root_dir)
    os.makedirs(os.path.dirname(pwd_file), exist_ok=True)
    content = "# WNMP MySQL Root Password\n"
    content += "# Auto-generated - DO NOT share this file\n"
    content += "# generated_at: {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"))
    content += "# host: {}\n".format(host or "127.0.0.1")
    content += "# port: {}\n".format(port or "3306")
    content += "# user: root\n"
    content += "password={}\n".format(password)
    with open(pwd_file, "w", encoding="utf-8") as f:
        f.write(content)
    return pwd_file


def load_root_password(root_dir):
    """Load root password from config/mysql/root-password.txt."""
    pwd_file = get_password_file_path(root_dir)
    if not os.path.isfile(pwd_file):
        return None
    try:
        with open(pwd_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("password="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def check_mysql_data_dir_state(mysql_data_dir, logger=None):
    """Check MySQL data directory state. Returns: not_exists/empty/initialized/dirty."""
    if not os.path.isdir(mysql_data_dir):
        return "not_exists"
    sys_dir = os.path.join(mysql_data_dir, "mysql")
    ibdata = os.path.join(mysql_data_dir, "ibdata1")
    if os.path.isdir(sys_dir) or os.path.isfile(ibdata):
        return "initialized"
    if os.listdir(mysql_data_dir):
        return "dirty"
    return "empty"


def is_mysql_data_initialized(mysql_data_dir, logger=None):
    """Check if MySQL data directory is initialized."""
    return check_mysql_data_dir_state(mysql_data_dir) == "initialized"


def ensure_tmp_dir(root_dir, logger=None):
    """Ensure tmp directory exists."""
    tmp_dir = os.path.join(root_dir, "tmp")
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        with open(os.path.join(tmp_dir, ".test"), "w") as f:
            f.write("test")
        os.remove(os.path.join(tmp_dir, ".test"))
        return True, tmp_dir
    except Exception:
        return False, None


def _get_mysql_exes(root_dir):
    """Return dict of MySQL binary paths."""
    return {
        "mysqld": os.path.join(root_dir, "bin", "mysql", "bin", "mysqld.exe"),
        "mysql": os.path.join(root_dir, "bin", "mysql", "bin", "mysql.exe"),
        "mysqladmin": os.path.join(root_dir, "bin", "mysql", "bin", "mysqladmin.exe"),
        # 路径收敛：通过统一路径模块获取 my.ini 路径
        "my_ini": get_mysql_ini_path(root_dir),
    }


def _read_error_log_incremental(root_dir, before_size, logger):
    """读取 error.log 新增内容，避免读取旧密码。"""
    error_log = os.path.join(root_dir, "logs", "mysql", "error.log")
    if not os.path.isfile(error_log):
        return ""
    try:
        current_size = os.path.getsize(error_log)
        if current_size <= before_size:
            return ""
        with open(error_log, "r", encoding="utf-8", errors="replace") as f:
            f.seek(before_size)
            return f.read()
    except Exception as e:
        from runtime.wnmp_log import log_warn
        log_warn(logger, "Read error.log incremental failed: " + str(e))
        return ""


def initialize_mysql_secure(root_dir, cfg, logger, panel_result_file=None):
    """Secure MySQL init: --initialize -> parse temp pwd -> set strong password -> save.

    事务式流程：任一步失败都明确日志，data/mysql 可能处于半初始化状态。
    panel_result_file: Panel 临时结果文件路径，ALTER USER 成功后立即写入密码，
                       即使后续步骤失败，Panel 也能获取已生成的 root 密码。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn

    exes = _get_mysql_exes(root_dir)
    mysql_data_dir = resolve_path(root_dir, cfg.get("MYSQL_DATA_DIR", "./data/mysql"))
    # 初始化时从 runtime.ini 读取端口（首次初始化，my.ini 可能尚未生成）
    mysql_host = cfg.get("MYSQL_HOST", "127.0.0.1")
    mysql_port = cfg.get("MYSQL_PORT", "3306")
    log_dir = os.path.join(root_dir, "logs", "mysql")
    os.makedirs(log_dir, exist_ok=True)

    if not os.path.isfile(exes["mysql"]):
        log_error(logger, "mysql.exe not found: bin/mysql/bin/mysql.exe")
        return False, "mysql.exe missing"

    if not os.path.isfile(exes["mysqladmin"]):
        log_error(logger, "mysqladmin.exe not found: bin/mysql/bin/mysqladmin.exe")
        return False, "mysqladmin.exe missing"

    os.makedirs(mysql_data_dir, exist_ok=True)

    # 记录 error.log 读取边界，避免读取旧密码
    error_log = os.path.join(log_dir, "error.log")
    before_size = 0
    if os.path.isfile(error_log):
        before_size = os.path.getsize(error_log)

    # 步骤 1: mysqld --initialize
    cmd = [
        exes["mysqld"],
        "--defaults-file=" + exes["my_ini"],
        "--initialize"
    ]

    log_info(logger, "MySQL secure initialization (step 1/7: --initialize)...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW, timeout=120)
        init_out = result.stdout + result.stderr

        # 写入 init.log 保存本次初始化输出
        with open(os.path.join(log_dir, "init.log"), "a", encoding="utf-8") as f:
            f.write("=== MySQL Initialize Output ===\n")
            f.write(init_out)

        if result.returncode != 0:
            log_error(logger, "MySQL initialization failed (returncode={})".format(result.returncode))
            log_error(logger, "data/mysql may be in half-initialized state, clear it or change MYSQL_DATA_DIR")
            return False, "Init failed"
    except subprocess.TimeoutExpired:
        log_error(logger, "MySQL initialization timeout")
        log_error(logger, "data/mysql may be in half-initialized state, clear it or change MYSQL_DATA_DIR")
        return False, "Timeout"
    except Exception as e:
        log_error(logger, "Init error: " + str(e))
        log_error(logger, "data/mysql may be in half-initialized state, clear it or change MYSQL_DATA_DIR")
        return False, str(e)

    # 步骤 2: 解析临时密码（合并 stdout、stderr、error.log 新增段）
    log_info(logger, "MySQL secure initialization (step 2/7: parse temp password)...")
    error_log_incremental = _read_error_log_incremental(root_dir, before_size, logger)
    combined_log = init_out + "\n" + error_log_incremental

    # 同时写入 init.log 方便排查
    with open(os.path.join(log_dir, "init.log"), "a", encoding="utf-8") as f:
        f.write("\n=== Error Log Incremental ===\n")
        f.write(error_log_incremental)

    temp_pwd = parse_mysql_temp_password(combined_log)
    if not temp_pwd:
        log_error(logger, "Cannot parse temporary password from init output or error.log")
        log_error(logger, "data/mysql may be in half-initialized state, clear it or change MYSQL_DATA_DIR")
        return False, "Parse temp password failed"

    # 步骤 3: 启动临时 MySQL
    log_info(logger, "MySQL secure initialization (step 3/7: start temp MySQL)...")
    init_log = os.path.join(root_dir, "logs", "mysql", "init.log")
    os.makedirs(os.path.dirname(init_log), exist_ok=True)
    cmd_start = [exes["mysqld"], "--defaults-file=" + exes["my_ini"]]
    proc = subprocess.Popen(cmd_start, cwd=root_dir, creationflags=subprocess.CREATE_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    temp_pid = proc.pid

    if not wait_for_port_open(mysql_host, int(mysql_port), timeout=60, logger=logger):
        log_error(logger, "Temp MySQL start timeout (60s)")
        kill_process(temp_pid, timeout=5)
        log_error(logger, "data/mysql may be in half-initialized state, clear it or change MYSQL_DATA_DIR")
        return False, "Temp MySQL start timeout"

    # 步骤 4: 生成强密码并执行 ALTER USER
    log_info(logger, "MySQL secure initialization (step 4/7: generate strong password)...")
    strong_pwd = generate_strong_password()

    # 对 SQL 中的密码做单引号转义
    escaped_pwd = strong_pwd.replace("'", "''")
    sql = "ALTER USER 'root'@'localhost' IDENTIFIED BY '{}'; FLUSH PRIVILEGES;".format(escaped_pwd)

    cmd_alter = [
        exes["mysql"],
        "--protocol=tcp",
        "--host=" + mysql_host,
        "--port=" + mysql_port,
        "-u", "root",
        "--password=" + temp_pwd,
        "--connect-expired-password",
        "-e",
        sql
    ]

    log_info(logger, "MySQL secure initialization (step 5/7: ALTER USER)...")
    try:
        r = subprocess.run(cmd_alter, capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        if r.returncode != 0:
            log_error(logger, "ALTER USER failed: " + (r.stderr or r.stdout))
            # 停止临时 MySQL
            _shutdown_temp_mysql(exes, mysql_host, mysql_port, strong_pwd, temp_pid, root_dir, cfg, logger)
            log_error(logger, "data/mysql may be in half-initialized state, clear it or change MYSQL_DATA_DIR")
            return False, "ALTER USER failed"
    except Exception as e:
        log_error(logger, "ALTER USER error: " + str(e))
        _shutdown_temp_mysql(exes, mysql_host, mysql_port, strong_pwd, temp_pid, root_dir, cfg, logger)
        log_error(logger, "data/mysql may be in half-initialized state, clear it or change MYSQL_DATA_DIR")
        return False, "ALTER USER error"

    # 步骤 6: 密码已生成，立即写入 panel-result-file（不等后续步骤）
    # 即使后续启动失败，Panel 也能获取已生成的 root 密码
    log_info(logger, "MySQL secure initialization (step 6/7: write password to panel-result-file)")
    if panel_result_file:
        try:
            import json as _json
            os.makedirs(os.path.dirname(panel_result_file), exist_ok=True)
            with open(panel_result_file, "w", encoding="utf-8") as _f:
                _json.dump({"mysql_root_password": strong_pwd}, _f)
            log_info(logger, "MySQL root password written to panel result file")
        except Exception:
            # 写入失败不记录明文密码，不影响后续流程
            log_warn(logger, "Failed to write panel result file (password not logged)")

    # 步骤 7: 停止临时 MySQL（等待完全退出和文件释放），检查返回值
    log_info(logger, "MySQL secure initialization (step 7/7: stop temp MySQL)...")
    shutdown_ok = _shutdown_temp_mysql(exes, mysql_host, mysql_port, strong_pwd, temp_pid, root_dir, cfg, logger)
    if not shutdown_ok:
        log_error(logger, "Temp MySQL did not shutdown completely, ibdata1 may still be locked")
        return False, "Temp MySQL shutdown incomplete"

    log_info(logger, "MySQL secure initialization completed, root password available via panel-result-file")
    return True, strong_pwd


def _wait_for_file_released(file_path, timeout=30, logger=None):
    """等待文件被释放，用于检测 ibdata1 是否仍被 mysqld 占用。

    Windows 优先使用 ctypes CreateFileW 独占打开检测（dwShareMode=0），
    无法使用 ctypes 时 fallback 到 open("ab")。
    不修改文件内容，只做打开/关闭测试。
    """
    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # 优先使用 ctypes CreateFileW 独占打开检测
            try:
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.CreateFileW(
                    file_path,
                    GENERIC_READ | GENERIC_WRITE,
                    0,  # dwShareMode=0 独占
                    None,
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    None
                )
                if handle != INVALID_HANDLE_VALUE:
                    kernel32.CloseHandle(handle)
                    return True
            except Exception:
                pass

            # fallback: open("ab") 追加模式
            try:
                f = open(file_path, "ab")
                f.close()
                return True
            except (PermissionError, OSError, IOError):
                pass

            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "Waiting for {} to be released...".format(os.path.basename(file_path)))
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return False


def _shutdown_temp_mysql(exes, mysql_host, mysql_port, password, pid, root_dir, cfg, logger):
    """停止临时 MySQL：优先 mysqladmin，失败后 PID 兜底。

    必须等待临时 mysqld 完全退出、端口关闭、ibdata1 释放后才返回 True。
    返回 False 表示关闭未完成，调用方不得继续启动正式 MySQL。
    """
    from runtime.wnmp_log import log_info, log_warn, log_error

    shutdown_requested = False

    # 优先使用 mysqladmin shutdown
    if os.path.isfile(exes["mysqladmin"]):
        try:
            cmd = [
                exes["mysqladmin"],
                "--protocol=tcp",
                "--host=" + mysql_host,
                "--port=" + mysql_port,
                "-u", "root",
                "--password=" + password,
                "shutdown"
            ]
            r = subprocess.run(cmd, capture_output=True, text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
            if r.returncode == 0:
                log_info(logger, "Temp MySQL shutdown requested via mysqladmin")
                shutdown_requested = True
            else:
                log_warn(logger, "mysqladmin shutdown failed (rc={}), fallback to PID".format(r.returncode))
        except Exception as e:
            log_warn(logger, "mysqladmin error: " + str(e) + ", fallback to PID")

    # PID 兜底：如果 mysqladmin 没有成功请求 shutdown
    if not shutdown_requested:
        kill_process(pid, timeout=5)

    # 等待临时 mysqld 进程完全退出
    log_info(logger, "Waiting for temp mysqld (pid={}) to exit...".format(pid))
    process_exited = False
    exit_wait_start = time.time()
    exit_wait_timeout = 30
    while time.time() - exit_wait_start < exit_wait_timeout:
        if is_process_running(pid) is False:
            process_exited = True
            break
        time.sleep(0.5)

    if not process_exited:
        log_warn(logger, "Temp mysqld (pid={}) did not exit within {}s, forcing kill".format(pid, exit_wait_timeout))
        kill_process(pid, timeout=5)
        for _ in range(10):
            if is_process_running(pid) is False:
                process_exited = True
                break
            time.sleep(0.5)

    if not process_exited:
        log_error(logger, "Temp MySQL did not shutdown completely, process still alive")
        return False

    log_info(logger, "Temp mysqld process exited")

    # 等待端口关闭
    port_closed = wait_for_port_close(mysql_host, int(mysql_port), timeout=10, logger=logger)
    if not port_closed:
        log_warn(logger, "Temp MySQL port {} still open after shutdown".format(mysql_port))
    else:
        log_info(logger, "Temp MySQL port {} closed".format(mysql_port))

    # 等待 ibdata1 文件释放（Windows 上文件锁可能在进程退出后短暂残留）
    mysql_data_dir = resolve_path(root_dir, cfg.get("MYSQL_DATA_DIR", "./data/mysql"))
    ibdata1_path = os.path.join(mysql_data_dir, "ibdata1")
    if os.path.isfile(ibdata1_path):
        log_info(logger, "Checking ibdata1 file release...")
        file_released = _wait_for_file_released(ibdata1_path, timeout=15, logger=logger)
        if file_released:
            log_info(logger, "ibdata1 released")
            log_info(logger, "Temp MySQL shutdown completed, data files released")
            return True
        else:
            log_error(logger, "Temp MySQL did not shutdown completely, ibdata1 may still be locked")
            return False
    else:
        log_info(logger, "Temp MySQL shutdown completed (ibdata1 not found, skipped file check)")
        return True


def _wait_mysql_port_with_retry(mysql_host, mysql_port, mysqld_pid, timeout, logger, root_dir=None):
    """等待 MySQL 端口开放，定期检测 mysqld 是否提前退出。

    增强日志：超时时记录 host/port/timeout/pid/进程是否存活。
    如果 mysqld 提前退出，立即返回 False 并检查 error log 中的关键错误。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn

    check_interval = 2  # 每 2 秒检查一次进程是否仍在
    elapsed = 0
    while elapsed < timeout:
        # 检查端口
        if is_port_listening(mysql_host, int(mysql_port)):
            return True

        # 检查 mysqld 进程是否仍在运行
        if is_process_running(mysqld_pid) is False:
            # mysqld 已退出，检查 error log 中的关键错误
            error_hint = ""
            if root_dir:
                error_hint = _check_mysql_error_log_hints(root_dir)
            log_error(logger, "mysqld (pid={}) exited before port {} opened. {}".format(
                mysqld_pid, mysql_port, error_hint))
            return False

        time.sleep(check_interval)
        elapsed += check_interval

    # 超时后最后检查一次端口
    if is_port_listening(mysql_host, int(mysql_port)):
        return True

    # 超时后检查 mysqld 进程是否仍在运行
    pid_alive = is_process_running(mysqld_pid)
    if pid_alive is not False:
        # 进程仍在但端口未开，额外等 2 秒复查
        log_warn(logger, "MySQL port {} timeout ({}s), but mysqld (pid={}) still alive, retrying in 2s...".format(
            mysql_port, timeout, mysqld_pid))
        time.sleep(2)
        if is_port_listening(mysql_host, int(mysql_port)):
            log_info(logger, "MySQL port {} open after retry".format(mysql_port))
            return True
        log_error(logger, "MySQL port {} still not open after retry, mysqld (pid={}) alive but not listening. "
                  "Check logs/mysql/error.log".format(mysql_port, mysqld_pid))
    else:
        log_error(logger, "MySQL port {} timeout ({}s), mysqld (pid={}) has exited. "
                  "Check logs/mysql/error.log".format(mysql_port, timeout, mysqld_pid))

    return False


def _check_mysql_error_log_hints(root_dir):
    """检查 MySQL error log 中的关键错误，返回提示字符串。"""
    error_log = os.path.join(root_dir, "logs", "mysql", "error.log")
    if not os.path.isfile(error_log):
        return "Check logs/mysql/error.log"

    try:
        # 只读取最后 10KB
        with open(error_log, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 10240)
            f.seek(size - read_size)
            tail = f.read().decode("utf-8", errors="replace")

        hints = []
        if "ibdata1" in tail and "writable" in tail.lower():
            hints.append("数据文件可能仍被上一次 mysqld 占用或权限不足")
        if "ready for connections" in tail.lower():
            hints.append("")  # 已有 ready for connections，说明之前启动过
        if "Shutdown complete" in tail:
            hints.append("")  # 已正常关闭

        if hints:
            return "; ".join(h for h in hints if h)
        return "Check logs/mysql/error.log"
    except Exception:
        return "Check logs/mysql/error.log"


def _confirm_mysql_listener(root_dir, mysql_host, mysql_port, timeout, proc_pid, logger):
    """等待 MySQL 端口开放并确认 listener path 属于当前 rootDir。

    确认逻辑：
      1. listener path 精确匹配 mysqld.exe → confirmed_by_path
      2. path=None 但 proc_pid 存活 + 端口已监听 + listener_pid==proc_pid
         或进程名匹配 mysqld.exe → confirmed_by_combination
    确认成功后写入 runtime/pids/mysqld.pid 作为缓存。
    返回 confirmed_pid 或 None。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn
    from runtime.wnmp_process import check_listener_ownership

    mysqld_exe = os.path.join(root_dir, "bin", "mysql", "bin", "mysqld.exe")
    pid_dir = os.path.join(root_dir, "runtime", "pids")

    log_info(logger, "Waiting for MySQL port {}:{} to be confirmed...".format(
        mysql_host, mysql_port))

    start_time = time.time()
    confirmed_pid = None

    while time.time() - start_time < timeout:
        # 先检查 mysqld 进程是否仍在运行
        if is_process_running(proc_pid) is False:
            log_error(logger, "mysqld (pid={}) exited before port {} opened".format(proc_pid, mysql_port))
            return None

        # 检查端口是否开放
        if not is_port_listening(mysql_host, int(mysql_port)):
            time.sleep(2)
            continue

        # 端口已开放，查询 listener path 归属
        ownership = check_listener_ownership(int(mysql_port), mysqld_exe,
                                              host=mysql_host, root_dir=root_dir,
                                              timeout=2, logger=logger)
        log_info(logger, "  Port {}:{} listener: status={} pid={} path={} is_ours={} path_reason={}".format(
            mysql_host, mysql_port, ownership["status"],
            ownership.get("pid"), ownership.get("path"), ownership.get("is_ours"),
            ownership.get("path_reason")))

        if ownership["status"] == "running":
            confirmed_pid = ownership["pid"]
            # 写入 PID 缓存
            write_pid_file(pid_dir, "mysqld.pid", confirmed_pid)
            log_info(logger, "MySQL started and confirmed by listener path: PID={}".format(confirmed_pid))
            return confirmed_pid
        elif ownership["status"] == "external":
            log_error(logger, "Port {}:{} is occupied by external process: path={}".format(
                mysql_host, mysql_port, ownership.get("path")))
            return None
        elif ownership["status"] == "unknown":
            # 端口已开放但 path=None，尝试组合确认
            listener_pid = ownership.get("pid")
            if listener_pid is not None:
                # 组合确认：proc_pid 存活 + 端口已监听 + listener_pid==proc_pid 或进程名匹配
                launched_alive = is_process_running(proc_pid) is not False
                pid_match = (listener_pid == proc_pid)
                proc_name = get_process_name(listener_pid, timeout=2)
                proc_name_match = (proc_name and proc_name.lower() in ("mysqld.exe", "mysqld"))

                if launched_alive and (pid_match or proc_name_match):
                    confirmed_pid = listener_pid
                    write_pid_file(pid_dir, "mysqld.pid", confirmed_pid)
                    log_info(logger, "MySQL started and confirmed by combination: PID={} "
                             "pid_match={} proc_name={} name_match={}".format(
                                 confirmed_pid, pid_match, proc_name, proc_name_match))
                    log_info(logger, "  当前系统无法读取监听进程路径，已使用本次启动 PID 与端口监听状态进行确认")
                    return confirmed_pid

            # 组合确认未通过，继续等待重试
            log_warn(logger, "Port {}:{} open but cannot confirm ownership, retrying...".format(
                mysql_host, mysql_port))
            time.sleep(2)
            continue
        else:
            # stopped: 端口可能刚关闭，继续等待
            time.sleep(2)
            continue

    # 超时：检查是否有端口开放但无法确认归属
    if is_port_listening(mysql_host, int(mysql_port)):
        log_error(logger, "MySQL port is open but cannot confirm ownership")
        return None

    log_error(logger, "MySQL failed to start, port {}:{} not listening after {}s".format(
        mysql_host, mysql_port, timeout))
    return None


def start_mysql(root_dir, cfg, logger, panel_result_file=None):
    """Start MySQL.

    MySQL 端口优先从 bin/mysql/my.ini 解析，解析失败回退到 runtime.ini。
    panel_result_file: Panel 临时结果文件路径，安全初始化生成密码后立即写入，
                       即使后续端口等待超时，Panel 也能获取已生成的 root 密码。
    """
    from runtime.wnmp_log import log_info, log_error, log_warn
    from runtime.wnmp_config import get_effective_mysql_port

    exes = _get_mysql_exes(root_dir)
    mysql_data_dir = resolve_path(root_dir, cfg.get("MYSQL_DATA_DIR", "./data/mysql"))
    pid_dir = os.path.join(root_dir, "runtime", "pids")
    ensure_tmp_dir(root_dir, logger)
    state = check_mysql_data_dir_state(mysql_data_dir, logger)

    # 读取 MySQL 启动超时配置
    from runtime import wnmp_config
    mysql_start_timeout = wnmp_config.get_int(cfg, "MYSQL_START_TIMEOUT", 90)

    # 优先从 my.ini 解析端口
    mysql_host = cfg.get("MYSQL_HOST", "127.0.0.1")
    mysql_port = get_effective_mysql_port(root_dir, cfg)

    if state == "dirty":
        log_error(logger, "MySQL data dir dirty, clear or change MYSQL_DATA_DIR")
        return False, "Dirty data dir"

    if state == "initialized":
        # 已初始化目录：不再检查 root-password.txt，直接启动
        log_info(logger, "MySQL data directory is initialized, starting normally")
        # 正常启动
        log_file = os.path.join(root_dir, "logs", "mysql", "runtime-mysqld.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        cmd = [exes["mysqld"], "--defaults-file=" + exes["my_ini"]]
        proc = start_process(cmd, cwd=root_dir, logger=logger, stdout_file=log_file, stderr_file=log_file)
        if not proc:
            return False, "Start process failed"

        # 基于 listener path 确认启动成功，PID 文件作为缓存
        confirmed = _confirm_mysql_listener(root_dir, mysql_host, mysql_port,
                                             mysql_start_timeout, proc.pid, logger)
        if confirmed:
            # 启动成功后清除 config_dirty 标记
            try:
                from runtime.wnmp_state import mark_component_config_applied
                mark_component_config_applied(root_dir, "mysql")
            except Exception:
                pass
            return True, confirmed
        else:
            # 确认失败：检查 mysqld 是否仍在运行
            pid_alive = is_process_running(proc.pid)
            if pid_alive is not False:
                return False, {"error": "MySQL port timeout, mysqld still alive"}
            else:
                error_hint = _check_mysql_error_log_hints(root_dir)
                return False, {"error": "mysqld exited before port opened, check logs/mysql/error.log", "hint": error_hint}

    # state == empty/not_exists: 进入安全初始化
    if not os.path.isfile(exes["mysql"]):
        log_error(logger, "mysql.exe not found, cannot secure init")
        return False, "mysql.exe missing"
    # 安全初始化时传入 panel_result_file，ALTER USER 成功后立即写入密码
    ok, init_result = initialize_mysql_secure(root_dir, cfg, logger, panel_result_file=panel_result_file)
    if not ok:
        return False, "Secure init failed"

    # init_result 是初始化时生成的 root 密码，需要传递给调用方
    mysql_init_password = init_result if isinstance(init_result, str) else None

    # Mark MySQL as initialized in state.json
    from runtime.wnmp_state import mark_mysql_initialized
    mark_mysql_initialized(root_dir)

    # 初始化成功后启动 MySQL
    cmd = [exes["mysqld"], "--defaults-file=" + exes["my_ini"]]
    log_file = os.path.join(root_dir, "logs", "mysql", "runtime-mysqld.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    proc = start_process(cmd, cwd=root_dir, logger=logger, stdout_file=log_file, stderr_file=log_file)
    if not proc:
        # 进程启动失败，但密码已写入 panel_result_file，返回含密码的失败信息
        if mysql_init_password:
            return False, {"error": "Start process failed", "mysql_init_password": mysql_init_password}
        return False, "Start process failed"

    # 基于 listener path 确认启动成功，PID 文件作为缓存
    confirmed = _confirm_mysql_listener(root_dir, mysql_host, mysql_port,
                                         mysql_start_timeout, proc.pid, logger)
    if confirmed:
        log_info(logger, "MySQL port {} listening and confirmed by listener path".format(mysql_port))
        # 启动成功后清除 config_dirty 标记
        try:
            from runtime.wnmp_state import mark_component_config_applied
            mark_component_config_applied(root_dir, "mysql")
        except Exception:
            pass
        # 初始化成功时返回密码信息，供 Panel 一次性传递
        if mysql_init_password:
            return True, {"pid": confirmed, "mysql_init_password": mysql_init_password}
        return True, confirmed
    else:
        # 确认失败：区分 mysqld 提前退出和端口超时
        pid_alive = is_process_running(proc.pid)
        error_msg = "MySQL port timeout, mysqld still alive" if pid_alive is not False else "mysqld exited before port opened, check logs/mysql/error.log"
        hint = _check_mysql_error_log_hints(root_dir) if pid_alive is False else ""
        if mysql_init_password:
            return False, {"error": error_msg, "mysql_init_password": mysql_init_password, "hint": hint}
        return False, {"error": error_msg, "hint": hint}


def stop_mysql(root_dir, cfg, logger):
    """Stop MySQL。先尝试 mysqladmin，再用 listener PID。

    不依赖 root-password.txt，失败后按 my.ini 端口 listener PID/path 停止当前项目 mysqld。
    停止安全边界：
      - 允许停止 recorded_pid（进程名匹配 mysqld.exe/mysqld）
      - 允许停止 listener path 精确匹配 mysqld.exe 的进程
      - path=None 且不是 recorded_pid 的未知 listener，禁止 kill
      - 外部进程不允许 kill
    停止后等待实际端口关闭。
    """
    from runtime.wnmp_log import log_info, log_warn, log_error
    from runtime.wnmp_config import get_effective_mysql_port

    exes = _get_mysql_exes(root_dir)
    pid_dir = os.path.join(root_dir, "runtime", "pids")
    mysqld_exe = os.path.join(root_dir, "bin", "mysql", "bin", "mysqld.exe")
    pid = read_pid_file(pid_dir, "mysqld.pid")
    # 从 my.ini 解析端口
    mysql_host = cfg.get("MYSQL_HOST", "127.0.0.1")
    mysql_port = get_effective_mysql_port(root_dir, cfg)
    # 尝试加载密码，缺失时跳过 mysqladmin
    pwd = load_root_password(root_dir)

    # 权限上下文日志
    current_admin = is_current_admin()
    log_info(logger, "Permission context: current_process_is_admin={}".format(current_admin))

    stopped_pids = []

    # 步骤 0：查询配置端口所有 listener PID/path/local_address
    log_info(logger, "Querying {}:{} listeners...".format(mysql_host, mysql_port))
    listeners = get_listening_processes(int(mysql_port), host=mysql_host, root_dir=root_dir,
                                        logger=logger, expected_path=mysqld_exe)
    # 记录所有 listener 信息
    for ln in listeners:
        ln_pid = ln.get("pid")
        ln_path = ln.get("path")
        ln_addr = ln.get("local_address", "?")
        ln_is_expected = ln.get("is_expected")
        ln_is_in_root = ln.get("is_in_root")
        ln_owner = "unknown"
        if ln_pid:
            ln_owner = "SYSTEM" if is_system_process(ln_pid) else "user"
        log_info(logger, "  Listener: PID={} local={} path={} is_expected={} is_in_root={} owner={}".format(
            ln_pid, ln_addr, ln_path, ln_is_expected, ln_is_in_root, ln_owner))

    # 遍历所有 listener，优先选择 is_expected=True 的 PID
    expected_listener = None
    in_root_listener = None  # 在 rootDir 下但不是目标 exe
    external_listener = None
    unknown_listener = None

    for ln in listeners:
        if ln.get("is_expected") is True:
            expected_listener = ln
            break  # 找到精确匹配，优先使用
        elif ln.get("is_in_root") is True and ln.get("is_expected") is False:
            in_root_listener = ln
        elif ln.get("is_expected") is False:
            external_listener = ln
        elif ln.get("is_expected") is None:
            unknown_listener = ln

    # 确定要停止的目标 listener
    target_listener = expected_listener or in_root_listener or unknown_listener
    listener_pid = target_listener.get("pid") if target_listener else None
    listener_path = target_listener.get("path") if target_listener else None
    listener_is_expected = target_listener.get("is_expected") if target_listener else None

    # 如果端口被外部程序占用，不能误杀
    if external_listener and not expected_listener and not in_root_listener:
        log_warn(logger, "Port {}:{} is occupied by external process: path={}".format(
            mysql_host, mysql_port, external_listener.get("path")))
        return False, "external"

    # 如果只有 unknown listener（path=None），检查是否为 recorded_pid
    if unknown_listener and not expected_listener and not in_root_listener:
        unknown_pid = unknown_listener.get("pid")
        if unknown_pid and pid and unknown_pid == pid:
            # unknown listener 是 recorded_pid，允许停止
            proc_name = get_process_name(unknown_pid, timeout=2)
            if proc_name and proc_name.lower() in ("mysqld.exe", "mysqld"):
                log_info(logger, "  Unknown listener PID={} matches recorded_pid and process name mysqld, allowing stop".format(
                    unknown_pid))
                target_listener = unknown_listener
                listener_pid = unknown_pid
                listener_is_expected = None  # path=None 但进程名匹配
            else:
                log_warn(logger, "Port {}:{} has unknown listener PID={} but process name '{}' does not match mysqld".format(
                    mysql_host, mysql_port, unknown_pid, proc_name))
                return False, "unknown_external_listener: 端口被未知进程占用，为避免误杀已跳过操作"
        else:
            # path=None 且不是 recorded_pid 的未知 listener，禁止 kill
            log_warn(logger, "Port {}:{} has unknown listener PID={} (recorded_pid={}), cannot confirm ownership".format(
                mysql_host, mysql_port, unknown_pid, pid))
            return False, "unknown_external_listener: 端口被未知进程占用，为避免误杀已跳过操作"

    # 记录目标进程权限信息
    listener_owner = "unknown"
    if listener_pid:
        listener_is_sys = is_system_process(listener_pid)
        listener_owner = "SYSTEM" if listener_is_sys else "user"
    log_info(logger, "Permission context: target_pid={} target_path={} target_owner={} current_admin={}".format(
        listener_pid, listener_path, listener_owner, current_admin))

    # SYSTEM 进程提前返回
    if listener_is_expected is True and listener_pid and listener_owner == "SYSTEM" and not current_admin:
        msg = "该组件由 SYSTEM/高权限启动，停止需要以管理员权限运行 WNMPanel.exe"
        log_info(logger, "Permission denied: " + msg)
        return False, msg

    # 步骤 1：mysqladmin shutdown（有密码时优先优雅停止）
    if os.path.isfile(exes["mysqladmin"]) and pwd:
        log_info(logger, "Stopping MySQL via mysqladmin ({}:{})...".format(mysql_host, mysql_port))
        try:
            cmd = [exes["mysqladmin"],
                  "--protocol=tcp",
                  "--host=" + mysql_host,
                  "--port=" + str(mysql_port),
                  "-u", "root",
                  "--password=" + pwd,
                  "shutdown"]
            r = subprocess.run(cmd, capture_output=True, text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
            log_info(logger, "  mysqladmin rc={} stdout={} stderr={}".format(
                r.returncode, r.stdout.strip()[:200], r.stderr.strip()[:200]))
            if r.returncode == 0:
                log_info(logger, "MySQL stopped via mysqladmin, waiting for port release...")
                if wait_for_port_close(mysql_host, int(mysql_port), timeout=10, logger=logger):
                    remove_pid_file(pid_dir, "mysqld.pid")
                    log_info(logger, "MySQL stopped successfully via mysqladmin")
                    return True, stopped_pids
                log_warn(logger, "mysqladmin returned 0 but port still listening, continuing...")
            else:
                log_warn(logger, "mysqladmin failed (rc={} stderr={}), continuing...".format(
                    r.returncode, r.stderr.strip()[:200]))
        except Exception as e:
            log_warn(logger, "mysqladmin error: " + str(e) + ", continuing...")
    elif not pwd:
        log_info(logger, "No MySQL password available, skipping mysqladmin, using listener PID/path fallback")

    # 步骤 2：listener PID 优先停止（is_expected=True 表示本项目 mysqld.exe）
    if listener_is_expected is True and listener_pid:
        log_info(logger, "  Port {}:{} listener is project mysqld PID={}, terminating...".format(
            mysql_host, mysql_port, listener_pid))
        kill_process(listener_pid, timeout=10, logger=logger)
        stopped_pids.append(listener_pid)
        # 修正 pid 文件（如果 listener PID 与 pid 文件不一致）
        if pid and pid != listener_pid:
            log_info(logger, "  Listener PID {} differs from pid file PID {}, updating pid file".format(
                listener_pid, pid))
            write_pid_file(pid_dir, "mysqld.pid", listener_pid)
    elif listener_pid and listener_is_expected is None and pid and listener_pid == pid:
        # path=None 但 PID 匹配 recorded_pid 且进程名匹配，允许停止
        log_info(logger, "  Port {}:{} listener PID={} matches recorded_pid (path=None, confirmed by combination), terminating...".format(
            mysql_host, mysql_port, listener_pid))
        kill_process(listener_pid, timeout=10, logger=logger)
        stopped_pids.append(listener_pid)

    # 步骤 3：按 PID 文件终止（辅助路径）
    if pid and is_process_running(pid) is not False:
        if pid not in stopped_pids:
            # 验证 recorded_pid 的进程名
            proc_name = get_process_name(pid, timeout=2)
            if proc_name and proc_name.lower() in ("mysqld.exe", "mysqld"):
                log_info(logger, "Stopping MySQL via recorded PID {} (process name confirmed)...".format(pid))
                kill_process(pid, timeout=10, logger=logger)
                stopped_pids.append(pid)
            else:
                log_warn(logger, "Recorded PID {} process name '{}' does not match mysqld, skipping".format(
                    pid, proc_name))

    if wait_for_port_close(mysql_host, int(mysql_port), timeout=10, logger=logger):
        remove_pid_file(pid_dir, "mysqld.pid")
        log_info(logger, "MySQL stopped successfully via listener/PID")
        return True, stopped_pids

    # 步骤 4：优先快速扫描本项目 mysqld.exe 进程并终止
    log_warn(logger, "PID stop did not release port, finding project MySQL processes...")
    # 优先使用快速扫描（WinAPI+tasklist），避免 PowerShell 慢兜底
    from runtime.wnmp_process import find_processes_by_executable_path
    tool_pids = find_processes_by_executable_path(mysqld_exe, timeout=2, logger=logger, fast_mode=True)
    if not tool_pids:
        # 快速扫描无结果，使用完整路径扫描（含 PowerShell 兜底）
        tool_pids = find_processes_by_path(root_dir, "mysqld.exe", logger)
    if tool_pids:
        log_info(logger, "  Found {} project MySQL processes: {}".format(len(tool_pids), tool_pids))
        terminated, failed = terminate_pids(tool_pids, timeout=10, tree=True, logger=logger)
        stopped_pids.extend(tool_pids)
        log_info(logger, "  Terminated: {}, failed: {}".format(terminated, failed))

    if wait_for_port_close(mysql_host, int(mysql_port), timeout=10, logger=logger):
        remove_pid_file(pid_dir, "mysqld.pid")
        log_info(logger, "MySQL stopped successfully via path fallback")
        return True, stopped_pids

    # 步骤 5：识别所有监听进程归属
    if is_port_listening(mysql_host, int(mysql_port)):
        listeners = get_listening_processes(int(mysql_port), host=mysql_host, root_dir=root_dir,
                                            logger=logger, expected_path=mysqld_exe)
        details = []
        system_hint = False
        for ln in listeners:
            ln_pid = ln.get("pid")
            ln_addr = ln.get("local_address", "?")
            ln_path = ln.get("path") or "unknown"
            if ln.get("is_expected") is True:
                detail = "local={} (project mysqld PID={} path={}".format(ln_addr, ln_pid, ln_path)
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
        msg = "port {} still occupied: ".format(mysql_port) + "; ".join(details)
        if system_hint:
            msg += " | 该组件由 SYSTEM/高权限启动，停止需要以管理员权限运行 WNMPanel.exe"
        log_error(logger, "Failed to stop MySQL: " + msg)
        return False, msg

    remove_pid_file(pid_dir, "mysqld.pid")
    log_info(logger, "MySQL stopped (port not in use)")
    return True, stopped_pids


def get_mysql_status(root_dir, cfg, logger=None):
    """Get MySQL running status.

    复用 panel/status.py 的 get_component_status 统一状态语义，
    CLI 和 Panel 不再得出不同结论。
    """
    try:
        from runtime.panel.status import get_component_status
        st = get_component_status("mysql", cfg)
        return {
            "running": st.get("running", False),
            "pid": st.get("listener_pid") or st.get("pid"),
            "port_listening": st.get("port_open", False),
            "state": st.get("state", "unknown"),
        }
    except Exception:
        # 回退到旧逻辑（兼容异常场景）
        from runtime.wnmp_config import get_effective_mysql_port
        pid_dir = os.path.join(root_dir, "runtime", "pids")
        pid = read_pid_file(pid_dir, "mysqld.pid")
        running = is_process_running(pid) if pid else False
        if running is None:
            running = True
        mysql_host = cfg.get("MYSQL_HOST", "127.0.0.1")
        mysql_port = get_effective_mysql_port(root_dir, cfg)
        listening = is_port_listening(mysql_host, mysql_port)
        return {"running": running, "pid": pid, "port_listening": listening}
