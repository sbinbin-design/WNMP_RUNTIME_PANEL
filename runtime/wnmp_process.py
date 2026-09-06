# -*- coding: utf-8 -*-
"""
WNMP Process Module - 进程管理基础功能
提供端口检测、进程启停、PID 文件管理等基础能力
全部使用 Python 标准库实现

进程路径查询优先级：WinAPI (ctypes) → tasklist → PowerShell fallback
不依赖 wmic.exe，兼容新版 Windows（Win10 21H2+ / Win11 默认不安装 WMIC）。
"""
import os
import sys
import socket
import time
import subprocess
import csv
import io


def check_port_available(host, port, logger=None):
    """检测指定端口是否可监听（未被占用）。

    先 connect_ex 判断是否已有监听，再用普通 bind 验证端口真正可用。
    端口被占用时稳定返回 False。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        result = sock.connect_ex((host, int(port)))
        sock.close()
        if result == 0:
            return False

        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.bind((host, int(port)))
        sock2.close()
        return True
    except (socket.error, OSError):
        return False


def wait_for_port_open(host, port, timeout=30, interval=0.5, logger=None):
    """等待指定端口变为可连接状态（服务已启动并监听）。"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_port_listening(host, port):
            return True
        time.sleep(interval)
    return False


def wait_for_port_close(host, port, timeout=30, interval=0.5, logger=None):
    """等待指定端口变为不可连接状态（服务已停止并释放端口）。"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if not is_port_listening(host, port):
            return True
        time.sleep(interval)
    return False


def write_pid_file(pid_dir, pid_filename, pid, logger=None):
    """写入 PID 文件到 runtime/pids/ 目录。

    写入失败时记录日志但不抛出异常，避免阻断启动流程。
    """
    try:
        os.makedirs(pid_dir, exist_ok=True)
        pid_path = os.path.join(pid_dir, pid_filename)
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(pid))
        return pid_path
    except (IOError, OSError) as e:
        # PID 文件写入失败时记录日志，不阻断启动
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  runtime/pids 不可写，PID 文件写入失败: {} - {}".format(pid_path, str(e)))
        return None


def read_pid_file(pid_dir, pid_filename):
    """读取 PID 文件，返回 pid 整数或 None。"""
    pid_path = os.path.join(pid_dir, pid_filename)
    if not os.path.isfile(pid_path):
        return None
    try:
        with open(pid_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)
    except (IOError, ValueError):
        pass
    return None


def remove_pid_file(pid_dir, pid_filename):
    """删除 PID 文件。"""
    pid_path = os.path.join(pid_dir, pid_filename)
    if os.path.isfile(pid_path):
        try:
            os.remove(pid_path)
        except OSError:
            pass


def is_process_running(pid):
    """检测指定 PID 的进程是否仍在运行。

    返回值：
      True  - 进程确认在运行
      False - 进程确认不存在（ERROR_INVALID_PARAMETER 或 PID 无效）
      None  - 无法确认（ERROR_ACCESS_DENIED 等权限问题）

    优先使用 ctypes 调用 Windows API（OpenProcess + GetExitCodeProcess），
    ctypes 不可用时回退到 tasklist（加 timeout 限制）。
    """
    if pid is None:
        return False

    # 优先使用 ctypes 快速检测
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_INVALID_PARAMETER = 87
        ERROR_ACCESS_DENIED = 5

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # OpenProcess 失败，检查错误码
            err = ctypes.GetLastError()
            if err == ERROR_INVALID_PARAMETER:
                # PID 不存在
                return False
            elif err == ERROR_ACCESS_DENIED:
                # 进程存在但权限不足，视为运行中
                return True
            else:
                # 其它错误（如 PID 复用等），保守返回 None
                return None

        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            else:
                return None
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass

    # 回退：tasklist（仅 ctypes 不可用时，加 timeout 限制）
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "PID eq {}".format(pid), "/FO", "CSV", "/NH"],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=2
        )
        raw = result.stdout.strip()
        if not raw:
            return False
        reader = csv.reader(io.StringIO(raw))
        for row in reader:
            if len(row) >= 2:
                pid_str = row[1].strip()
                try:
                    if int(pid_str) == pid:
                        return True
                except ValueError:
                    pass
        return False
    except Exception:
        return None


def kill_process(pid, timeout=10, logger=None):
    """强制终止指定 PID 的进程及其所有子进程（兜底函数）。

    停止策略：
      直接执行 taskkill /F /PID <pid> /T，不再先尝试不带 /F 的 taskkill。
      组件级优雅停止（如 nginx -s quit、mysqladmin shutdown）由上层调用方负责，
      进入本函数时已确定需要强制终止。

    即使 is_process_running 返回 None（权限不足），也会尝试 taskkill。
    """
    if pid is None:
        return True

    running = is_process_running(pid)
    # running=False 表示进程确认不存在，可跳过
    # running=True 或 None（权限不足）都需要尝试 taskkill
    if running is False:
        return True

    if logger:
        from runtime.wnmp_log import log_info
        log_info(logger, "Force killing process PID {} (running={})...".format(pid, running))

    # 直接强制终止：taskkill /F /PID <pid> /T
    force_rc = None
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5
        )
        force_rc = r.returncode
    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  kill_process_tree force=true pid={} tree=true failed: {}".format(pid, str(e)))
        return False

    # 等待进程退出
    start_time = time.time()
    while time.time() - start_time < timeout:
        check = is_process_running(pid)
        if check is False:
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  kill_process_tree force=true pid={} tree=true rc={} decision=forced_tree_kill_success".format(
                    pid, force_rc))
            return True
        time.sleep(0.3)

    # 超时仍未退出
    check = is_process_running(pid)
    if check is None:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  kill_process_tree force=true pid={} tree=true rc={} decision=access_denied_cannot_confirm".format(
                pid, force_rc))
        return False

    if logger:
        from runtime.wnmp_log import log_warn
        log_warn(logger, "  kill_process_tree force=true pid={} tree=true rc={} decision=terminate_failed".format(
            pid, force_rc))
    return False


def get_process_image_path(pid, timeout=3, allow_powershell=False):
    """获取指定 PID 进程的完整可执行路径（WinAPI 优先，不依赖 wmic）。

    查询优先级：
      1. WinAPI: OpenProcess + QueryFullProcessImageNameW（最快，无需外部命令）
      2. tasklist: 仅能获取进程名，无法获取完整路径
      3. PowerShell: Get-CimInstance Win32_Process（兜底，有超时保护）

    Args:
        pid: 进程 ID
        timeout: PowerShell fallback 超时秒数
        allow_powershell: True=允许 WinAPI 失败时 PowerShell 兜底（debug/停止/非热路径）；
                          False=禁止 PowerShell（/api/status 热路径，默认值）
    Returns:
        dict: {"path": str or None, "reason": str or None}
              reason 可选值: "success", "access_denied", "process_exited",
                            "invalid_pid", "api_error", "powershell_failed",
                            "query_timeout", "not_windows", "powershell_disabled"
    """
    if not pid:
        return {"path": None, "reason": "invalid_pid"}

    # 优先使用 WinAPI（仅 Windows）
    if os.name == "nt":
        winapi_result = _get_process_path_via_winapi(pid)
        if winapi_result["path"] is not None:
            return winapi_result
        # 热路径：禁止 PowerShell fallback，直接返回 WinAPI 结果
        if not allow_powershell:
            return winapi_result
        # 非热路径：允许 PowerShell fallback
        if winapi_result["reason"] == "access_denied":
            ps_result = _get_process_path_via_powershell(pid, timeout)
            if ps_result["path"] is not None:
                return ps_result
            return winapi_result
        # process_exited / invalid_pid / api_error 不再 fallback
        if winapi_result["reason"] in ("process_exited", "invalid_pid"):
            return winapi_result
        # api_error 等，尝试 PowerShell
        ps_result = _get_process_path_via_powershell(pid, timeout)
        if ps_result["path"] is not None:
            return ps_result
        return winapi_result

    # 非 Windows：尝试 PowerShell fallback
    if not allow_powershell:
        return {"path": None, "reason": "powershell_disabled"}
    return _get_process_path_via_powershell(pid, timeout)


def _get_process_path_via_winapi(pid):
    """通过 Windows 原生 API 获取进程完整路径。

    使用 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + QueryFullProcessImageNameW。
    不依赖任何外部命令。
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_INVALID_PARAMETER = 87
        ERROR_ACCESS_DENIED = 5

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            err = ctypes.GetLastError()
            if err == ERROR_INVALID_PARAMETER:
                return {"path": None, "reason": "process_exited"}
            elif err == ERROR_ACCESS_DENIED:
                return {"path": None, "reason": "access_denied"}
            else:
                return {"path": None, "reason": "api_error"}

        try:
            # QueryFullProcessImageNameW
            # BOOL QueryFullProcessImageNameW(HANDLE hProcess, DWORD dwFlags,
            #                                   LPWSTR lpExeName, PDWORD lpdwSize)
            QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
            QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD,
                ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)
            ]
            QueryFullProcessImageNameW.restype = wintypes.BOOL

            # 先用较大缓冲区
            buf_size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(buf_size.value)
            success = QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_size))
            if success:
                return {"path": buf.value, "reason": "success"}
            else:
                return {"path": None, "reason": "api_error"}
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return {"path": None, "reason": "api_error"}


def _get_process_path_via_powershell(pid, timeout=3):
    """通过 PowerShell 获取进程完整路径（兜底方案，有超时保护）。"""
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"ProcessId={}\" | "
            "Select-Object ProcessId,ExecutablePath | ConvertTo-Csv -NoTypeInformation"
        ).format(pid)
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", ps_cmd
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            rows = _parse_csv_by_header(result.stdout, "ProcessId", "ExecutablePath", None)
            for pid_val, exec_path in rows:
                if pid_val == pid and exec_path:
                    return {"path": exec_path, "reason": "success"}
        return {"path": None, "reason": "powershell_failed"}
    except subprocess.TimeoutExpired:
        return {"path": None, "reason": "query_timeout"}
    except Exception:
        return {"path": None, "reason": "powershell_failed"}


def get_process_name(pid, timeout=3, allow_powershell=False):
    """获取指定 PID 进程的进程名（不含路径）。

    优先从 get_process_image_path 的路径中提取 basename；
    路径不可读时 fallback 到 tasklist 获取进程名。
    不依赖 wmic。

    Args:
        pid: 进程 ID
        timeout: 查询超时秒数
        allow_powershell: True=允许 PowerShell 兜底获取路径；False=禁止（热路径默认）
    """
    if not pid:
        return None

    # 优先从完整路径提取 basename
    path_result = get_process_image_path(pid, timeout=timeout, allow_powershell=allow_powershell)
    if path_result["path"]:
        return os.path.basename(path_result["path"])

    # fallback: tasklist 获取进程名
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "PID eq {}".format(pid), "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            reader = csv.reader(io.StringIO(result.stdout.strip()))
            for row in reader:
                if len(row) >= 2:
                    try:
                        if int(row[1].strip()) == pid:
                            return row[0].strip()
                    except ValueError:
                        pass
    except Exception:
        pass

    return None


def get_process_name_fast(pid, timeout=1):
    """快速获取指定 PID 进程名（仅 WinAPI + tasklist，不调用 PowerShell）。

    专供 /api/status 热路径和 confirm_running_by_recorded_pid 使用。
    timeout 建议 0.8~1 秒，宁可返回 None 也不能卡住状态检测。
    """
    if not pid:
        return None

    # 1. WinAPI 获取路径后提取 basename（最快，无外部命令）
    path_result = _get_process_path_via_winapi(pid)
    if path_result["path"]:
        return os.path.basename(path_result["path"])

    # 2. tasklist 获取进程名（次快，不依赖 PowerShell）
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "PID eq {}".format(pid), "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            reader = csv.reader(io.StringIO(result.stdout.strip()))
            for row in reader:
                if len(row) >= 2:
                    try:
                        if int(row[1].strip()) == pid:
                            return row[0].strip()
                    except ValueError:
                        pass
    except Exception:
        pass

    return None


def is_process_alive(pid):
    """检测指定 PID 进程是否存活（语义化封装，不依赖 wmic）。

    返回值：
      True  - 进程确认在运行
      False - 进程确认不存在
      None  - 无法确认（权限不足等）
    """
    return is_process_running(pid)


def get_pid_detail(pid, timeout=3):
    """获取指定 PID 进程的详细信息（统一查询入口，不依赖 wmic）。

    Returns:
        dict: {
            "pid": int,
            "alive": bool or None,
            "path": str or None,
            "path_reason": str or None,
            "process_name": str or None,
        }
    """
    alive = is_process_alive(pid)
    path_result = get_process_image_path(pid, timeout=timeout, allow_powershell=True)
    proc_name = None
    if path_result["path"]:
        proc_name = os.path.basename(path_result["path"])
    else:
        proc_name = get_process_name(pid, timeout=timeout, allow_powershell=True)

    return {
        "pid": pid,
        "alive": alive,
        "path": path_result["path"],
        "path_reason": path_result["reason"],
        "process_name": proc_name,
    }


def get_process_path(pid, timeout=3):
    """获取指定 PID 进程的可执行路径（兼容旧接口包装）。

    内部调用 get_process_image_path，仅返回路径字符串或 None。
    新代码建议使用 get_process_image_path 获取完整信息（含 reason）。
    此函数用于非热路径（debug/停止），允许 PowerShell 兜底。
    """
    result = get_process_image_path(pid, timeout=timeout, allow_powershell=True)
    return result["path"]


def get_parent_pid(pid, timeout=3):
    """获取指定 PID 进程的父进程 PID（ParentProcessId）。

    查询优先级：
      1. WinAPI: OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + NtQueryInformationProcess
         （最快，不依赖外部命令，SYSTEM 进程在权限受限时可能返回 access_denied）
      2. PowerShell: Get-CimInstance Win32_Process（兜底，含超时保护，SYSTEM 可读）

    不依赖 wmic。用于 Master+Worker 模型下确认 listener Worker 是否属于
    某个 Master 的进程树（祖先关系判断）。

    Args:
        pid: 进程 ID
        timeout: PowerShell 兜底超时秒数

    Returns:
        int or None: 父进程 PID；进程不存在 / 权限不足 / 查询失败返回 None
    """
    if not pid:
        return None

    # 优先 WinAPI（仅 Windows）
    if os.name == "nt":
        ppid = _get_parent_pid_via_winapi(pid)
        if ppid is not None:
            return ppid
        # WinAPI 失败（access_denied / api_error），尝试 PowerShell 兜底
        ppid = _get_parent_pid_via_powershell(pid, timeout)
        if ppid is not None:
            return ppid
        return None

    # 非 Windows：PowerShell 兜底
    return _get_parent_pid_via_powershell(pid, timeout)


def _get_parent_pid_via_winapi(pid):
    """通过 WinAPI NtQueryInformationProcess 获取父进程 PID。

    使用 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + NtQueryInformationProcess
    读取 PROCESS_BASIC_INFORMATION.InheritedFromUniqueProcessId。
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_INVALID_PARAMETER = 87
        ERROR_ACCESS_DENIED = 5

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            err = ctypes.GetLastError()
            if err in (ERROR_INVALID_PARAMETER,):
                return None  # 进程不存在
            # access_denied 或其它错误：返回 None，交由 PowerShell 兜底
            return None

        try:
            # PROCESS_BASIC_INFORMATION 结构
            # typedef struct {
            #   PVOID Reserved1;
            #   PPEB PebBaseAddress;
            #   PVOID Reserved2[2];
            #   ULONG_PTR UniqueProcessId;
            #   PVOID Reserved3;
            # } PROCESS_BASIC_INFORMATION;
            # InheritedFromUniqueProcessId 实际位于 Reserved3 偏移处（x64 下偏移 0x20）
            class PROCESS_BASIC_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("Reserved1", ctypes.c_void_p),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("Reserved2_0", ctypes.c_void_p),
                    ("Reserved2_1", ctypes.c_void_p),
                    ("UniqueProcessId", ctypes.c_void_p),
                    ("InheritedFromUniqueProcessId", ctypes.c_void_p),
                ]

            pbi = PROCESS_BASIC_INFORMATION()
            # NtQueryInformationProcess(ProcessHandle, ProcessInformationClass,
            #                            ProcessInformation, ProcessInformationLength, ReturnLength)
            # ProcessBasicInformation = 0
            status = ntdll.NtQueryInformationProcess(
                handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None
            )
            if status == 0 and pbi.InheritedFromUniqueProcessId:
                return int(pbi.InheritedFromUniqueProcessId)
            return None
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _get_parent_pid_via_powershell(pid, timeout=3):
    """通过 PowerShell CIM 获取父进程 PID（兜底，SYSTEM 可读）。"""
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"ProcessId={}\" | "
            "Select-Object ProcessId,ParentProcessId | ConvertTo-Csv -NoTypeInformation"
        ).format(pid)
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", ps_cmd
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            reader = csv.DictReader(io.StringIO(result.stdout.strip()))
            for row in reader:
                pid_str = (row.get("ProcessId") or "").strip()
                ppid_str = (row.get("ParentProcessId") or "").strip()
                if not pid_str or not ppid_str:
                    continue
                try:
                    if int(pid_str) == pid:
                        return int(ppid_str)
                except ValueError:
                    continue
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def is_process_in_ancestry(child_pid, ancestor_pid, max_depth=16, timeout=3):
    """判断 child_pid 是否是 ancestor_pid 的后代进程（沿父链回溯）。

    用于 Master+Worker 模型：确认 listener Worker 是否属于 recorded Master 的进程树。

    Args:
        child_pid: 子进程 PID（如 listener Worker）
        ancestor_pid: 祖先进程 PID（如 Master）
        max_depth: 最大回溯层数，防止异常环
        timeout: 每次父进程查询的超时秒数

    Returns:
        bool: child 是否为 ancestor 的后代；查询失败返回 False
    """
    if not child_pid or not ancestor_pid:
        return False
    if child_pid == ancestor_pid:
        return True
    seen = set()
    cur = child_pid
    for _ in range(max_depth):
        if cur is None:
            return False
        if cur == ancestor_pid:
            return True
        if cur in seen:
            return False  # 环保护
        seen.add(cur)
        cur = get_parent_pid(cur, timeout=timeout)
    return False


def get_process_owner(pid, timeout=3):
    """获取指定 PID 进程的所属用户（不依赖 wmic）。

    使用 tasklist /V 获取用户信息，tasklist 是 Windows 内置命令。
    """
    if not pid:
        return None
    try:
        tasklist = subprocess.run(
            ["tasklist", "/FI", "PID eq {}".format(pid), "/FO", "CSV", "/NH", "/V"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=timeout
        )
        if tasklist.returncode == 0 and tasklist.stdout.strip():
            import csv as _csv
            reader = _csv.reader(io.StringIO(tasklist.stdout.strip()))
            for row in reader:
                # CSV 格式: "Image Name","PID","Session Name","Session#","Mem Usage","Status","User Name","CPU Time","Window Title"
                if len(row) >= 7:
                    return row[6].strip()
    except Exception:
        pass
    return None


def is_system_process(pid, timeout=3):
    """判断指定 PID 进程是否以 SYSTEM 用户运行。

    Args:
        pid: 进程 ID
        timeout: 命令超时秒数
    Returns:
        bool or None: True=SYSTEM 进程，False=非 SYSTEM 进程，None=无法判断
    """
    owner = get_process_owner(pid, timeout)
    if owner is None:
        return None
    owner_upper = owner.upper()
    return "SYSTEM" in owner_upper or "NT AUTHORITY" in owner_upper


def is_current_admin():
    """判断当前 Python 进程是否以管理员权限运行。

    Returns:
        bool: True=管理员，False=非管理员
    """
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _normalize_path(path_str):
    """规范化路径：大写盘符、正斜杠、去除尾部斜杠。"""
    if not path_str:
        return ""
    p = path_str.strip().rstrip("/").rstrip("\\")
    if len(p) >= 2 and p[1] == ":":
        p = p[0].upper() + p[1:]
    return p.replace("\\", "/")


def _is_path_in_root(exec_path, root_dir):
    """判断 exec_path 是否位于 root_dir 下。"""
    try:
        norm_exec = os.path.normpath(os.path.normcase(_normalize_path(exec_path)))
        norm_root = os.path.normpath(os.path.normcase(_normalize_path(root_dir)))
        if norm_exec == norm_root:
            return True
        common = os.path.commonpath([norm_exec, norm_root])
        return common == norm_root
    except Exception:
        return False


def _parse_csv_by_header(raw_text, pid_col_name, exec_col_name, logger):
    """使用 csv.DictReader 按字段名解析 CSV 数据。

    返回 [(pid_int, exec_path_str), ...]
    """
    results = []
    if not raw_text.strip():
        return results
    try:
        reader = csv.DictReader(io.StringIO(raw_text.strip()))
        for row in reader:
            pid_str = row.get(pid_col_name, "").strip()
            exec_path = row.get(exec_col_name, "").strip()
            if not pid_str or not exec_path:
                continue
            try:
                results.append((int(pid_str), exec_path))
            except ValueError:
                pass
    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  csv parsing failed: " + str(e))
    return results


def _find_processes_via_winapi(root_dir, image_name, logger):
    """通过 WinAPI + tasklist 查找本工具目录下的进程（不依赖 wmic）。

    先用 tasklist 获取所有匹配 image_name 的 PID，
    再逐个用 WinAPI 查询完整路径，判断是否在 root_dir 下。
    """
    results = []
    try:
        # 使用 tasklist 获取所有匹配 image_name 的 PID
        tasklist_result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq {}".format(image_name),
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=10
        )
        if tasklist_result.returncode != 0 or not tasklist_result.stdout.strip():
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  tasklist returned no results for {} (normal if service not running)".format(image_name))
            return results

        # 解析 CSV 获取 PID 列表
        candidate_pids = []
        reader = csv.reader(io.StringIO(tasklist_result.stdout.strip()))
        for row in reader:
            if len(row) >= 2:
                try:
                    pid_val = int(row[1].strip())
                    candidate_pids.append(pid_val)
                except ValueError:
                    pass

        if not candidate_pids:
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  tasklist found no {} processes (normal if service not running)".format(image_name))
            return results

        # 逐个用 WinAPI 查询路径并判断归属
        for pid_val in candidate_pids:
            path_result = get_process_image_path(pid_val, timeout=3)
            exec_path = path_result["path"]
            if exec_path and _is_path_in_root(exec_path, root_dir):
                results.append(pid_val)
                if logger:
                    from runtime.wnmp_log import log_info
                    log_info(logger, "  WinAPI found tool process: PID={} path={}".format(pid_val, exec_path))
            elif exec_path is None and path_result["reason"] in ("access_denied", "api_error"):
                # 路径不可读但进程存在，尝试 PowerShell 兜底查路径
                ps_result = _get_process_path_via_powershell(pid_val, timeout=5)
                ps_path = ps_result["path"]
                if ps_path and _is_path_in_root(ps_path, root_dir):
                    results.append(pid_val)
                    if logger:
                        from runtime.wnmp_log import log_info
                        log_info(logger, "  PowerShell found tool process: PID={} path={}".format(pid_val, ps_path))
    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  _find_processes_via_winapi exception: " + str(e))

    return results


def _find_processes_via_powershell(root_dir, image_name, logger):
    """通过 PowerShell CIM 查找本工具目录下的进程（兜底方案）。"""
    results = []
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='" + image_name + "'\" | "
            "Select-Object ProcessId,ExecutablePath | ConvertTo-Csv -NoTypeInformation"
        )
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-Command", ps_cmd
        ]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20
        )
        if result.returncode != 0:
            if logger:
                from runtime.wnmp_log import log_warn
                log_warn(logger, "  PowerShell CIM failed rc={}: {}".format(result.returncode, result.stderr.strip()[:100]))
            return results

        rows = _parse_csv_by_header(result.stdout, "ProcessId", "ExecutablePath", logger)
        if not rows:
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  PowerShell returned no valid rows (normal if service not running)")

        for pid_val, exec_path in rows:
            if _is_path_in_root(exec_path, root_dir):
                results.append(pid_val)
                if logger:
                    from runtime.wnmp_log import log_info
                    log_info(logger, "  PS found tool process: PID={} path={}".format(pid_val, exec_path))
    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "  PowerShell CIM exception: " + str(e))

    return results


def find_processes_by_path(root_dir, image_name, logger=None):
    """按可执行文件路径查找本工具目录下的进程（不依赖 wmic）。

    root_dir: 工具根目录
    image_name: 可执行文件名（如 nginx.exe、php-cgi.exe、mysqld.exe）
    返回匹配的 PID 列表，仅返回 executable path 位于 root_dir 下的进程。
    """
    if logger:
        from runtime.wnmp_log import log_info
        log_info(logger, "Finding processes by image: {} (root: {})".format(image_name, root_dir))

    # 优先使用 WinAPI + tasklist（不依赖 wmic）
    results = _find_processes_via_winapi(root_dir, image_name, logger)

    if not results:
        ps_results = _find_processes_via_powershell(root_dir, image_name, logger)
        if ps_results:
            if logger:
                log_info(logger, "  WinAPI no results, PowerShell found {} processes".format(len(ps_results)))
            results = ps_results

    if not results and logger:
        log_info(logger, "  No tool processes found for {}".format(image_name))

    return results


def find_processes_by_executable_path(expected_path, timeout=2, logger=None, fast_mode=True):
    """按完整可执行文件路径查找进程，返回匹配的 PID 列表（不依赖 wmic）。

    用于 /api/status 端口开放但 PID 文件缺失时的进程收养识别。
    必须匹配完整路径（normalized），不按 image name 模糊匹配。

    expected_path: 预期的完整可执行文件路径（如 root\\bin\\nginx\\nginx.exe）
    timeout: 扫描超时秒数，避免 /api/status 卡死
    fast_mode: True=仅 WinAPI+tasklist 短扫描（/api/status 热路径）；
               False=WinAPI+PowerShell 串行扫描（停止操作等非热路径）
    返回 [pid, ...]，未找到返回空列表。
    """
    if not expected_path or not os.path.isfile(expected_path):
        return []

    image_name = os.path.basename(expected_path)
    norm_expected = _normalize_path(expected_path)

    # 优先 WinAPI + tasklist（更快，不依赖 wmic）
    pids = _find_processes_by_exact_path_via_winapi(norm_expected, image_name, timeout, logger)
    if not pids and not fast_mode:
        # 非热路径：WinAPI 无结果时 PowerShell 兜底
        pids = _find_processes_by_exact_path_via_powershell(norm_expected, image_name, timeout, logger)

    return pids


def _find_processes_by_exact_path_via_winapi(norm_expected_path, image_name, timeout, logger):
    """通过 WinAPI + tasklist 查找可执行路径完全匹配的进程（不依赖 wmic）。

    先用 tasklist 获取所有匹配 image_name 的 PID，
    再逐个用 WinAPI 查询完整路径做精确匹配。
    """
    results = []
    try:
        tasklist_result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq {}".format(image_name),
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=timeout
        )
        if tasklist_result.returncode != 0 or not tasklist_result.stdout.strip():
            return results

        candidate_pids = []
        reader = csv.reader(io.StringIO(tasklist_result.stdout.strip()))
        for row in reader:
            if len(row) >= 2:
                try:
                    candidate_pids.append(int(row[1].strip()))
                except ValueError:
                    pass

        for pid_val in candidate_pids:
            path_result = get_process_image_path(pid_val, timeout=timeout)
            if path_result["path"] and _normalize_path(path_result["path"]) == norm_expected_path:
                results.append(pid_val)
    except Exception:
        pass
    return results


def _find_processes_by_exact_path_via_powershell(norm_expected_path, image_name, timeout, logger):
    """通过 PowerShell CIM 查找可执行路径完全匹配的进程（兜底）。

    timeout 由调用方控制，不再使用 max(timeout, 5) 放大。
    /api/status 热路径传入 timeout=2，停止操作传入更大值。
    """
    results = []
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='" + image_name + "'\" | "
            "Select-Object ProcessId,ExecutablePath | ConvertTo-Csv -NoTypeInformation"
        )
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-Command", ps_cmd
        ]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=timeout
        )
        if result.returncode != 0:
            return results

        rows = _parse_csv_by_header(result.stdout, "ProcessId", "ExecutablePath", logger)
        for pid_val, exec_path in rows:
            if _normalize_path(exec_path) == norm_expected_path:
                results.append(pid_val)
    except Exception:
        pass
    return results


def kill_processes_by_path(root_dir, image_name, timeout=10, logger=None):
    """按路径停止本工具目录下的指定进程。返回 (stopped_count, failed_count, error_msg)。"""
    pids = find_processes_by_path(root_dir, image_name, logger)

    if not pids:
        return 0, 0, ""

    if logger:
        from runtime.wnmp_log import log_info
        log_info(logger, "Stopping {} {} instances: {}".format(len(pids), image_name, pids))

    stopped = 0
    failed = 0

    for pid in pids:
        if kill_process(pid, timeout=timeout, logger=logger):
            stopped += 1
        else:
            failed += 1

    return stopped, failed, "" if failed == 0 else "{} processes may remain".format(failed)


def cleanup_residual_processes(root_dir, image_name, logger=None):
    """stop 后扫描本工具残留进程并清理。

    端口已释放但仍有本工具进程残留时调用。
    返回 (cleaned_count, failed_count)
    """
    pids = find_processes_by_path(root_dir, image_name, logger)

    if not pids:
        return 0, 0

    if logger:
        from runtime.wnmp_log import log_info, log_error
        log_error(logger, "Port released but {} residual tool processes found: {}".format(image_name, pids))

    stopped = 0
    failed = 0

    for pid in pids:
        if kill_process(pid, timeout=5, logger=logger):
            stopped += 1
        else:
            failed += 1

    if logger:
        if failed == 0:
            log_info(logger, "Cleanup: {} processes terminated".format(stopped))
        else:
            log_error(logger, "Cleanup: {} terminated, {} may remain".format(stopped, failed))

    return stopped, failed


def start_process(cmd_list, cwd=None, logger=None, stdout_file=None, stderr_file=None, env=None):
    """启动进程，返回 subprocess.Popen 对象或 None。

    stdout_file/stderr_file: 若提供则重定向到文件，否则 DEVNULL。
    父进程侧关闭日志文件句柄，避免句柄堆积。
    env: 可选的环境变量 dict。默认 None（继承当前进程环境，行为与旧版本一致）。
         传入时直接传给 subprocess.Popen(..., env=env)，不影响其它调用方。
    """
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        stdout_f = None
        stderr_f = None

        if stdout_file:
            stdout_f = open(stdout_file, "a", encoding="utf-8", errors="replace")
        else:
            stdout_f = subprocess.DEVNULL

        if stderr_file:
            stderr_f = open(stderr_file, "a", encoding="utf-8", errors="replace")
        else:
            stderr_f = subprocess.DEVNULL

        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=cwd,
                stdout=stdout_f,
                stderr=stderr_f,
                env=env,
                creationflags=creation_flags
            )
            return proc
        finally:
            if stdout_f and stdout_f is not subprocess.DEVNULL:
                stdout_f.close()
            if stderr_f and stderr_f is not subprocess.DEVNULL:
                stderr_f.close()
    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_error
            log_error(logger, "Failed to start process: " + str(e))
        return None


def is_port_listening(host, port, timeout=0.3):
    """检测指定端口是否正在监听。

    默认超时 0.3 秒，适用于 /api/status 热路径。
    127.0.0.1 上关闭端口通常立即返回 RST，不会卡住。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, int(port)))
        sock.close()
        return result == 0
    except Exception:
        return False


def terminate_pids(pids, timeout=10, tree=True, logger=None):
    """统一强制终止工具：对 PID 列表逐个强制终止并等待退出（兜底函数）。

    停止策略：
      直接对每个 PID 执行 taskkill /F /PID <pid> [/T]，不再先尝试不带 /F 的 taskkill。
      组件级优雅停止由上层调用方负责，进入本函数时已确定需要强制终止。

    Args:
        pids: 要终止的 PID 列表
        timeout: 每个进程的终止超时秒数
        tree: True 则终止进程树（/T），False 仅终止进程本身
        logger: 日志对象
    Returns:
        (terminated, failed): 成功终止数和失败数
    """
    if not pids:
        return 0, 0

    terminated = 0
    failed = 0

    for pid in pids:
        running = is_process_running(pid)
        # running=False 表示进程确认不存在，可跳过
        if running is False:
            terminated += 1
            continue

        if logger:
            from runtime.wnmp_log import log_info
            log_info(logger, "Force terminating PID {} (tree={}, running={})...".format(pid, tree, running))

        # 直接强制终止：taskkill /F [/T] /PID <pid>
        force_rc = None
        try:
            cmd = ["taskkill", "/F"]
            if tree:
                cmd.append("/T")
            cmd.extend(["/PID", str(pid)])
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5
            )
            force_rc = r.returncode
        except Exception as e:
            if logger:
                from runtime.wnmp_log import log_warn
                log_warn(logger, "  kill_process_tree force=true pid={} tree={} failed: {}".format(pid, tree, str(e)))
            failed += 1
            continue

        # 等待进程退出
        start = time.time()
        while time.time() - start < timeout:
            check = is_process_running(pid)
            if check is False:
                break
            time.sleep(0.3)

        check = is_process_running(pid)
        if check is False:
            terminated += 1
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "  kill_process_tree force=true pid={} tree={} rc={} decision=forced_tree_kill_success".format(
                    pid, tree, force_rc))
        elif check is None:
            # 权限不足无法确认，视为失败
            failed += 1
            if logger:
                from runtime.wnmp_log import log_warn
                log_warn(logger, "  kill_process_tree force=true pid={} tree={} rc={} decision=access_denied_cannot_confirm".format(
                    pid, tree, force_rc))
        else:
            failed += 1
            if logger:
                from runtime.wnmp_log import log_warn
                log_warn(logger, "  kill_process_tree force=true pid={} tree={} rc={} decision=terminate_failed".format(
                    pid, tree, force_rc))

    return terminated, failed


def wait_ports_closed(host, ports, timeout=10, interval=0.3, logger=None):
    """等待多个端口全部释放。

    Args:
        host: 主机地址
        ports: 端口列表
        timeout: 最大等待秒数
        interval: 检查间隔秒数
        logger: 日志对象
    Returns:
        True 如果所有端口都已释放，False 如果超时
    """
    if not ports:
        return True

    start = time.time()
    while time.time() - start < timeout:
        all_closed = True
        for port in ports:
            if is_port_listening(host, int(port)):
                all_closed = False
                break
        if all_closed:
            return True
        time.sleep(interval)

    return False


def find_port_listener_path(host, port, root_dir=None, timeout=3, logger=None):
    """查找占用指定端口的进程的可执行路径（兼容包装）。

    内部调用 get_listening_processes，返回第一个候选。
    新代码建议直接使用 get_listening_processes 获取所有 listener。

    Args:
        host: 主机地址
        port: 端口号
        root_dir: 如果提供，判断监听进程是否属于本项目
        timeout: 命令超时秒数
        logger: 日志对象
    Returns:
        dict: {"pid": int or None, "path": str or None, "is_ours": bool or None,
               "local_address": str or None, "owner": str or None}
    """
    listeners = get_listening_processes(port, host=host, root_dir=root_dir,
                                        timeout=timeout, logger=logger)
    if listeners:
        first = listeners[0]
        return {
            "pid": first.get("pid"),
            "path": first.get("path"),
            "is_ours": first.get("is_ours"),
            "local_address": first.get("local_address"),
            "owner": first.get("owner"),
        }
    return {"pid": None, "path": None, "is_ours": None, "local_address": None, "owner": None}


def _paths_equal(path_a, path_b):
    """精确比较两个路径是否相等。使用 abspath、normcase、normpath 处理大小写、斜杠和尾部分隔符。"""
    if not path_a or not path_b:
        return False
    try:
        norm_a = os.path.normpath(os.path.normcase(os.path.abspath(path_a)))
        norm_b = os.path.normpath(os.path.normcase(os.path.abspath(path_b)))
        return norm_a == norm_b
    except Exception:
        return False


def get_listening_processes(port, host="127.0.0.1", root_dir=None, timeout=3, logger=None, expected_path=None, fast_mode=True, include_owner=False):
    """查找指定端口的所有 LISTENING 进程，返回候选列表。

    支持 0.0.0.0、[::]、:::、[::1] 等本机监听地址。
    一个端口可能同时有 IPv4 和 IPv6 多条 LISTENING 记录。

    Args:
        port: 端口号
        host: 查询的目标主机地址（默认 127.0.0.1）
        root_dir: 如果提供，判断每个 listener 是否属于本项目（is_in_root）
        expected_path: 如果提供，判断 listener path 是否精确匹配（is_expected）
        timeout: 命令超时秒数
        logger: 日志对象
        fast_mode: True=仅 WinAPI 批量查询路径，不逐个兜底
        include_owner: True=查询进程 owner（慢，仅 /api/status/debug 使用）；
                       False=不查询 owner（默认，/api/status 热路径使用）
    Returns:
        list[dict]: 每个 dict 包含 pid, local_address, path, owner, is_ours, is_in_root, is_expected
                    owner: include_owner=True 时返回用户名，否则返回 None
                    is_ours: 兼容旧字段，等同于 is_expected（精确匹配 expected_path 时 True）
                    is_in_root: 进程路径在 rootDir 下（仅表示在项目目录，不一定是目标 exe）
                    is_expected: listener path 精确等于 expected_path
                    以上字段为 None 表示无法判断
    """
    results = []
    try:
        ns = subprocess.run(
            ["netstat", "-aon", "-p", "TCP"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=timeout
        )
        if ns.returncode != 0:
            return results

        port_str = str(port)
        # 收集所有匹配的 (priority, pid, local_addr)
        raw_candidates = []

        for line in ns.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 5 or parts[3] != "LISTENING":
                continue
            local_addr = parts[1]
            addr_port = local_addr.rsplit(":", 1)
            if len(addr_port) != 2 or addr_port[1] != port_str:
                continue

            addr_host = addr_port[0]
            is_local = False
            if addr_host == host:
                is_local = True
            elif host == "127.0.0.1" and addr_host in ("0.0.0.0", "[::]", "::", "[::1]"):
                is_local = True
            elif host == "0.0.0.0" and addr_host in ("0.0.0.0", "[::]", "::", "127.0.0.1", "[::1]"):
                is_local = True

            if is_local:
                try:
                    pid = int(parts[4])
                    priority = 0 if addr_host == host else 1
                    raw_candidates.append((priority, pid, local_addr))
                except ValueError:
                    continue

        if not raw_candidates:
            return results

        # 按优先级排序
        raw_candidates.sort(key=lambda x: x[0])

        # 批量获取进程路径
        unique_pids = list(set(pid for _, pid, _ in raw_candidates))
        pid_paths = _batch_get_process_paths(unique_pids, timeout, fast_mode=fast_mode)

        # 构建结果
        for priority, pid, local_addr in raw_candidates:
            path = pid_paths.get(pid)
            # path_query_error: 路径查询失败时记录原因（不依赖 wmic）
            path_query_error = None
            if path is None:
                if fast_mode:
                    # 热路径：不再对每个 path=None 的 PID 调用 get_process_image_path，
                    # 避免触发 PowerShell fallback，直接标记原因
                    path_query_error = "winapi_unreadable"
                else:
                    # 非热路径：允许进一步查询详细 reason（含 PowerShell 兜底）
                    path_detail = get_process_image_path(pid, timeout=1, allow_powershell=True)
                    reason = path_detail.get("reason", "path_unreadable")
                    path_query_error = reason
            # is_in_root: 进程路径在 rootDir 下（仅表示在项目目录，不一定是目标 exe）
            is_in_root = None
            if path and root_dir:
                is_in_root = _is_path_in_root(path, root_dir)
            # is_expected: listener path 精确等于 expected_path
            is_expected = None
            if path and expected_path:
                is_expected = _paths_equal(path, expected_path)
            # is_ours: 兼容旧字段，优先使用 is_expected，无 expected_path 时回退 is_in_root
            if expected_path:
                is_ours = is_expected
            else:
                is_ours = is_in_root
            # 获取进程 owner（仅 include_owner=True 时查询，避免热路径执行 tasklist /V）
            owner = None
            if include_owner:
                try:
                    owner = get_process_owner(pid, timeout=min(timeout, 2))
                except Exception:
                    pass
            results.append({
                "pid": pid,
                "local_address": local_addr,
                "path": path,
                "owner": owner,
                "is_ours": is_ours,
                "is_in_root": is_in_root,
                "is_expected": is_expected,
                "path_query_error": path_query_error,
            })

    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "get_listening_processes error: " + str(e))

    return results


def get_listening_processes_for_ports(port_expected_map, host="127.0.0.1", root_dir=None, timeout=3, logger=None, fast_mode=True):
    """批量查询多个端口的 listener，一次 netstat + 一次 WinAPI 批量获取路径。

    避免每个端口重复 netstat/WinAPI，适合 /api/status 一次请求查询所有组件端口。
    不查询 owner（owner 只用于 /api/status/debug 和 stop 动作）。

    Args:
        port_expected_map: dict，{port_number: expected_path}，每个端口绑定正确的 expected_path
        host: 查询主机地址
        root_dir: 项目根目录
        timeout: 命令超时秒数
        logger: 日志对象
    Returns:
        dict: {port_number: list[dict]}，每个 dict 包含 pid, local_address, path, is_ours, is_in_root, is_expected
    """
    result_map = {p: [] for p in port_expected_map}
    if not port_expected_map:
        return result_map

    try:
        # 一次 netstat 获取所有 LISTENING 记录
        ns = subprocess.run(
            ["netstat", "-aon", "-p", "TCP"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=timeout
        )
        if ns.returncode != 0:
            return result_map

        # 收集所有匹配的 (port, priority, pid, local_addr)
        port_strs = {str(p) for p in port_expected_map}
        raw_map = {p: [] for p in port_expected_map}  # port -> [(priority, pid, local_addr)]

        for line in ns.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 5 or parts[3] != "LISTENING":
                continue
            local_addr = parts[1]
            addr_port = local_addr.rsplit(":", 1)
            if len(addr_port) != 2 or addr_port[1] not in port_strs:
                continue

            port_num = int(addr_port[1])
            addr_host = addr_port[0]
            is_local = False
            if addr_host == host:
                is_local = True
            elif host == "127.0.0.1" and addr_host in ("0.0.0.0", "[::]", "::", "[::1]"):
                is_local = True
            elif host == "0.0.0.0" and addr_host in ("0.0.0.0", "[::]", "::", "127.0.0.1", "[::1]"):
                is_local = True

            if is_local and port_num in raw_map:
                try:
                    pid = int(parts[4])
                    priority = 0 if addr_host == host else 1
                    raw_map[port_num].append((priority, pid, local_addr))
                except ValueError:
                    continue

        # 收集所有唯一 PID
        all_pids = set()
        for port_num, candidates in raw_map.items():
            for _, pid, _ in candidates:
                all_pids.add(pid)

        # 一次 WinAPI 批量获取所有 PID 的路径
        pid_paths = _batch_get_process_paths(list(all_pids), timeout, fast_mode=fast_mode) if all_pids else {}

        # 构建每个端口的结果
        for port_num, candidates in raw_map.items():
            expected_path = port_expected_map[port_num]
            candidates.sort(key=lambda x: x[0])
            port_results = []
            for priority, pid, local_addr in candidates:
                path = pid_paths.get(pid)
                # path_query_error: 路径查询失败时记录原因（不依赖 wmic）
                path_query_error = None
                if path is None:
                    if fast_mode:
                        # 热路径：不再对每个 path=None 的 PID 调用 get_process_image_path，
                        # 避免触发 PowerShell fallback，直接标记原因
                        path_query_error = "winapi_unreadable"
                    else:
                        # 非热路径：允许进一步查询详细 reason（含 PowerShell 兜底）
                        path_detail = get_process_image_path(pid, timeout=1, allow_powershell=True)
                        path_query_error = path_detail.get("reason", "path_unreadable")
                is_in_root = None
                if path and root_dir:
                    is_in_root = _is_path_in_root(path, root_dir)
                is_expected = None
                if path and expected_path:
                    is_expected = _paths_equal(path, expected_path)
                # is_ours: 有 expected_path 时用 is_expected，否则用 is_in_root
                if expected_path:
                    is_ours = is_expected
                else:
                    is_ours = is_in_root
                port_results.append({
                    "pid": pid,
                    "local_address": local_addr,
                    "path": path,
                    "owner": None,  # 批量查询不获取 owner
                    "is_ours": is_ours,
                    "is_in_root": is_in_root,
                    "is_expected": is_expected,
                    "path_query_error": path_query_error,
                })
            result_map[port_num] = port_results

    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "get_listening_processes_for_ports error: " + str(e))

    return result_map


def _batch_get_process_paths(pids, timeout=3, fast_mode=True):
    """批量获取多个 PID 的可执行路径（不依赖 wmic）。

    优先使用 WinAPI 逐个查询（最快，无外部命令开销），
    失败时 fallback 到 PowerShell 批量查询。

    Args:
        pids: PID 列表
        timeout: 查询超时秒数
        fast_mode: True=仅 WinAPI 逐个查询，不 PowerShell 兜底（/api/status 热路径）；
                   False=WinAPI 失败后 PowerShell 兜底（/api/status/debug 等非热路径）
    Returns:
        dict: {pid: path_str or None}
    """
    pid_paths = {}
    if not pids:
        return pid_paths

    # 优先使用 WinAPI 逐个查询（无外部命令开销，极快）
    failed_pids = []
    for pid in pids:
        path_result = _get_process_path_via_winapi(pid)
        if path_result["path"] is not None:
            pid_paths[pid] = path_result["path"]
        else:
            failed_pids.append(pid)
            pid_paths[pid] = None

    # 快速模式：不 PowerShell 兜底
    if not fast_mode and failed_pids:
        # 非热路径：对 WinAPI 失败的 PID，尝试 PowerShell 批量查询
        ps_paths = _batch_get_process_paths_via_powershell(failed_pids, timeout)
        for pid, path in ps_paths.items():
            if path is not None:
                pid_paths[pid] = path

    return pid_paths


def _batch_get_process_paths_via_powershell(pids, timeout=3):
    """通过 PowerShell 批量获取多个 PID 的可执行路径（兜底方案，有超时保护）。"""
    pid_paths = {}
    if not pids:
        return pid_paths

    try:
        # 构建 PowerShell WHERE 子句
        pid_filters = " OR ".join("ProcessId={}".format(p) for p in pids)
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"{}\" | "
            "Select-Object ProcessId,ExecutablePath | ConvertTo-Csv -NoTypeInformation"
        ).format(pid_filters)
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", ps_cmd
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            rows = _parse_csv_by_header(result.stdout, "ProcessId", "ExecutablePath", None)
            for pid_val, exec_path in rows:
                if pid_val in pids:
                    pid_paths[pid_val] = exec_path if exec_path else None
    except Exception:
        pass

    return pid_paths


def check_listener_ownership(port, expected_path, host="127.0.0.1", root_dir=None, timeout=3, logger=None, include_owner=False, fast_mode=True):
    """检查指定端口的 listener 是否属于本项目，返回统一归属结果（不依赖 wmic）。

    统一归属判断逻辑，供状态检测和启动确认共用。
    不依赖 PID 文件，纯粹基于端口 listener path 判断。
    使用 is_expected（精确匹配 expected_path）判定 running，不用 is_in_root。

    Args:
        port: 端口号
        expected_path: 预期的本项目可执行文件完整路径
        host: 查询主机地址
        root_dir: 项目根目录
        timeout: 查询超时秒数
        logger: 日志对象
        include_owner: True=查询进程 owner（仅 /api/status/debug 使用）；
                       False=不查询 owner（默认，/api/status 热路径使用）
        fast_mode: True=禁止 PowerShell fallback（/api/status 热路径，默认）；
                   False=允许 PowerShell 兜底（/api/status/debug、停止动作）
    Returns:
        dict: {
            "status": "running"|"stopped"|"external"|"unknown",
            "pid": int or None,
            "path": str or None,
            "path_reason": str or None,   # 新增：路径不可读时的原因
            "owner": str or None,
            "is_ours": bool or None,
            "is_expected": bool or None,
            "is_in_root": bool or None,
            "listeners": list[dict],
            "message": str,
        }
    """
    listeners = get_listening_processes(port, host=host, root_dir=root_dir, timeout=timeout,
                                        logger=logger, expected_path=expected_path,
                                        fast_mode=fast_mode, include_owner=include_owner)

    if not listeners:
        return {
            "status": "stopped",
            "pid": None,
            "path": None,
            "path_reason": None,
            "owner": None,
            "is_ours": None,
            "is_expected": None,
            "is_in_root": None,
            "listeners": [],
            "message": "配置端口未监听",
        }

    # 检查是否有精确匹配 expected_path 的 listener（is_expected=True → running）
    expected_listener = None
    in_root_listener = None  # 在 rootDir 下但不是目标 exe
    external_listener = None
    unknown_listener = None

    for ln in listeners:
        if ln.get("is_expected") is True:
            expected_listener = ln
            break
        elif ln.get("is_in_root") is True and ln.get("is_expected") is False:
            in_root_listener = ln
        elif ln.get("is_expected") is False:
            external_listener = ln
        elif ln.get("is_expected") is None and ln.get("is_in_root") is None:
            unknown_listener = ln
        elif ln.get("is_in_root") is True and ln.get("is_expected") is None:
            # 有路径在 rootDir 下但无法确认是否精确匹配
            in_root_listener = ln

    if expected_listener:
        return {
            "status": "running",
            "pid": expected_listener.get("pid"),
            "path": expected_listener.get("path"),
            "path_reason": "success",
            "owner": expected_listener.get("owner"),
            "is_ours": True,
            "is_expected": True,
            "is_in_root": expected_listener.get("is_in_root"),
            "listeners": listeners,
            "message": "正常运行",
        }
    elif in_root_listener:
        # 在 rootDir 下但不是目标 exe（如 rootDir 下其它程序占用端口）
        return {
            "status": "external",
            "pid": in_root_listener.get("pid"),
            "path": in_root_listener.get("path"),
            "path_reason": "success",
            "owner": in_root_listener.get("owner"),
            "is_ours": False,
            "is_expected": False,
            "is_in_root": True,
            "listeners": listeners,
            "message": "端口被非目标进程占用",
        }
    elif external_listener:
        return {
            "status": "external",
            "pid": external_listener.get("pid"),
            "path": external_listener.get("path"),
            "path_reason": "success",
            "owner": external_listener.get("owner"),
            "is_ours": False,
            "is_expected": False,
            "is_in_root": external_listener.get("is_in_root"),
            "listeners": listeners,
            "message": "端口被外部程序占用",
        }
    else:
        # unknown_listener: 端口已监听但无法确认 path
        # 从 listener 的 path_query_error 提取原因
        path_reason = "path_unreadable"
        if unknown_listener:
            pqe = unknown_listener.get("path_query_error", "")
            if "access_denied" in str(pqe).lower() or "权限" in str(pqe):
                path_reason = "winapi_access_denied"
            elif "timeout" in str(pqe).lower() or "超时" in str(pqe):
                path_reason = "query_timeout"
            elif "exited" in str(pqe).lower() or "process_exited" in str(pqe):
                path_reason = "process_exited"

        return {
            "status": "unknown",
            "pid": unknown_listener.get("pid") if unknown_listener else None,
            "path": unknown_listener.get("path") if unknown_listener else None,
            "path_reason": path_reason,
            "owner": unknown_listener.get("owner") if unknown_listener else None,
            "is_ours": None,
            "is_expected": None,
            "is_in_root": unknown_listener.get("is_in_root") if unknown_listener else None,
            "listeners": listeners,
            "message": "端口已开放，但无法确认进程归属",
        }


def wait_for_port_listener(port, expected_path, host="127.0.0.1", root_dir=None,
                           timeout=30, interval=0.5, logger=None):
    """等待指定端口被本项目进程监听，返回归属确认结果。

    供启动函数使用：启动后等待端口开放并确认 listener path 属于当前 rootDir。
    不依赖 PID 文件。

    Args:
        port: 端口号
        expected_path: 预期的本项目可执行文件完整路径
        host: 查询主机地址
        root_dir: 项目根目录
        timeout: 最大等待秒数
        interval: 检查间隔秒数
        logger: 日志对象
    Returns:
        dict: check_listener_ownership 的返回值，超时时 status="stopped"
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = check_listener_ownership(port, expected_path, host=host,
                                          root_dir=root_dir, timeout=2, logger=logger)
        if result["status"] == "running":
            return result
        elif result["status"] == "external":
            # 端口被外部程序占用，不需要继续等待
            return result
        elif result["status"] == "unknown":
            # 端口已开放但无法确认归属，短暂等待后重试
            time.sleep(interval)
            continue
        else:
            # stopped: 端口未开放，继续等待
            time.sleep(interval)
            continue

    # 超时，最后检查一次
    return check_listener_ownership(port, expected_path, host=host,
                                    root_dir=root_dir, timeout=2, logger=logger)
