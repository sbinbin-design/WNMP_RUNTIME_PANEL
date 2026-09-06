# -*- coding: utf-8 -*-
"""
WNMP Config Module - reads config/runtime.ini

端口解析原则：
- runtime.ini 中的 HTTP_PORT/HTTPS_PORT/PHP_CGI_PORT/MYSQL_PORT 仅作为首次生成模板时的默认值
- 初始化完成后，真实端口应以生成后的配置文件为准
- parse_nginx_http_port / parse_nginx_https_port 从 nginx 配置解析 listen 指令
- parse_mysql_port 从 my.ini 解析 [mysqld] port
- parse_php_cgi_port 从 php-cgi.ini 解析 [php-cgi] host/port
- 解析失败时返回 None，调用方应回退到 runtime.ini 默认值并提示"无法解析配置端口"
"""
import os
import re
import sys
from runtime.wnmp_component_paths import (
    get_nginx_conf_path, get_nginx_site_conf_path, get_nginx_vhosts_dir,
    get_nginx_custom_http_dir, get_nginx_custom_server_dir,
    get_mysql_ini_path, get_php_cgi_ini_path,
)


DEFAULTS = {
    "HTTP_PORT": "80",
    "HTTPS_PORT": "443",
    "ENABLE_HTTPS": "1",
    "PHP_CGI_HOST": "127.0.0.1",
    "PHP_CGI_PORT": "9000",
    "PHP_CGI_CHILDREN": "5",
    "PHP_CGI_MAX_REQUESTS": "500",
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PORT": "3306",
    "WEB_ROOT": "./www",
    "MYSQL_DATA_DIR": "./data/mysql",
    "AUTO_OPEN_BROWSER": "1",
    "AUTO_GENERATE_CERT": "1",
    "AUTO_START": "0",
    "START_TIMEOUT": "60",
    "MYSQL_START_TIMEOUT": "90",
    "STOP_TIMEOUT": "30",
    "SERVICE_NAME": "WNMPRuntime",
    "SERVICE_DISPLAY_NAME": "WNMP Runtime Service",
    "PANEL_HOST": "127.0.0.1",
    "PANEL_PORT": "8787",
    "PANEL_EXIT_ON_CLOSE": "1",
    "PANEL_HEARTBEAT_INTERVAL": "5",
    "PANEL_NO_CLIENT_EXIT_SECONDS": "20",
    "PANEL_SHUTDOWN_GRACE_SECONDS": "2",
    "PANEL_VERSION_CACHE_TTL": "600",
    # PANEL_STATUS_ADOPT_PROCESS: [已废弃] 保留默认值仅为向后兼容
    "PANEL_STATUS_ADOPT_PROCESS": "0",
}


def load_config(root_dir):
    """Load config/runtime.ini, return dict with defaults applied."""
    config_path = os.path.join(root_dir, "config", "runtime.ini")
    cfg = dict(DEFAULTS)

    if not os.path.isfile(config_path):
        return cfg

    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key:
                    cfg[key] = value
    except Exception:
        pass

    return cfg


def get(cfg, key, default=None):
    """Get config value with fallback to DEFAULTS then explicit default."""
    if key in cfg:
        return cfg[key]
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


def get_int(cfg, key, default=0):
    """Get config value as int."""
    try:
        return int(get(cfg, key, str(default)))
    except (ValueError, TypeError):
        return default


# ---- 端口解析：从实际配置文件解析，而非 runtime.ini ----

def parse_nginx_listens(root_dir):
    """解析 Nginx 配置文件中所有 listen 指令，返回结构化列表。

    解析 bin/nginx/conf/site.conf 和 bin/nginx/conf/nginx.conf。
    支持格式：listen 80; listen 80 default_server; listen 127.0.0.1:8080;
    listen 0.0.0.0:80; listen [::]:443 ssl; listen 443 ssl http2;
    listen 443 default_server ssl http2;
    忽略整行注释和行尾注释，忽略被注释的 listen。
    返回 [{"port":int, "ssl":bool, "raw":str}, ...]
    """
    # 配置文件列表：site.conf、nginx.conf、vhosts/*.conf、custom/http/*.conf、custom/server/*.conf
    # 路径收敛：通过统一路径模块获取配置文件路径
    config_files = [
        get_nginx_site_conf_path(root_dir),
        get_nginx_conf_path(root_dir),
    ]
    # 纳入 vhosts 目录下的 .conf 文件（排除 .disabled 和非 .conf 文件）
    vhosts_dir = get_nginx_vhosts_dir(root_dir)
    if os.path.isdir(vhosts_dir):
        try:
            for fname in sorted(os.listdir(vhosts_dir)):
                if fname.endswith(".conf") and not fname.endswith(".disabled"):
                    config_files.append(os.path.join(vhosts_dir, fname))
        except Exception:
            pass
    # 纳入 custom/http 和 custom/server 目录下的 .conf 文件，与 compute_component_config_hash 范围一致
    # 路径收敛：通过统一路径模块获取 custom 目录路径
    for custom_dir_func in (get_nginx_custom_http_dir, get_nginx_custom_server_dir):
        custom_dir = custom_dir_func(root_dir)
        if os.path.isdir(custom_dir):
            try:
                for fname in sorted(os.listdir(custom_dir)):
                    if fname.endswith(".conf") and not fname.endswith(".disabled"):
                        config_files.append(os.path.join(custom_dir, fname))
            except Exception:
                pass
    listens = []
    seen = set()  # 去重：(port, ssl)
    for conf_path in config_files:
        if not os.path.isfile(conf_path):
            continue
        try:
            with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            for line in content.split("\n"):
                stripped = line.strip()
                # 跳过整行注释
                if stripped.startswith("#"):
                    continue
                # 去掉行尾注释（# 后面的内容）
                comment_pos = stripped.find("#")
                if comment_pos > 0:
                    stripped = stripped[:comment_pos].strip()
                # 匹配 listen 指令（忽略大小写）
                m = re.match(r'listen\s+(.+?)\s*;', stripped, re.IGNORECASE)
                if not m:
                    continue
                raw_listen = m.group(1).strip()
                is_ssl = "ssl" in raw_listen.lower()

                # 提取端口号：支持多种格式
                # listen 80; listen 80 default_server; listen 443 ssl http2;
                # listen 127.0.0.1:8080; listen 0.0.0.0:80;
                # listen [::]:443 ssl;
                port = None
                # 格式1: address:port（含 IPv6 [::]:port）
                addr_match = re.match(r'(?:\[([0-9a-fA-F:]+)\]|([0-9.]+))?:(\d+)', raw_listen)
                if addr_match:
                    port = int(addr_match.group(3))
                else:
                    # 格式2: 纯端口号
                    port_match = re.match(r'(\d+)', raw_listen)
                    if port_match:
                        port = int(port_match.group(1))

                if port is not None:
                    key = (port, is_ssl)
                    if key not in seen:
                        seen.add(key)
                        listens.append({
                            "port": port,
                            "ssl": is_ssl,
                            "raw": "listen " + raw_listen + ";"
                        })
        except Exception:
            continue
    return listens


def _has_nginx_config_files(root_dir):
    """检查 Nginx 配置文件是否存在且可读。

    返回 (exists, has_listen)：
    - exists: 至少一个配置文件存在
    - has_listen: 至少解析到一个 listen 指令
    """
    # 路径收敛：通过统一路径模块获取配置文件路径
    config_files = [
        get_nginx_site_conf_path(root_dir),
        get_nginx_conf_path(root_dir),
    ]
    # 也检查 vhosts 目录
    vhosts_dir = get_nginx_vhosts_dir(root_dir)
    if os.path.isdir(vhosts_dir):
        try:
            for fname in sorted(os.listdir(vhosts_dir)):
                if fname.endswith(".conf") and not fname.endswith(".disabled"):
                    config_files.append(os.path.join(vhosts_dir, fname))
        except Exception:
            pass
    # 也检查 custom/http 和 custom/server 目录，与 parse_nginx_listens 范围一致
    # 路径收敛：通过统一路径模块获取 custom 目录路径
    for custom_dir_func in (get_nginx_custom_http_dir, get_nginx_custom_server_dir):
        custom_dir = custom_dir_func(root_dir)
        if os.path.isdir(custom_dir):
            try:
                for fname in sorted(os.listdir(custom_dir)):
                    if fname.endswith(".conf") and not fname.endswith(".disabled"):
                        config_files.append(os.path.join(custom_dir, fname))
            except Exception:
                pass
    exists = False
    for conf_path in config_files:
        if not os.path.isfile(conf_path):
            continue
        exists = True
        break
    if not exists:
        return False, False
    # 检查是否能解析到 listen
    listens = parse_nginx_listens(root_dir)
    return True, len(listens) > 0


def parse_nginx_http_port(root_dir):
    """从 Nginx 配置解析第一个 HTTP listen 端口（非 ssl）。

    返回 int 端口号，解析失败返回 None。
    """
    listens = parse_nginx_listens(root_dir)
    for ln in listens:
        if not ln["ssl"]:
            return ln["port"]
    return None


def parse_nginx_https_port(root_dir):
    """从 Nginx 配置解析第一个 HTTPS listen 端口（ssl）。

    返回 int 端口号，解析失败返回 None。
    """
    listens = parse_nginx_listens(root_dir)
    for ln in listens:
        if ln["ssl"]:
            return ln["port"]
    return None


def parse_mysql_port(root_dir):
    """从 bin/mysql/my.ini 解析 [mysqld] port。

    返回 int 端口号，解析失败返回 None。
    """
    # 路径收敛：通过统一路径模块获取 my.ini 路径
    my_ini_path = get_mysql_ini_path(root_dir)
    if not os.path.isfile(my_ini_path):
        return None
    try:
        with open(my_ini_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        in_mysqld = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith(";"):
                continue
            # 检测 section
            if re.match(r'\[mysqld\]', stripped, re.IGNORECASE):
                in_mysqld = True
                continue
            if stripped.startswith("[") and in_mysqld:
                # 进入其他 section，停止解析
                break
            if in_mysqld:
                m = re.match(r'port\s*=\s*(\d+)', stripped, re.IGNORECASE)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return None


def parse_php_cgi_config(root_dir):
    """从 bin/php/php-cgi.ini 解析 PHP-CGI 运行参数。

    返回 dict: {"host": str, "port": int, "children": int, "max_requests": int}
    解析失败返回 None。

    兼容性约束：
    - children 缺失或非法时回退默认 5，且约束 children >= 1。
    - max_requests 缺失或非法时回退默认 500（老 php-cgi.ini 无此字段不影响启动）。
    - max_requests=0 表示 PHP 原生语义"不按请求数回收"，允许存在但默认绝不设为 0。
    """
    # 路径收敛：通过统一路径模块获取 php-cgi.ini 路径
    php_cgi_ini = get_php_cgi_ini_path(root_dir)
    if not os.path.isfile(php_cgi_ini):
        return None
    try:
        result = {}
        raw_children = None
        raw_max_requests = None
        with open(php_cgi_ini, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith(";") or not stripped:
                    continue
                m = re.match(r'(\w+)\s*=\s*(.+)', stripped)
                if m:
                    key = m.group(1).lower()
                    value = m.group(2).strip()
                    if key == "host":
                        result["host"] = value
                    elif key == "port":
                        result["port"] = int(value)
                    elif key == "children":
                        raw_children = value
                    elif key == "max_requests":
                        raw_max_requests = value

        if "host" not in result or "port" not in result:
            return None

        # children：缺失/非法回退 5，且约束 >= 1
        children = 5
        try:
            children = int(raw_children) if raw_children is not None else 5
        except (ValueError, TypeError):
            children = 5
        if children < 1:
            children = 5
        result["children"] = children

        # max_requests：缺失/非法回退 500，允许非负整数（0 表示不按请求数回收）
        max_requests = 500
        try:
            max_requests = int(raw_max_requests) if raw_max_requests is not None else 500
        except (ValueError, TypeError):
            max_requests = 500
        if max_requests < 0:
            max_requests = 500
        result["max_requests"] = max_requests

        return result
    except Exception:
        pass
    return None


def get_effective_nginx_listens(root_dir, cfg):
    """获取 Nginx 有效监听端口的结构化结果。

    返回 {"parsed":bool, "http":[port,...], "https":[port,...],
           "fallback":bool, "warning":str}

    边界规则：
    - 配置文件存在且成功解析到 listen 时，完全以配置文件为准：
      parsed=True, fallback=False
      没有 HTTP listen → http=[]；没有 HTTPS ssl listen → https=[]
    - 配置文件不存在或完全无法解析 listen 时，回退 runtime.ini：
      parsed=False, fallback=True, warning 包含提示
    """
    listens = parse_nginx_listens(root_dir)
    http_ports = [ln["port"] for ln in listens if not ln["ssl"]]
    https_ports = [ln["port"] for ln in listens if ln["ssl"]]

    config_exists, has_listen = _has_nginx_config_files(root_dir)

    if config_exists and has_listen:
        # 配置文件存在且解析到 listen，完全以配置文件为准
        return {
            "parsed": True,
            "http": http_ports,
            "https": https_ports,
            "fallback": False,
            "warning": "",
        }

    # 配置文件不存在或无法解析 listen → 回退 runtime.ini
    warning = "Nginx 配置文件不存在或无法解析 listen 指令，已回退 runtime.ini 默认值"
    fallback_http = [get_int(cfg, "HTTP_PORT", 80)] if not http_ports else http_ports
    fallback_https = []
    if get_int(cfg, "ENABLE_HTTPS", 0) == 1:
        fallback_https = [get_int(cfg, "HTTPS_PORT", 443)]

    return {
        "parsed": False,
        "http": fallback_http,
        "https": fallback_https,
        "fallback": True,
        "warning": warning,
    }


def get_effective_nginx_http_port(root_dir, cfg):
    """获取 Nginx 第一个 HTTP 实际监听端口。

    配置文件已解析但无 HTTP listen 时返回 None。
    配置文件无法解析时回退 runtime.ini。
    """
    result = get_effective_nginx_listens(root_dir, cfg)
    if result["http"]:
        return result["http"][0]
    if result["parsed"]:
        # 配置文件已解析但无 HTTP listen
        return None
    # fallback 模式下回退 runtime.ini
    return get_int(cfg, "HTTP_PORT", 80)


def get_effective_nginx_https_port(root_dir, cfg):
    """获取 Nginx 第一个 HTTPS 实际监听端口。

    配置文件已解析但无 ssl listen 时返回 None。
    配置文件无法解析时回退 runtime.ini（仅当 ENABLE_HTTPS=1）。
    """
    result = get_effective_nginx_listens(root_dir, cfg)
    if result["https"]:
        return result["https"][0]
    if result["parsed"]:
        # 配置文件已解析但无 ssl listen
        return None
    # fallback 模式下回退 runtime.ini
    if get_int(cfg, "ENABLE_HTTPS", 0) == 1:
        return get_int(cfg, "HTTPS_PORT", 443)
    return None


def get_effective_nginx_ports(root_dir, cfg):
    """获取 Nginx 所有实际监听端口，分为 HTTP 和 HTTPS 列表。

    基于 get_effective_nginx_listens 实现。
    返回 {"http": [port, ...], "https": [port, ...]}
    """
    result = get_effective_nginx_listens(root_dir, cfg)
    return {"http": result["http"], "https": result["https"]}


def is_effective_nginx_https_enabled(root_dir, cfg):
    """判断 Nginx 是否实际启用了 HTTPS。

    基于 get_effective_nginx_listens 实现。
    配置文件已解析时，完全以配置文件为准（有 ssl listen → True）。
    配置文件无法解析时，回退 runtime.ini 的 ENABLE_HTTPS。
    """
    result = get_effective_nginx_listens(root_dir, cfg)
    if result["parsed"]:
        return len(result["https"]) > 0
    # fallback 模式
    return get_int(cfg, "ENABLE_HTTPS", 0) == 1


def is_nginx_port_parsed_from_config(root_dir):
    """判断 Nginx 端口是否成功从配置文件解析（用于 warning 提示）。

    返回 (http_parsed, https_parsed) 两个 bool。
    """
    listens = parse_nginx_listens(root_dir)
    has_http = any(not ln["ssl"] for ln in listens)
    has_https = any(ln["ssl"] for ln in listens)
    return has_http, has_https


def get_effective_mysql_port(root_dir, cfg):
    """获取 MySQL 实际监听端口。

    优先从配置文件解析，解析失败回退到 runtime.ini。
    """
    port = parse_mysql_port(root_dir)
    if port is not None:
        return port
    return get_int(cfg, "MYSQL_PORT", 3306)


def get_effective_php_cgi_host_port(root_dir, cfg):
    """获取 PHP-CGI 实际监听地址和端口。

    优先从 php-cgi.ini 解析，解析失败回退到 runtime.ini。
    返回 (host, port)。
    """
    cgi_cfg = parse_php_cgi_config(root_dir)
    if cgi_cfg:
        return cgi_cfg["host"], cgi_cfg["port"]
    return get(cfg, "PHP_CGI_HOST", "127.0.0.1"), get_int(cfg, "PHP_CGI_PORT", 9000)
