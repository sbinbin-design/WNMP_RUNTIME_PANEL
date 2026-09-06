/*
 * WNMPanel.exe - WNMP Runtime Panel Launcher
 *
 * 职责：读取配置、启动内置 Python、拉起 runtime.panel_server、
 *       等待 Panel 就绪并打开浏览器。
 *
 * 不做：Nginx/PHP/MySQL 启停、业务逻辑、bat 调用、系统 Python 查找。
 */

#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shellapi.h>
#include <shlwapi.h>
#include <winhttp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/*  常量                                                               */
/* ------------------------------------------------------------------ */
#define MAX_PATH_BUF    4096
#define MAX_CMDLINE     8192
#define MAX_LOG_LINE    8192
#define MAX_ENV_BUF     32768
#define PANEL_READY_TIMEOUT_MS  800    /* 快速模式：最多等 800ms /api/ping */
#define PANEL_READY_POLL_MS     200    /* 每 200ms 探测一次 */
#define DEFAULT_PANEL_HOST  L"127.0.0.1"
#define DEFAULT_PANEL_PORT  8787
#define DEFAULT_AUTO_OPEN   1

/* ------------------------------------------------------------------ */
/*  全局状态                                                            */
/* ------------------------------------------------------------------ */
static WCHAR g_rootDir[MAX_PATH_BUF];       /* 项目根目录 */
static WCHAR g_iniPath[MAX_PATH_BUF];       /* runtime.ini 路径 */
static WCHAR g_pythonExe[MAX_PATH_BUF];     /* 内置 python.exe 路径 */
static WCHAR g_logDir[MAX_PATH_BUF];        /* logs\panel 目录 */
static WCHAR g_logPath[MAX_PATH_BUF];       /* launcher.log 路径 */
static WCHAR g_panelLogPath[MAX_PATH_BUF];  /* panel_server.log 路径 */

static WCHAR g_panelHost[256]   = {0};      /* 最终 PANEL_HOST */
static int   g_panelPort        = 0;        /* 最终 PANEL_PORT */
static int   g_autoOpenBrowser  = 0;        /* 是否自动打开浏览器 */
static int   g_debug            = 0;        /* --debug 标志 */
static int   g_testOsVersion    = 0;        /* --test-os-version 标志 */

static WCHAR g_probeHost[256]   = {0};      /* 探测用 host（0.0.0.0 → 127.0.0.1） */
static WCHAR g_browserHost[256] = {0};      /* 浏览器用 host */

static WCHAR g_finalPythonPath[MAX_PATH_BUF] = {0}; /* 记录日志用 */
static WCHAR g_finalCmdLine[MAX_CMDLINE]     = {0};  /* 记录日志用 */
static WCHAR g_finalCwd[MAX_PATH_BUF]        = {0};  /* 记录日志用 */
static WCHAR g_finalPythonPathEnv[MAX_ENV_BUF] = {0}; /* 记录日志用 */

/* ------------------------------------------------------------------ */
/*  日志系统                                                            */
/* ------------------------------------------------------------------ */
static CRITICAL_SECTION g_logCS;

static void log_init(void)
{
    InitializeCriticalSection(&g_logCS);
}

static void log_write(const WCHAR *fmt, ...)
{
    FILE *fp = NULL;
    va_list ap;

    EnterCriticalSection(&g_logCS);

    /* 尝试以 UTF-8 BOM 追加写入 */
    fp = _wfopen(g_logPath, L"a, ccs=UTF-8");
    if (!fp) {
        LeaveCriticalSection(&g_logCS);
        return;
    }

    /* 写入时间戳 */
    SYSTEMTIME st;
    GetLocalTime(&st);
    fwprintf(fp, L"[%04d-%02d-%02d %02d:%02d:%02d] ",
             st.wYear, st.wMonth, st.wDay,
             st.wHour, st.wMinute, st.wSecond);

    va_start(ap, fmt);
    vfwprintf(fp, fmt, ap);
    va_end(ap);

    fwprintf(fp, L"\n");
    fclose(fp);

    LeaveCriticalSection(&g_logCS);
}

/* ------------------------------------------------------------------ */
/*  工具函数                                                            */
/* ------------------------------------------------------------------ */

/* 去除字符串前后空白 */
static WCHAR* trim_w(WCHAR *s)
{
    WCHAR *end;
    while (*s == L' ' || *s == L'\t' || *s == L'\r' || *s == L'\n') s++;
    if (*s == 0) return s;
    end = s + wcslen(s) - 1;
    while (end > s && (*end == L' ' || *end == L'\t' || *end == L'\r' || *end == L'\n')) end--;
    end[1] = 0;
    return s;
}

/* ANSI 字符串 trim（用于 UTF-8 行解析） */
static char* trim_a(char *s)
{
    char *end;
    while (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n') s++;
    if (*s == 0) return s;
    end = s + strlen(s) - 1;
    while (end > s && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n')) end--;
    end[1] = 0;
    return s;
}

/* 宽字符串大小写不敏感比较 */
static int wstrieq(const WCHAR *a, const WCHAR *b)
{
    return _wcsicmp(a, b) == 0;
}

/* ------------------------------------------------------------------ */
/*  操作系统版本检测（RtlGetVersion 真实版本）                          */
/*  新增：最低版本基线检测，NT 10.0 及以上支持，低于基线阻断启动         */
/* ------------------------------------------------------------------ */

/* OSVERSIONINFOEXW 结构体（与 RTL_OSVERSIONINFOEXW 兼容） */
typedef struct _MY_OSVERSIONINFOEXW {
    DWORD dwOSVersionInfoSize;
    DWORD dwMajorVersion;
    DWORD dwMinorVersion;
    DWORD dwBuildNumber;
    DWORD dwPlatformId;
    WCHAR szCSDVersion[128];
    WORD  wServicePackMajor;
    WORD  wServicePackMinor;
    WORD  wSuiteMask;
    BYTE  wProductType;
    BYTE  wReserved;
} MY_OSVERSIONINFOEXW;

/* RtlGetVersion 函数指针类型 */
typedef LONG (WINAPI *RtlGetVersionPtr)(MY_OSVERSIONINFOEXW *);

/*
 * GetRealWindowsVersion - 通过 ntdll.RtlGetVersion 获取真实 Windows 版本
 * 不受 manifest/兼容模式影响，始终返回真实 major/minor/build/productType
 * 返回：1 成功，0 失败
 */
static int GetRealWindowsVersion(DWORD *major, DWORD *minor, DWORD *build, BYTE *productType)
{
    HMODULE hNtDll = LoadLibraryW(L"ntdll.dll");
    if (!hNtDll) return 0;

    RtlGetVersionPtr fnRtlGetVersion = (RtlGetVersionPtr)GetProcAddress(hNtDll, "RtlGetVersion");
    if (!fnRtlGetVersion) {
        FreeLibrary(hNtDll);
        return 0;
    }

    MY_OSVERSIONINFOEXW osvi;
    ZeroMemory(&osvi, sizeof(osvi));
    osvi.dwOSVersionInfoSize = sizeof(osvi);

    LONG status = fnRtlGetVersion(&osvi);
    FreeLibrary(hNtDll);

    if (status != 0) return 0;

    if (major)       *major = osvi.dwMajorVersion;
    if (minor)       *minor = osvi.dwMinorVersion;
    if (build)       *build = osvi.dwBuildNumber;
    if (productType) *productType = osvi.wProductType;

    return 1;
}

/*
 * IsSupportedWindowsVersion - 判断 Windows 版本是否受支持（纯函数，可模拟测试）
 * 支持条件：major > 10 或 major == 10（含 minor >= 0）
 * 不支持：major < 10（NT 6.1/6.2/6.3 等）
 */
static int IsSupportedWindowsVersion(DWORD major, DWORD minor)
{
    (void)minor; /* major==10 时所有 minor 均支持；major>10 同样支持 */
    if (major > 10) return 1;
    if (major == 10) return 1;
    return 0;
}

/*
 * FormatWindowsVersionName - 根据版本号生成友好名称
 * 仅用于日志和弹窗展示，不参与支持判断
 * productType: VER_NT_WORKSTATION=1, VER_NT_DOMAIN_CONTROLLER=2, VER_NT_SERVER=3
 */
static void FormatWindowsVersionName(DWORD major, DWORD minor, DWORD build, BYTE productType,
                                     WCHAR *nameBuf, size_t nameBufLen)
{
    if (major == 10 && minor == 0) {
        if (productType == 1) { /* VER_NT_WORKSTATION */
            if (build >= 22000) {
                swprintf_s(nameBuf, nameBufLen, L"Windows 11 (NT 10.0 build %lu)", build);
            } else {
                swprintf_s(nameBuf, nameBufLen, L"Windows 10 (NT 10.0 build %lu)", build);
            }
        } else { /* Server */
            swprintf_s(nameBuf, nameBufLen, L"Windows Server 2016+ (NT 10.0 build %lu)", build);
        }
    } else if (major == 6 && minor == 3) {
        if (productType == 1) {
            swprintf_s(nameBuf, nameBufLen, L"Windows 8.1 (NT 6.3 build %lu)", build);
        } else {
            swprintf_s(nameBuf, nameBufLen, L"Windows Server 2012 R2 (NT 6.3 build %lu)", build);
        }
    } else if (major == 6 && minor == 2) {
        if (productType == 1) {
            swprintf_s(nameBuf, nameBufLen, L"Windows 8 (NT 6.2 build %lu)", build);
        } else {
            swprintf_s(nameBuf, nameBufLen, L"Windows Server 2012 (NT 6.2 build %lu)", build);
        }
    } else if (major == 6 && minor == 1) {
        if (productType == 1) {
            swprintf_s(nameBuf, nameBufLen, L"Windows 7 (NT 6.1 build %lu)", build);
        } else {
            swprintf_s(nameBuf, nameBufLen, L"Windows Server 2008 R2 (NT 6.1 build %lu)", build);
        }
    } else if (major == 6 && minor == 0) {
        if (productType == 1) {
            swprintf_s(nameBuf, nameBufLen, L"Windows Vista (NT 6.0 build %lu)", build);
        } else {
            swprintf_s(nameBuf, nameBufLen, L"Windows Server 2008 (NT 6.0 build %lu)", build);
        }
    } else {
        swprintf_s(nameBuf, nameBufLen, L"Windows NT %lu.%lu build %lu", major, minor, build);
    }
}

/* ------------------------------------------------------------------ */
/*  路径初始化                                                          */
/* ------------------------------------------------------------------ */
static int init_paths(void)
{
    WCHAR exePath[MAX_PATH_BUF];

    /* 获取自身 exe 所在目录作为 rootDir */
    if (!GetModuleFileNameW(NULL, exePath, MAX_PATH_BUF)) {
        MessageBoxW(NULL,
            L"GetModuleFileNameW 失败，无法确定程序路径。",
            L"WNMP Panel 启动失败", MB_OK | MB_ICONERROR);
        return 0;
    }

    /* 去掉文件名，保留目录 */
    WCHAR *lastSlash = wcsrchr(exePath, L'\\');
    if (lastSlash) *lastSlash = 0;

    /* 处理路径末尾反斜杠 */
    wcscpy_s(g_rootDir, MAX_PATH_BUF, exePath);

    /* runtime.ini 路径 */
    PathCombineW(g_iniPath, g_rootDir, L"config\\runtime.ini");

    /* python.exe 路径 */
    PathCombineW(g_pythonExe, g_rootDir, L"bin\\python\\python.exe");

    /* 日志目录 */
    PathCombineW(g_logDir, g_rootDir, L"logs\\panel");

    /* launcher.log 路径 */
    PathCombineW(g_logPath, g_logDir, L"launcher.log");

    /* panel_server.log 路径 */
    PathCombineW(g_panelLogPath, g_logDir, L"panel_server.log");

    return 1;
}

/* ------------------------------------------------------------------ */
/*  确保日志目录存在                                                     */
/* ------------------------------------------------------------------ */
static int ensure_log_dir(void)
{
    /* 创建 logs 目录 */
    WCHAR logsDir[MAX_PATH_BUF];
    PathCombineW(logsDir, g_rootDir, L"logs");
    CreateDirectoryW(logsDir, NULL);

    /* 创建 logs\panel 目录 */
    CreateDirectoryW(g_logDir, NULL);

    return 1;
}

/* ------------------------------------------------------------------ */
/*  runtime.ini 解析                                                    */
/* ------------------------------------------------------------------ */
static void parse_runtime_ini(void)
{
    FILE *fp = _wfopen(g_iniPath, L"rb");
    if (!fp) {
        /* 配置文件缺失，使用默认值 */
        wcscpy_s(g_panelHost, 256, DEFAULT_PANEL_HOST);
        g_panelPort = DEFAULT_PANEL_PORT;
        g_autoOpenBrowser = DEFAULT_AUTO_OPEN;
        log_write(L"runtime.ini 不存在: %s，使用默认值", g_iniPath);
        return;
    }

    /* 读取文件内容，支持 UTF-8 BOM */
    fseek(fp, 0, SEEK_END);
    long fsize = ftell(fp);
    fseek(fp, 0, SEEK_SET);

    if (fsize <= 0) {
        fclose(fp);
        wcscpy_s(g_panelHost, 256, DEFAULT_PANEL_HOST);
        g_panelPort = DEFAULT_PANEL_PORT;
        g_autoOpenBrowser = DEFAULT_AUTO_OPEN;
        log_write(L"runtime.ini 为空: %s，使用默认值", g_iniPath);
        return;
    }

    char *buf = (char *)malloc(fsize + 1);
    if (!buf) {
        fclose(fp);
        wcscpy_s(g_panelHost, 256, DEFAULT_PANEL_HOST);
        g_panelPort = DEFAULT_PANEL_PORT;
        g_autoOpenBrowser = DEFAULT_AUTO_OPEN;
        log_write(L"内存分配失败，使用默认值");
        return;
    }

    fread(buf, 1, fsize, fp);
    buf[fsize] = 0;
    fclose(fp);

    /* 跳过 UTF-8 BOM */
    char *data = buf;
    if ((unsigned char)data[0] == 0xEF &&
        (unsigned char)data[1] == 0xBB &&
        (unsigned char)data[2] == 0xBF) {
        data += 3;
    }

    /* 临时存储解析结果，标记是否找到 */
    int foundHost = 0, foundPort = 0, foundAuto = 0;
    WCHAR tmpHost[256] = {0};
    int tmpPort = 0;
    int tmpAuto = 0;

    /* 逐行解析 */
    char *line = strtok(data, "\n");
    while (line) {
        char *trimmed = trim_a(line);

        /* 跳过空行和注释 */
        if (trimmed[0] == 0 || trimmed[0] == '#') {
            line = strtok(NULL, "\n");
            continue;
        }

        /* 查找 KEY=VALUE */
        char *eq = strchr(trimmed, '=');
        if (!eq) {
            line = strtok(NULL, "\n");
            continue;
        }

        *eq = 0;
        char *key = trim_a(trimmed);
        char *val = trim_a(eq + 1);

        /* 转换 key 为宽字符串进行比较 */
        WCHAR wkey[256];
        MultiByteToWideChar(CP_UTF8, 0, key, -1, wkey, 256);

        if (wstrieq(wkey, L"PANEL_HOST")) {
            WCHAR wval[256];
            MultiByteToWideChar(CP_UTF8, 0, val, -1, wval, 256);
            WCHAR *tv = trim_w(wval);
            if (tv[0] != 0) {
                wcscpy_s(tmpHost, 256, tv);
                foundHost = 1;
            }
        }
        else if (wstrieq(wkey, L"PANEL_PORT")) {
            WCHAR wval[256];
            MultiByteToWideChar(CP_UTF8, 0, val, -1, wval, 256);
            WCHAR *tv = trim_w(wval);
            int port = _wtoi(tv);
            if (port >= 1 && port <= 65535) {
                tmpPort = port;
                foundPort = 1;
            } else {
                log_write(L"警告: PANEL_PORT 值非法 '%s'，回退默认值 %d", tv, DEFAULT_PANEL_PORT);
            }
        }
        else if (wstrieq(wkey, L"AUTO_OPEN_BROWSER")) {
            WCHAR wval[256];
            MultiByteToWideChar(CP_UTF8, 0, val, -1, wval, 256);
            WCHAR *tv = trim_w(wval);
            if (wstrieq(tv, L"1") || wstrieq(tv, L"true") || wstrieq(tv, L"yes")) {
                tmpAuto = 1;
                foundAuto = 1;
            } else if (wstrieq(tv, L"0") || wstrieq(tv, L"false") || wstrieq(tv, L"no")) {
                tmpAuto = 0;
                foundAuto = 1;
            } else {
                log_write(L"警告: AUTO_OPEN_BROWSER 值非法 '%s'，回退默认值", tv);
            }
        }

        line = strtok(NULL, "\n");
    }

    free(buf);

    /* 应用解析结果或默认值 */
    if (foundHost) {
        wcscpy_s(g_panelHost, 256, tmpHost);
    } else {
        wcscpy_s(g_panelHost, 256, DEFAULT_PANEL_HOST);
        log_write(L"runtime.ini 缺少 PANEL_HOST，使用默认值 %s", DEFAULT_PANEL_HOST);
    }

    if (foundPort) {
        g_panelPort = tmpPort;
    } else {
        g_panelPort = DEFAULT_PANEL_PORT;
        log_write(L"runtime.ini 缺少 PANEL_PORT，使用默认值 %d", DEFAULT_PANEL_PORT);
    }

    if (foundAuto) {
        g_autoOpenBrowser = tmpAuto;
    } else {
        g_autoOpenBrowser = DEFAULT_AUTO_OPEN;
        log_write(L"runtime.ini 缺少 AUTO_OPEN_BROWSER，使用默认值 %d", DEFAULT_AUTO_OPEN);
    }
}

/* ------------------------------------------------------------------ */
/*  命令行参数解析                                                       */
/* ------------------------------------------------------------------ */
static void parse_cmdline(void)
{
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return;

    for (int i = 1; i < argc; i++) {
        if (wstrieq(argv[i], L"--host") && i + 1 < argc) {
            wcscpy_s(g_panelHost, 256, argv[i + 1]);
            log_write(L"命令行覆盖 PANEL_HOST=%s", g_panelHost);
            i++;
        }
        else if (wstrieq(argv[i], L"--port") && i + 1 < argc) {
            int port = _wtoi(argv[i + 1]);
            if (port >= 1 && port <= 65535) {
                g_panelPort = port;
                log_write(L"命令行覆盖 PANEL_PORT=%d", g_panelPort);
            } else {
                log_write(L"警告: 命令行 --port 值非法 '%s'，忽略", argv[i + 1]);
            }
            i++;
        }
        else if (wstrieq(argv[i], L"--no-browser")) {
            g_autoOpenBrowser = 0;
            log_write(L"命令行覆盖 AUTO_OPEN_BROWSER=0 (--no-browser)");
        }
        else if (wstrieq(argv[i], L"--debug")) {
            g_debug = 1;
            log_write(L"命令行 --debug 已启用");
        }
        else if (wstrieq(argv[i], L"--test-os-version")) {
            g_testOsVersion = 1;
        }
    }

    LocalFree(argv);
}

/* ------------------------------------------------------------------ */
/*  计算 probeHost 和 browserHost                                       */
/* ------------------------------------------------------------------ */
static void compute_hosts(void)
{
    if (wstrieq(g_panelHost, L"0.0.0.0")) {
        wcscpy_s(g_probeHost, 256, L"127.0.0.1");
        wcscpy_s(g_browserHost, 256, L"127.0.0.1");
    } else {
        wcscpy_s(g_probeHost, 256, g_panelHost);
        wcscpy_s(g_browserHost, 256, g_panelHost);
    }
}

/* ------------------------------------------------------------------ */
/*  HTTP 探测：检查 Panel 是否已运行                                     */
/*  mode=0: 仅检查 HTTP 状态码（快速探测，用于等待子进程就绪）            */
/*  mode=1: 检查状态码 + 响应体含 "WNMP Panel"（严格探测，用于区分 Panel  */
/*          和其它 HTTP 服务）                                           */
/* ------------------------------------------------------------------ */
static int http_probe_ex(const WCHAR *host, int port, const WCHAR *path,
                         int timeoutMs, int mode)
{
    HINTERNET hSession = NULL, hConnect = NULL, hRequest = NULL;
    int result = 0;

    /* 使用 NO_PROXY 避免本地探测被系统代理干扰 */
    hSession = WinHttpOpen(L"WNMPanel",
                           WINHTTP_ACCESS_TYPE_NO_PROXY,
                           WINHTTP_NO_PROXY_NAME,
                           WINHTTP_NO_PROXY_BYPASS, 0);
    if (!hSession) return 0;

    /* 设置超时：resolve=1s, connect=2s, send=2s, recv=timeoutMs */
    WinHttpSetTimeouts(hSession, 1000, 2000, 2000, timeoutMs);

    hConnect = WinHttpConnect(hSession, host, (INTERNET_PORT)port, 0);
    if (!hConnect) {
        WinHttpCloseHandle(hSession);
        return 0;
    }

    hRequest = WinHttpOpenRequest(hConnect, L"GET", path,
                                  NULL, WINHTTP_NO_REFERER,
                                  WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
    if (!hRequest) {
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return 0;
    }

    if (WinHttpSendRequest(hRequest, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                           WINHTTP_NO_REQUEST_DATA, 0, 0, 0)) {
        if (WinHttpReceiveResponse(hRequest, NULL)) {
            DWORD statusCode = 0;
            DWORD sz = sizeof(statusCode);
            if (WinHttpQueryHeaders(hRequest,
                                    WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                    NULL, &statusCode, &sz, NULL)) {
                if (statusCode == 200) {
                    if (mode == 0) {
                        /* 快速模式：仅检查状态码 200 */
                        result = 1;
                    } else {
                        /* 严格模式：读取响应体，检查含 "WNMP Panel" */
                        char body[4096] = {0};
                        DWORD totalRead = 0;
                        DWORD avail = 0;
                        while (WinHttpQueryDataAvailable(hRequest, &avail) && avail > 0) {
                            DWORD toRead = avail;
                            if (totalRead + toRead > sizeof(body) - 1)
                                toRead = sizeof(body) - 1 - totalRead;
                            if (toRead == 0) break;
                            DWORD bytesRead = 0;
                            WinHttpReadData(hRequest, body + totalRead, toRead, &bytesRead);
                            totalRead += bytesRead;
                        }
                        body[totalRead] = '\0';
                        if (strstr(body, "WNMP Panel")) {
                            result = 1;
                        }
                    }
                }
            }
        }
    }

    WinHttpCloseHandle(hRequest);
    WinHttpCloseHandle(hConnect);
    WinHttpCloseHandle(hSession);
    return result;
}

/* 快速探测（仅检查 HTTP 200，用于等待子进程就绪） */
static int http_probe(const WCHAR *host, int port, const WCHAR *path, int timeoutMs)
{
    return http_probe_ex(host, port, path, timeoutMs, 0);
}

/* 严格探测（检查 HTTP 200 + 响应体含 "WNMP Panel"，用于区分 Panel） */
static int http_probe_strict(const WCHAR *host, int port, const WCHAR *path, int timeoutMs)
{
    return http_probe_ex(host, port, path, timeoutMs, 1);
}

/* ------------------------------------------------------------------ */
/*  检查端口是否被占用（尝试连接）                                       */
/* ------------------------------------------------------------------ */
static int is_port_in_use(const WCHAR *host, int port)
{
    /* 严格探测是否是本 Panel（/api/ping 返回 200 且响应体含 "WNMP Panel"） */
    if (http_probe_strict(host, port, L"/api/ping", 3000)) {
        return 2;  /* Panel 已在运行 */
    }

    /* 尝试 TCP 连接检测端口是否被占用 */
    SOCKET sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock == INVALID_SOCKET) return 0;

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((u_short)port);

    /* 将宽字符 host 转换为 ANSI */
    char ahost[256];
    WideCharToMultiByte(CP_ACP, 0, host, -1, ahost, 256, NULL, NULL);
    addr.sin_addr.s_addr = inet_addr(ahost);

    u_long mode = 1;
    ioctlsocket(sock, FIONBIO, &mode);

    /* 显式检查 connect 返回值，避免忽略非阻塞连接中的真实错误 */
    int rc = connect(sock, (struct sockaddr *)&addr, sizeof(addr));
    if (rc == 0) {
        /* 非阻塞 connect 立即成功（极少见），端口被占用 */
        closesocket(sock);
        return 1;
    }
    /* rc == SOCKET_ERROR 时，检查 WSAGetLastError */
    int wsaErr = WSAGetLastError();
    if (wsaErr != WSAEWOULDBLOCK && wsaErr != WSAEINPROGRESS &&
        wsaErr != WSAEALREADY && wsaErr != WSAEINVAL) {
        /* 非阻塞连接进行中以外的错误：连接被拒绝、网络不可达等，端口空闲 */
        log_write(L"is_port_in_use: connect 失败, WSAError=%d, 视为端口空闲", wsaErr);
        closesocket(sock);
        return 0;
    }

    fd_set writefds;
    FD_ZERO(&writefds);
    FD_SET(sock, &writefds);

    struct timeval tv;
    tv.tv_sec = 1;
    tv.tv_usec = 0;

    int sel = select(0, NULL, &writefds, NULL, &tv);
    if (sel > 0) {
        /* select 返回可写，还需用 getsockopt(SO_ERROR) 判断最终连接结果 */
        int sockErr = 0;
        int errLen = sizeof(sockErr);
        getsockopt(sock, SOL_SOCKET, SO_ERROR, (char *)&sockErr, &errLen);
        if (sockErr == 0) {
            closesocket(sock);
            return 1;  /* 连接成功，端口被占用 */
        }
        /* SO_ERROR 非零（如 ECONNREFUSED），端口空闲 */
        log_write(L"is_port_in_use: select 成功但 SO_ERROR=%d, 视为端口空闲", sockErr);
    }
    closesocket(sock);
    return 0;  /* 端口空闲 */
}

/* ------------------------------------------------------------------ */
/*  Winsock 初始化                                                      */
/* ------------------------------------------------------------------ */
static int init_winsock(void)
{
    WSADATA wsaData;
    return (WSAStartup(MAKEWORD(2, 2), &wsaData) == 0);
}

/* ------------------------------------------------------------------ */
/*  打开浏览器                                                          */
/* ------------------------------------------------------------------ */
static void open_browser(const WCHAR *host, int port)
{
    WCHAR url[512];
    swprintf_s(url, 512, L"http://%s:%d", host, port);
    log_write(L"打开浏览器: %s", url);
    ShellExecuteW(NULL, L"open", url, NULL, NULL, SW_SHOWNORMAL);
}

/* ------------------------------------------------------------------ */
/*  显示错误弹窗                                                        */
/* ------------------------------------------------------------------ */
static void show_error(const WCHAR *message)
{
    MessageBoxW(NULL, message, L"WNMP Panel 启动失败", MB_OK | MB_ICONERROR);
}

/* ------------------------------------------------------------------ */
/*  主流程                                                              */
/* ------------------------------------------------------------------ */
int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance,
                    LPWSTR lpCmdLine, int nCmdShow)
{
    (void)hInstance; (void)hPrevInstance; (void)lpCmdLine; (void)nCmdShow;

    /* 0. 提前扫描 --test-os-version 标志（需在 init_paths/log_init 之前识别） */
    {
        int argc = 0;
        LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
        if (argv) {
            for (int i = 1; i < argc; i++) {
                if (wstrieq(argv[i], L"--test-os-version")) {
                    g_testOsVersion = 1;
                    break;
                }
            }
            LocalFree(argv);
        }
    }

    /* 1. 初始化路径 */
    if (!init_paths()) {
        return 1;
    }

    /* 2. 确保日志目录存在 */
    ensure_log_dir();

    /* 3. 初始化日志 */
    log_init();

    log_write(L"========== WNMPanel.exe 启动 ==========");
    log_write(L"rootDir: %s", g_rootDir);
    log_write(L"runtime.ini: %s", g_iniPath);

    /* 3a. --test-os-version 测试模式：运行 OS 检测纯函数模拟测试后退出 */
    if (g_testOsVersion) {
        /* 分配控制台窗口并绑定 stdin/stdout/stderr */
        AllocConsole();
        FILE *conIn = NULL;
        FILE *conOut = NULL;
        FILE *conErr = NULL;
        freopen_s(&conIn, "CONIN$", "r", stdin);
        freopen_s(&conOut, "CONOUT$", "w", stdout);
        freopen_s(&conErr, "CONOUT$", "w", stderr);

        wprintf(L"=== OS Version Detection Test ===\n\n");

        /* 测试 1: IsSupportedWindowsVersion 纯函数模拟 */
        struct { DWORD major; DWORD minor; int expected; const WCHAR *desc; } testCases[] = {
            { 6, 1, 0, L"Windows 7 (NT 6.1)" },
            { 6, 2, 0, L"Windows 8 (NT 6.2)" },
            { 6, 3, 0, L"Windows 8.1 (NT 6.3)" },
            { 10, 0, 1, L"Windows 10 (NT 10.0)" },
            { 10, 1, 1, L"Future NT 10.1" },
            { 11, 0, 1, L"Future NT 11.0" },
        };
        int passCount = 0;
        int failCount = 0;
        for (int i = 0; i < (int)(sizeof(testCases)/sizeof(testCases[0])); i++) {
            int result = IsSupportedWindowsVersion(testCases[i].major, testCases[i].minor);
            int ok = (result == testCases[i].expected);
            wprintf(L"  [%s] %s: IsSupported(%lu,%lu)=%d expected=%d\n",
                    ok ? L"PASS" : L"FAIL", testCases[i].desc,
                    testCases[i].major, testCases[i].minor,
                    result, testCases[i].expected);
            if (ok) passCount++; else failCount++;
        }
        wprintf(L"\n  Simulated tests: %d passed, %d failed\n\n", passCount, failCount);

        /* 测试 2: 真实系统检测 */
        DWORD realMajor = 0, realMinor = 0, realBuild = 0;
        BYTE realProductType = 0;
        if (GetRealWindowsVersion(&realMajor, &realMinor, &realBuild, &realProductType)) {
            WCHAR friendlyName[256] = {0};
            FormatWindowsVersionName(realMajor, realMinor, realBuild, realProductType,
                                     friendlyName, 256);
            int supported = IsSupportedWindowsVersion(realMajor, realMinor);
            wprintf(L"  Real system: %s\n", friendlyName);
            wprintf(L"  major=%lu minor=%lu build=%lu productType=%u supported=%d\n",
                    realMajor, realMinor, realBuild, (unsigned)realProductType, supported);
        } else {
            wprintf(L"  Real system: RtlGetVersion failed (non-Windows or ntdll missing)\n");
        }

        wprintf(L"\n=== Test complete ===\n");
        log_write(L"--test-os-version: 模拟测试 %d passed %d failed", passCount, failCount);

        /* 等待用户按键后退出 */
        wprintf(L"\nPress Enter to exit...");
        WCHAR inputBuf[16];
        fgetws(inputBuf, 16, stdin);
        if (conIn) fclose(conIn);
        if (conOut) fclose(conOut);
        if (conErr) fclose(conErr);
        FreeConsole();
        return (failCount > 0) ? 1 : 0;
    }

    /* 3b. 操作系统最低版本基线检测（优先级高于权限诊断、Python 启动等所有后续步骤） */
    {
        DWORD osMajor = 0, osMinor = 0, osBuild = 0;
        BYTE osProductType = 0;

        if (!GetRealWindowsVersion(&osMajor, &osMinor, &osBuild, &osProductType)) {
            /* RtlGetVersion 调用失败，保守阻断 */
            log_write(L"OS检测: RtlGetVersion 调用失败，保守阻断启动");
            MessageBoxW(NULL,
                L"无法检测当前 Windows 版本，出于安全原因已停止启动。\n"
                L"Unable to detect Windows version. Startup has been stopped for safety.",
                L"WNMP Panel 启动失败", MB_OK | MB_ICONERROR);
            return 1;
        }

        WCHAR friendlyName[256] = {0};
        FormatWindowsVersionName(osMajor, osMinor, osBuild, osProductType, friendlyName, 256);

        int supported = IsSupportedWindowsVersion(osMajor, osMinor);
        log_write(L"OS检测: version=%lu.%lu build=%lu productType=%u name=%s supported=%d",
                  osMajor, osMinor, osBuild, (unsigned)osProductType, friendlyName, supported);

        if (!supported) {
            log_write(L"OS检测: 系统版本低于基线(NT 10.0)，阻断启动");
            WCHAR fullMsg[1024];
            swprintf_s(fullMsg, 1024,
                L"当前系统版本不受支持 / Unsupported Windows version\n\n"
                L"WNMP Runtime 支持 Windows 10 / 11 和 Windows Server 2016 及以上版本。\n"
                L"当前系统版本过低，可能无法运行内置 Python、Nginx、PHP-CGI 或 MySQL。\n"
                L"请升级操作系统后再使用。\n\n"
                L"Supported: Windows 10/11 and Windows Server 2016 or later.\n"
                L"Current: %s", friendlyName);
            MessageBoxW(NULL, fullMsg,
                L"WNMP Panel 启动失败", MB_OK | MB_ICONERROR);
            return 1;
        }

        log_write(L"OS检测: 系统版本满足基线要求，继续启动");
    }

    /* 权限诊断：记录当前进程是否管理员、完整性级别、当前用户名 */
    {
        BOOL isAdmin = FALSE;
        /* 方法1: CheckTokenMembership 检查是否在 Administrators 组 */
        SID_IDENTIFIER_AUTHORITY ntAuth = SECURITY_NT_AUTHORITY;
        PSID adminGroup = NULL;
        if (AllocateAndInitializeSid(&ntAuth, 2,
                SECURITY_BUILTIN_DOMAIN_RID, DOMAIN_ALIAS_RID_ADMINS,
                0, 0, 0, 0, 0, 0, &adminGroup)) {
            CheckTokenMembership(NULL, adminGroup, &isAdmin);
            FreeSid(adminGroup);
        }
        log_write(L"权限诊断: is_admin=%d (CheckTokenMembership)", isAdmin);

        /* 方法2: GetTokenInformation(TokenIntegrityLevel) 获取完整性级别 */
        HANDLE hToken = NULL;
        if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &hToken)) {
            DWORD needed = 0;
            GetTokenInformation(hToken, TokenIntegrityLevel, NULL, 0, &needed);
            if (needed > 0) {
                BYTE *buf = (BYTE *)malloc(needed);
                if (buf) {
                    if (GetTokenInformation(hToken, TokenIntegrityLevel, buf, needed, &needed)) {
                        TOKEN_MANDATORY_LABEL *tml = (TOKEN_MANDATORY_LABEL *)buf;
                        DWORD intLevel = *GetSidSubAuthority(tml->Label.Sid,
                            *GetSidSubAuthorityCount(tml->Label.Sid) - 1);
                        /* 0x1000=Low, 0x2000=Medium, 0x3000=High, 0x4000=System */
                        const WCHAR *levelName = L"Unknown";
                        if (intLevel >= 0x4000) levelName = L"System";
                        else if (intLevel >= 0x3000) levelName = L"High";
                        else if (intLevel >= 0x2000) levelName = L"Medium";
                        else if (intLevel >= 0x1000) levelName = L"Low";
                        log_write(L"权限诊断: integrity_level=0x%X (%s)", intLevel, levelName);
                    }
                    free(buf);
                }
            }
            CloseHandle(hToken);
        }

        /* 当前用户名 */
        WCHAR userName[256] = {0};
        DWORD nameLen = 256;
        if (GetUserNameW(userName, &nameLen)) {
            log_write(L"权限诊断: user=%s", userName);
        }
    }

    /* 4. 解析 runtime.ini */
    parse_runtime_ini();
    log_write(L"runtime.ini 解析结果: PANEL_HOST=%s, PANEL_PORT=%d, AUTO_OPEN_BROWSER=%d",
              g_panelHost, g_panelPort, g_autoOpenBrowser);

    /* 5. 解析命令行参数（覆盖 ini 配置） */
    parse_cmdline();

    /* 6. 计算 probeHost / browserHost */
    compute_hosts();
    log_write(L"final_host=%s, final_port=%d, probe_host=%s, browser_host=%s",
              g_panelHost, g_panelPort, g_probeHost, g_browserHost);

    /* 7. 检查内置 Python 是否存在 */
    if (GetFileAttributesW(g_pythonExe) == INVALID_FILE_ATTRIBUTES) {
        WCHAR msg[1024];
        swprintf_s(msg, 1024,
            L"缺少项目内置 Python：bin\\python\\python.exe\n\n"
            L"路径：%s\n\n"
            L"请检查程序包是否完整。\n\n"
            L"详情请查看 logs\\panel\\launcher.log",
            g_pythonExe);
        log_write(L"错误: 内置 Python 不存在: %s", g_pythonExe);
        show_error(msg);
        return 1;
    }
    log_write(L"pythonExe: %s", g_pythonExe);

    /* 8. 初始化 Winsock（用于端口探测） */
    if (!init_winsock()) {
        log_write(L"警告: WSAStartup 失败，跳过端口探测");
    }

    /* 9. 启动前端口探测 */
    int portStatus = is_port_in_use(g_probeHost, g_panelPort);
    if (portStatus == 2) {
        /* Panel 已在运行，直接打开浏览器并退出 */
        log_write(L"Panel 已在运行 (http://%s:%d/api/ping 响应正常)，直接打开浏览器",
                  g_probeHost, g_panelPort);
        if (g_autoOpenBrowser) {
            open_browser(g_browserHost, g_panelPort);
        }
        WSACleanup();
        return 0;
    }
    else if (portStatus == 1) {
        /* 端口被占用但不是 Panel */
        WCHAR msg[1024];
        swprintf_s(msg, 1024,
            L"端口 %d 已被占用，请修改 config\\runtime.ini 中 PANEL_PORT 或关闭占用程序。\n\n"
            L"详情请查看 logs\\panel\\launcher.log",
            g_panelPort);
        log_write(L"错误: 端口 %d 被占用但不是 Panel", g_panelPort);
        show_error(msg);
        WSACleanup();
        return 1;
    }
    log_write(L"端口 %d 空闲，准备启动 Panel", g_panelPort);

    /* 10. 启动前文件存在校验 */
    {
        WCHAR checkPath[MAX_PATH_BUF];
        const WCHAR *missingFiles[] = {
            L"runtime\\panel_server.py",
            L"runtime\\wnmpctl.py",
            L"runtime\\__init__.py",
            L"bin\\python\\python.exe",
            NULL
        };
        for (int i = 0; missingFiles[i] != NULL; i++) {
            PathCombineW(checkPath, g_rootDir, missingFiles[i]);
            if (!PathFileExistsW(checkPath)) {
                WCHAR msg[1024];
                swprintf_s(msg, 1024,
                    L"运行包不完整，缺少必要文件：\n\n"
                    L"  %s\n\n"
                    L"请重新下载完整的 WNMP Runtime 包。",
                    missingFiles[i]);
                log_write(L"错误: 缺少必要文件 %s (完整路径: %s)", missingFiles[i], checkPath);
                show_error(msg);
                WSACleanup();
                return 1;
            }
        }
        log_write(L"启动前文件校验通过");
    }

    /* 11. 构造命令行：使用绝对脚本路径，不使用 -m */
    WCHAR panelScript[MAX_PATH_BUF];
    PathCombineW(panelScript, g_rootDir, L"runtime\\panel_server.py");

    WCHAR cmdLine[MAX_CMDLINE];
    swprintf_s(cmdLine, MAX_CMDLINE,
        L"\"%s\" -u \"%s\" --host %s --port %d --no-browser",
        g_pythonExe, panelScript, g_panelHost, g_panelPort);

    /* 保存日志用 */
    wcscpy_s(g_finalCmdLine, MAX_CMDLINE, cmdLine);
    wcscpy_s(g_finalCwd, MAX_PATH_BUF, g_rootDir);
    wcscpy_s(g_finalPythonPath, MAX_PATH_BUF, g_pythonExe);

    log_write(L"cmdLine: %s", cmdLine);
    log_write(L"cwd: %s", g_rootDir);

    /* 12. 设置环境变量 PYTHONPATH（兼容优化，非必需） */
    WCHAR pythonPathEnv[MAX_ENV_BUF] = {0};
    DWORD envLen = GetEnvironmentVariableW(L"PYTHONPATH", pythonPathEnv, MAX_ENV_BUF);

    WCHAR newPythonPath[MAX_ENV_BUF];
    if (envLen > 0) {
        swprintf_s(newPythonPath, MAX_ENV_BUF, L"%s;%s", g_rootDir, pythonPathEnv);
    } else {
        wcscpy_s(newPythonPath, MAX_ENV_BUF, g_rootDir);
    }
    SetEnvironmentVariableW(L"PYTHONPATH", newPythonPath);
    log_write(L"PYTHONPATH: %s", newPythonPath);
    wcscpy_s(g_finalPythonPathEnv, MAX_ENV_BUF, newPythonPath);

    /* 12b. 更新嵌入式 Python 的 ._pth 文件（兼容优化，非启动前置条件）
     * 正式入口已改为绝对脚本路径，panel_server.py 自身会通过 sys.path.insert
     * 保证 runtime 可导入。._pth 更新只是兼容优化，写入失败不阻止启动。
     * 策略：扫描 bin/python/ 下的 *._pth 文件，在末尾追加 rootDir 绝对路径。
     * 如果末尾行已经是 rootDir 则跳过，避免重复追加。
     * 注意：._pth 文件是 UTF-8 编码文本，需用 char 读写。 */
    {
        WCHAR pthDir[MAX_PATH_BUF];
        PathCombineW(pthDir, g_rootDir, L"bin\\python");

        WIN32_FIND_DATAW findData;
        WCHAR searchPattern[MAX_PATH_BUF];
        PathCombineW(searchPattern, pthDir, L"*._pth");

        HANDLE hFind = FindFirstFileW(searchPattern, &findData);
        if (hFind != INVALID_HANDLE_VALUE) {
            do {
                WCHAR pthPath[MAX_PATH_BUF];
                PathCombineW(pthPath, pthDir, findData.cFileName);

                /* 将 rootDir 转为 UTF-8 用于匹配和写入 */
                char rootDirUtf8[MAX_PATH_BUF * 3];
                int utf8Len = WideCharToMultiByte(CP_UTF8, 0, g_rootDir, -1,
                    rootDirUtf8, sizeof(rootDirUtf8), NULL, NULL);
                if (utf8Len <= 0) {
                    log_write(L"警告: rootDir 转 UTF-8 失败，跳过 %s", pthPath);
                    continue;
                }
                /* utf8Len 包含末尾 \0，去掉 */
                utf8Len--;

                /* 读取现有内容，检查是否已包含 rootDir */
                char pthContent[8192] = {0};
                DWORD bytesRead = 0;
                HANDLE hPth = CreateFileW(pthPath, GENERIC_READ, FILE_SHARE_READ,
                    NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
                if (hPth != INVALID_HANDLE_VALUE) {
                    ReadFile(hPth, pthContent, sizeof(pthContent) - 1, &bytesRead, NULL);
                    CloseHandle(hPth);
                }
                pthContent[bytesRead] = '\0';

                /* 检查是否已包含 rootDir 行 */
                BOOL alreadyHasRootDir = (strstr(pthContent, rootDirUtf8) != NULL);

                if (!alreadyHasRootDir) {
                    /* 追加 rootDir 到末尾 */
                    hPth = CreateFileW(pthPath, FILE_APPEND_DATA, FILE_SHARE_READ,
                        NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
                    if (hPth != INVALID_HANDLE_VALUE) {
                        /* 确保末尾有换行 */
                        if (bytesRead > 0 && pthContent[bytesRead - 1] != '\n') {
                            const char *crlf = "\r\n";
                            DWORD written;
                            WriteFile(hPth, crlf, (DWORD)strlen(crlf), &written, NULL);
                        }
                        /* 写入 rootDir + 换行 */
                        char appendBuf[MAX_PATH_BUF * 3 + 4];
                        int appendLen2 = snprintf(appendBuf, sizeof(appendBuf), "%s\r\n", rootDirUtf8);
                        DWORD written;
                        WriteFile(hPth, appendBuf, (DWORD)appendLen2, &written, NULL);
                        CloseHandle(hPth);
                        log_write(L"已更新 %s，追加 rootDir: %s", pthPath, g_rootDir);
                    } else {
                        /* 写入失败时记录警告，不阻止启动（绝对脚本路径不依赖 ._pth） */
                        DWORD pthErr = GetLastError();
                        log_write(L"警告: 无法写入 %s (错误码: %lu)，不影响启动（绝对脚本路径不依赖 ._pth）", pthPath, pthErr);
                    }
                } else {
                    log_write(L"%s 已包含 rootDir，无需更新", pthPath);
                }
            } while (FindNextFileW(hFind, &findData));
            FindClose(hFind);
        } else {
            log_write(L"未找到 ._pth 文件，跳过更新（非嵌入式 Python 或标准安装）");
        }
    }

    /* 13. 创建标准句柄重定向 */
    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(SECURITY_ATTRIBUTES);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = NULL;

    /* 创建 panel_server.log 句柄（追加写入） */
    HANDLE hPanelLog = CreateFileW(g_panelLogPath,
        FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
        &sa, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);

    /* 创建 NUL 句柄用于 stdin */
    HANDLE hNul = CreateFileW(L"NUL",
        GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
        &sa, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);

    int useStdHandles = 0;
    if (hPanelLog != INVALID_HANDLE_VALUE && hNul != INVALID_HANDLE_VALUE) {
        useStdHandles = 1;
    } else {
        log_write(L"警告: 无法创建标准句柄 (panelLog=%p, nul=%p)，不启用 STARTF_USESTDHANDLES",
                  hPanelLog, hNul);
    }

    /* 14. 设置 STARTUPINFO */
    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);

    DWORD creationFlags = CREATE_NO_WINDOW;
    BOOL bInheritHandles = FALSE;

    if (useStdHandles) {
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = hNul;
        si.hStdOutput = hPanelLog;
        si.hStdError = hPanelLog;
        bInheritHandles = TRUE;
        log_write(L"标准句柄重定向已启用: stdin→NUL, stdout/stderr→panel_server.log");
    } else {
        si.dwFlags = 0;
        bInheritHandles = FALSE;
        log_write(L"标准句柄重定向未启用");
    }

    /* 15. 创建 Python 子进程 */
    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    /* lpApplicationName: 未加引号的 python.exe 绝对路径 */
    /* lpCommandLine: 可写缓冲区，包含带引号的 python.exe 路径和参数 */
    /* lpCurrentDirectory: 未加引号的 rootDir */
    WCHAR cmdLineBuf[MAX_CMDLINE];
    wcscpy_s(cmdLineBuf, MAX_CMDLINE, cmdLine);

    log_write(L"CreateProcessW 参数:");
    log_write(L"  lpApplicationName=%s", g_pythonExe);
    log_write(L"  lpCommandLine=%s", cmdLineBuf);
    log_write(L"  lpCurrentDirectory=%s", g_rootDir);
    log_write(L"  bInheritHandles=%d", bInheritHandles);
    log_write(L"  dwCreationFlags=0x%X", creationFlags);

    BOOL cpResult = CreateProcessW(
        g_pythonExe,       /* lpApplicationName: 未加引号的 python.exe 路径 */
        cmdLineBuf,        /* lpCommandLine: 可写缓冲区 */
        NULL,              /* lpProcessAttributes */
        NULL,              /* lpThreadAttributes */
        bInheritHandles,   /* bInheritHandles */
        creationFlags,     /* dwCreationFlags */
        NULL,              /* lpEnvironment: 继承父进程环境（已通过 SetEnvironmentVariable 设置 PYTHONPATH） */
        g_rootDir,         /* lpCurrentDirectory: rootDir */
        &si,               /* lpStartupInfo */
        &pi                /* lpProcessInformation */
    );

    if (!cpResult) {
        DWORD err = GetLastError();
        WCHAR msg[1024];
        swprintf_s(msg, 1024,
            L"CreateProcessW 失败 (错误码: %lu)\n\n"
            L"pythonExe: %s\n"
            L"cmdLine: %s\n"
            L"cwd: %s\n\n"
            L"详情请查看 logs\\panel\\launcher.log 和 logs\\panel\\panel_server.log",
            err, g_pythonExe, cmdLine, g_rootDir);
        log_write(L"CreateProcessW 失败: GetLastError=%lu", err);
        show_error(msg);

        if (hPanelLog != INVALID_HANDLE_VALUE) CloseHandle(hPanelLog);
        if (hNul != INVALID_HANDLE_VALUE) CloseHandle(hNul);
        WSACleanup();
        return 1;
    }

    log_write(L"CreateProcessW 成功: PID=%lu, TID=%lu", pi.dwProcessId, pi.dwThreadId);
    log_write(L"权限诊断: python.exe PID=%lu (由 WNMPanel.exe 启动，应继承权限)", pi.dwProcessId);

    /* 16. 关闭不需要的句柄 */
    /* 注意：hPanelLog 和 hNul 已被子进程继承，但父进程不再需要它们。
       不关闭 hPanelLog，因为子进程可能还在写入。等子进程结束后再关。
       hNul 可以安全关闭。 */
    if (hNul != INVALID_HANDLE_VALUE) CloseHandle(hNul);

    /* 17. 等待 Panel 就绪（快速模式）
     * 最多等待 PANEL_READY_TIMEOUT_MS，ping 成功立即打开浏览器。
     * ping 未成功但子进程仍在运行，也直接打开浏览器，让 Web 页面显示 loading-view。
     * 只有子进程提前退出才弹错。启动器不等待 /api/status。 */
    log_write(L"等待 Panel 就绪 (快速模式，最多 %dms)...", PANEL_READY_TIMEOUT_MS);

    int panelReady = 0;
    int childExited = 0;
    DWORD elapsed = 0;
    DWORD exitCode = 0;

    while (elapsed < PANEL_READY_TIMEOUT_MS) {
        /* 检查子进程是否已退出 */
        if (GetExitCodeProcess(pi.hProcess, &exitCode) && exitCode != STILL_ACTIVE) {
            childExited = 1;
            log_write(L"Panel 子进程提前退出，exitCode=%lu", exitCode);
            break;
        }

        /* 只探测 /api/ping（轻量，不触发组件检测），不再回退探测 / */
        if (http_probe(g_probeHost, g_panelPort, L"/api/ping", 500)) {
            panelReady = 1;
            log_write(L"Panel 已就绪 (http://%s:%d/api/ping 响应正常)",
                      g_probeHost, g_panelPort);
            break;
        }

        Sleep(PANEL_READY_POLL_MS);
        elapsed += PANEL_READY_POLL_MS;
    }

    if (childExited) {
        WCHAR msg[1024];
        swprintf_s(msg, 1024,
            L"Panel 启动失败，子进程已提前退出 (exitCode=%lu)。\n\n"
            L"请查看以下日志获取详细错误信息：\n"
            L"  %s\n"
            L"  %s\n\n"
            L"端口: %d",
            exitCode, g_panelLogPath, g_logPath, g_panelPort);
        show_error(msg);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        if (hPanelLog != INVALID_HANDLE_VALUE) CloseHandle(hPanelLog);
        WSACleanup();
        return 1;
    }

    if (!panelReady) {
        /* 快速模式：ping 未成功但子进程仍在运行，直接打开浏览器
         * 让 Web 页面显示 loading-view，用户可看到加载状态 */
        log_write(L"Panel 未在 %dms 内响应 ping，但子进程仍在运行，直接打开浏览器",
                  PANEL_READY_TIMEOUT_MS);
    }

    /* 18. Panel 就绪或子进程仍在运行，打开浏览器 */
    if (g_autoOpenBrowser) {
        open_browser(g_browserHost, g_panelPort);
    } else {
        log_write(L"AUTO_OPEN_BROWSER=0，不打开浏览器");
    }

    log_write(L"========== WNMPanel.exe 启动完成 ==========");

    /* 19. 清理 */
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (hPanelLog != INVALID_HANDLE_VALUE) CloseHandle(hPanelLog);
    WSACleanup();

    return 0;
}
