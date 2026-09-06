# -*- coding: utf-8 -*-
"""
WNMP Templates Module - generates config files from templates

P2 阶段：模板文件迁移到 runtime/templates/<component>/，生成输出写入新组件配置路径。
模板与用户配置分离原则：
- templates（runtime/templates/nginx/ 等）只用于首次生成或显式恢复默认
- bin/nginx/conf/、bin/php/、bin/mysql/ 下的实际文件是用户配置
- 首次初始化时：文件不存在才从模板生成；文件已存在则跳过并记录日志
- 如目标文件已存在但未被 Panel 管理，先备份原始文件再生成
- start/restart 不得调用配置生成函数，只读取已有配置
"""
import os
import re
from runtime.wnmp_component_paths import (
    get_nginx_conf_path, get_nginx_site_conf_path,
    get_nginx_vhosts_dir, get_nginx_custom_http_dir, get_nginx_custom_server_dir,
    get_nginx_mime_types_path, get_nginx_fastcgi_params_path,
    get_php_ini_path, get_php_cgi_ini_path, get_mysql_ini_path,
    get_php_user_ini_path, get_mysql_user_ini_path,
    get_template_dir,
    backup_original_config_if_needed, is_panel_managed_config,
    ensure_panel_managed_header,
    migrate_component_configs_if_needed,
)


def ensure_component_configs_ready(root_dir, cfg=None, logger=None):
    """启动前保障配置布局：创建目录、迁移旧配置、生成缺失文件。

    P2 配置路径归位修复：已有初始化环境直接启动时，新路径配置可能未迁移/未生成。
    本函数在 start/restart 前调用，确保 MySQL/Nginx/PHP 新路径配置存在。

    保障规则（非破坏性）：
    1. 创建必要目录（bin/nginx/conf、bin/php、bin/mysql 及其子目录）
    2. 从旧 config 路径迁移缺失文件到新组件目录（migrate_component_configs_if_needed）
    3. 检查新路径活跃配置是否存在：bin/nginx/conf/nginx.conf、bin/nginx/conf/site.conf、
       bin/php/php.ini、bin/php/php-cgi.ini、bin/mysql/my.ini
    4. 缺失时从模板生成，使用 generate_* 函数处理备份接管逻辑

    Args:
        root_dir: 项目根目录
        cfg: 配置对象（可选，用于传递给 generate_* 函数）
        logger: 日志记录器

    Returns:
        tuple: (success: bool, message: str)
    """
    def _log_info(msg):
        if logger:
            try:
                from runtime.wnmp_log import log_info
                log_info(logger, "[config_ready] " + msg)
            except Exception:
                pass

    def _log_warn(msg):
        if logger:
            try:
                from runtime.wnmp_log import log_warn
                log_warn(logger, "[config_ready] " + msg)
            except Exception:
                pass

    def _log_error(msg):
        if logger:
            try:
                from runtime.wnmp_log import log_error
                log_error(logger, "[config_ready] " + msg)
            except Exception:
                pass

    # 1. 创建必要目录
    _log_info("Creating necessary directories...")
    _NECESSARY_DIRS = [
        os.path.join(root_dir, "bin", "nginx", "conf"),
        os.path.join(root_dir, "bin", "nginx", "conf", "vhosts"),
        os.path.join(root_dir, "bin", "nginx", "conf", "custom", "http"),
        os.path.join(root_dir, "bin", "nginx", "conf", "custom", "server"),
        os.path.join(root_dir, "bin", "php"),
        os.path.join(root_dir, "bin", "mysql"),
    ]
    for d in _NECESSARY_DIRS:
        os.makedirs(d, exist_ok=True)

    # 2. 迁移旧 config 到新组件目录（只补缺失，不覆盖已有）
    _log_info("Migrating legacy configs if needed...")
    try:
        migration_result = migrate_component_configs_if_needed(root_dir, logger)
        _log_info("Legacy migration completed: {}".format(
            "migrated" if migration_result.get("overall_migrated") else "no migration needed"))
    except Exception as e:
        _log_warn("Legacy migration encountered error: {}. Continuing with config check...".format(e))

    # 3. 检查并生成缺失的活跃配置文件
    if cfg is None:
        try:
            from runtime import wnmp_config
            cfg = wnmp_config.load_config(root_dir)
        except Exception:
            cfg = {}

    _ACTIVE_CONFIGS = [
        ("nginx.conf", get_nginx_conf_path(root_dir)),
        ("site.conf", get_nginx_site_conf_path(root_dir)),
        ("php.ini", get_php_ini_path(root_dir)),
        ("php-cgi.ini", get_php_cgi_ini_path(root_dir)),
        ("my.ini", get_mysql_ini_path(root_dir)),
    ]

    _GENERATORS = {
        "nginx.conf": lambda: generate_nginx_config(root_dir, cfg, logger),
        "site.conf": lambda: generate_site_config(root_dir, cfg, logger),
        "php.ini": lambda: generate_php_config(root_dir, cfg, logger),
        "php-cgi.ini": lambda: generate_php_cgi_config(root_dir, cfg, logger),
        "my.ini": lambda: generate_mysql_config(root_dir, cfg, logger),
    }

    _generated_count = 0
    _missing_configs = []

    for config_name, config_path in _ACTIVE_CONFIGS:
        if os.path.isfile(config_path):
            continue  # 已有，跳过

        _log_info("Missing config detected: {} ({}). Generating...".format(config_name, config_path))
        generator = _GENERATORS.get(config_name)
        if generator:
            try:
                ok, msg = generator()
                if ok:
                    _log_info("Generated missing config: {} -> {}".format(config_name, config_path))
                    _generated_count += 1
                else:
                    _log_error("Failed to generate {}: {}".format(config_name, msg))
                    _missing_configs.append("{} (generation failed: {})".format(config_path, msg))
            except Exception as e:
                _log_error("Exception generating {}: {}".format(config_name, e))
                _missing_configs.append("{} (exception: {})".format(config_path, e))
        else:
            _log_error("No generator for {}".format(config_name))
            _missing_configs.append("{} (no generator)".format(config_path))

    # 4. 返回结果
    if _missing_configs:
        _log_error("Config layout check failed. Missing configs: {}".format(_missing_configs))
        return False, "Missing configs: " + "; ".join(_missing_configs)

    _log_info("Config layout check passed. {} new configs generated.".format(_generated_count))
    return True, "OK. {} configs generated.".format(_generated_count) if _generated_count > 0 else "OK. All configs ready."


def read_template(root_dir, component, template_name):
    """Read a template file from runtime/templates/<component>/ directory.

    P2 阶段模板从 runtime/templates/<component>/ 读取，不再从 config/ 读取。
    """
    template_dir = get_template_dir(root_dir, component)
    if not template_dir:
        return None
    template_path = os.path.join(template_dir, template_name)
    if not os.path.isfile(template_path):
        return None
    with open(template_path, "r", encoding="utf-8-sig") as f:
        return f.read()


def replace_variables(template, variables):
    """Replace {{KEY}} placeholders in template with values."""
    result = template
    for key, value in variables.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def write_config(output_path, content):
    """Write config file with UTF-8 encoding."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)


def validate_no_placeholders(file_path, content, logger=None):
    """检查生成后的配置文件是否仍包含未替换的占位符 {{...}}。

    返回 (True, None) 或 (False, unexpanded_list)
    """
    pattern = re.compile(r'\{\{[^}]+\}\}')
    matches = pattern.findall(content)
    if matches:
        if logger:
            from runtime.wnmp_log import log_error
            log_error(logger, "Config file has unexpanded placeholders: {} -> {}".format(
                file_path, ", ".join(matches)))
        return False, matches
    return True, None


def validate_all_configs(root_dir, cfg, logger=None):
    """验证所有生成后的配置文件是否仍包含未替换的占位符。

    返回 (True, None) 或 (False, [(file_path, unexpanded_list), ...])
    """
    from runtime.wnmp_log import log_error, log_info

    # 路径收敛：通过统一路径模块获取配置文件路径
    config_files = [
        get_nginx_conf_path(root_dir),
        get_nginx_site_conf_path(root_dir),
        get_php_ini_path(root_dir),
        get_php_cgi_ini_path(root_dir),
        get_mysql_ini_path(root_dir),
    ]

    errors = []
    for file_path in config_files:
        if os.path.isfile(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            ok, matches = validate_no_placeholders(file_path, content, logger)
            if not ok:
                errors.append((file_path, matches))

    if errors:
        log_error(logger, "Config placeholder validation failed:")
        for file_path, matches in errors:
            log_error(logger, "  {} -> {}".format(file_path, ", ".join(matches)))
        return False, errors

    log_info(logger, "All config placeholders validated successfully")
    return True, None


def _check_existing_config(output_path, component, root_dir, logger=None):
    """检查目标配置文件是否已存在，返回三分支判断结果。

    P2-A 阻断修复：不再简单跳过已存在文件，而是区分是否被 Panel 管理。

    Returns:
        str: "not_exists" | "panel_managed" | "needs_takeover"
    """
    if not os.path.isfile(output_path):
        return "not_exists"
    if is_panel_managed_config(output_path):
        if logger:
            from runtime.wnmp_log import log_info
            log_info(logger, "配置文件已被 Panel 管理，跳过生成: " + output_path)
        return "panel_managed"
    # 文件存在但无 Panel 标记，需要备份后接管
    return "needs_takeover"


def _backup_and_takeover(output_path, component, root_dir, logger=None):
    """备份原始配置文件并准备接管。

    P2-A 数据安全收口：目标活跃配置文件存在且没有 Panel 管理标记时，
    接管前必须先成功备份。备份失败时必须中止接管，不允许继续覆盖原始文件。

    Returns:
        bool: True 表示备份成功可以接管，False 表示备份失败不应覆盖
    """
    backup_result = backup_original_config_if_needed(root_dir, component, output_path)
    if backup_result["backed_up"]:
        if logger:
            from runtime.wnmp_log import log_info
            log_info(logger, "备份原始配置: {} -> {} (component: {})".format(
                output_path, backup_result["backup_path"], component))
        return True
    elif backup_result["reason"] == "already_panel_managed":
        # 已被 Panel 管理，不需要接管
        return False
    else:
        # P2-A 数据安全收口：备份失败时必须中止接管，不允许继续覆盖原始文件
        if logger:
            from runtime.wnmp_log import log_error
            log_error(logger, "原始配置备份失败，接管已中止: {} (reason: {}). "
                "请检查文件权限/文件占用/磁盘空间。".format(output_path, backup_result["reason"]))
        return False


def _ensure_config_dirs(root_dir):
    """P2：首次初始化时创建组件配置目录。

    必须在生成配置文件之前调用，确保 bin/nginx/conf 等目录存在。
    """
    from runtime.wnmp_component_paths import (
        get_nginx_vhosts_dir, get_nginx_custom_http_dir, get_nginx_custom_server_dir,
    )
    dirs = [
        os.path.dirname(get_nginx_conf_path(root_dir)),
        os.path.dirname(get_nginx_site_conf_path(root_dir)),
        get_nginx_vhosts_dir(root_dir),
        get_nginx_custom_http_dir(root_dir),
        get_nginx_custom_server_dir(root_dir),
        os.path.dirname(get_php_ini_path(root_dir)),
        os.path.dirname(get_php_cgi_ini_path(root_dir)),
        os.path.dirname(get_mysql_ini_path(root_dir)),
    ]
    for d in dirs:
        if d:
            os.makedirs(d, exist_ok=True)


def _ensure_nginx_base_files(root_dir, logger=None):
    """P2 收口：确保 mime.types 和 fastcgi_params 存在于 bin/nginx/conf/。

    规则：
    - 如果新路径已存在，不覆盖
    - 如果旧 config/nginx/ 下存在且新路径不存在，复制过去
    - 如果都不存在，从 Nginx 官方自带文件复制（bin/nginx/conf/ 下已有）
    """
    import shutil
    _BASE_FILES = [
        ("mime.types", get_nginx_mime_types_path(root_dir)),
        ("fastcgi_params", get_nginx_fastcgi_params_path(root_dir)),
    ]
    for name, new_path in _BASE_FILES:
        if os.path.isfile(new_path):
            continue
        # 尝试从旧 config/nginx/ 复制
        legacy_path = os.path.join(root_dir, "config", "nginx", name)
        if os.path.isfile(legacy_path):
            try:
                shutil.copy2(legacy_path, new_path)
                if logger:
                    from runtime.wnmp_log import log_info
                    log_info(logger, "复制基础文件: {} -> {}".format(legacy_path, new_path))
                continue
            except Exception:
                pass
        # Nginx 官方自带文件通常已在 bin/nginx/conf/ 下，无需额外处理


def generate_nginx_config(root_dir, cfg, logger=None):
    """Generate bin/nginx/conf/nginx.conf from template, include custom/*.conf if exists.

    P2-A 阻断修复：三分支判断——不存在→生成，有Panel标记→跳过，无Panel标记→备份后接管。

    路径引用修复：所有由程序生成的文件系统绝对路径统一通过 format_nginx_path 格式化，
    转换为正斜杠并以双引号包裹，避免 WNMP 安装到含空格目录（如 D:/Program Files/WNMP）
    时 nginx -t 因路径被空白拆分而报错。
    """
    from runtime.wnmp_path import format_nginx_path

    # 路径收敛：通过统一路径模块获取输出路径
    output_path = get_nginx_conf_path(root_dir)

    # P2-A 阻断修复：三分支判断
    status = _check_existing_config(output_path, "nginx", root_dir, logger)
    if status == "panel_managed":
        return True, output_path
    if status == "needs_takeover":
        # P2-A 数据安全收口：备份失败时中止接管，不继续覆盖
        if not _backup_and_takeover(output_path, "nginx", root_dir, logger):
            return False, "原始配置备份失败，接管已中止: " + output_path

    # 统一构建完整文件系统路径，并通过 format_nginx_path 加双引号包裹
    # 包含的指令：error_log、pid、include mime.types、access_log、
    # client_body_temp_path、proxy_temp_path、fastcgi_temp_path、
    # uwsgi_temp_path、scgi_temp_path、include site.conf、
    # include custom/http/*.conf、include vhosts/*.conf
    nginx_error_log = format_nginx_path(os.path.join(root_dir, "logs", "nginx", "error.log"))
    nginx_access_log = format_nginx_path(os.path.join(root_dir, "logs", "nginx", "access.log"))
    nginx_pid_path = format_nginx_path(os.path.join(root_dir, "runtime", "nginx.pid"))
    nginx_mime_types = format_nginx_path(get_nginx_mime_types_path(root_dir))
    nginx_site_conf = format_nginx_path(get_nginx_site_conf_path(root_dir))
    nginx_client_body_temp = format_nginx_path(os.path.join(root_dir, "temp", "client_body_temp"))
    nginx_proxy_temp = format_nginx_path(os.path.join(root_dir, "temp", "proxy_temp"))
    nginx_fastcgi_temp = format_nginx_path(os.path.join(root_dir, "temp", "fastcgi_temp"))
    nginx_uwsgi_temp = format_nginx_path(os.path.join(root_dir, "temp", "uwsgi_temp"))
    nginx_scgi_temp = format_nginx_path(os.path.join(root_dir, "temp", "scgi_temp"))
    # include 通配符路径：目录后跟 /*.conf，整体由 format_nginx_path 包裹
    nginx_vhosts_glob = format_nginx_path(os.path.join(get_nginx_vhosts_dir(root_dir), "*.conf"))
    nginx_custom_http_glob = format_nginx_path(os.path.join(get_nginx_custom_http_dir(root_dir), "*.conf"))

    # P2：模板从 runtime/templates/nginx/ 读取
    template = read_template(root_dir, "nginx", "nginx.conf.template")
    if template is None:
        return False, "Nginx config template not found"

    variables = {
        # 路径值均已通过 format_nginx_path 加双引号包裹，模板直接替换即可
        "NGINX_ERROR_LOG": nginx_error_log,
        "NGINX_ACCESS_LOG": nginx_access_log,
        "NGINX_PID_PATH": nginx_pid_path,
        "NGINX_MIME_TYPES": nginx_mime_types,
        "NGINX_SITE_CONF": nginx_site_conf,
        "NGINX_CLIENT_BODY_TEMP": nginx_client_body_temp,
        "NGINX_PROXY_TEMP": nginx_proxy_temp,
        "NGINX_FASTCGI_TEMP": nginx_fastcgi_temp,
        "NGINX_UWSGI_TEMP": nginx_uwsgi_temp,
        "NGINX_SCGI_TEMP": nginx_scgi_temp,
        "NGINX_VHOSTS_GLOB": nginx_vhosts_glob,
        "NGINX_CUSTOM_HTTP_GLOB": nginx_custom_http_glob,
    }

    content = replace_variables(template, variables)

    # P2：添加 Panel 管理标记
    content = ensure_panel_managed_header(content, "nginx", "template")

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_config(output_path, content)

    # P2 收口：确保 mime.types 和 fastcgi_params 存在于 bin/nginx/conf/
    _ensure_nginx_base_files(root_dir, logger)

    ok, _ = validate_no_placeholders(output_path, content)
    if not ok:
        return False, "nginx.conf has unexpanded placeholders"
    return True, output_path


def remove_https_block(content, enable_https):
    """移除模板中的 HTTPS server 块（#HTTPS_SERVER_START# 到 #HTTPS_SERVER_END#）。

    如果 enable_https=0，移除整个 HTTPS 块和标记行。
    如果 enable_https=1，保留内容但移除标记行。
    """
    start_marker = "#HTTPS_SERVER_START#"
    end_marker = "#HTTPS_SERVER_END#"

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        return content

    if enable_https == 0:
        return content[:start_idx] + content[end_idx + len(end_marker):]
    else:
        https_block = content[start_idx:end_idx + len(end_marker)]
        https_content = https_block.replace(start_marker, "").replace(end_marker, "").strip()
        return content[:start_idx] + "\n" + https_content + "\n" + content[end_idx + len(end_marker):]


def validate_site_config_https(content, enable_https, logger=None):
    """验证 site.conf 中 HTTPS 配置的一致性。

    enable_https=0 时，不应出现 SSL 相关内容。
    enable_https=1 时，证书路径应为绝对路径。
    """
    from runtime.wnmp_log import log_error

    ssl_keywords = ["listen 443 ssl", "ssl_certificate", "ssl_certificate_key", "server.crt", "server.key"]

    if enable_https == 0:
        found_ssl = []
        for keyword in ssl_keywords:
            if keyword in content:
                found_ssl.append(keyword)

        if found_ssl:
            if logger:
                log_error(logger, "Config validation failed: ENABLE_HTTPS=0 but SSL content found: {}".format(
                    ", ".join(found_ssl)))
            return False, found_ssl
        return True, None

    else:
        cert_path = re.search(r'ssl_certificate\s+([^;]+);', content)
        key_path = re.search(r'ssl_certificate_key\s+([^;]+);', content)

        # 路径值现已统一通过 format_nginx_path 加双引号包裹，验证时需要先剥掉首尾引号
        def _strip_quotes(p):
            if len(p) >= 2 and p.startswith('"') and p.endswith('"'):
                return p[1:-1]
            return p

        if cert_path:
            path = _strip_quotes(cert_path.group(1).strip())
            if path.startswith("{{") or path.startswith("./") or (len(path) > 1 and path[1] != ":"):
                if logger:
                    log_error(logger, "Config validation failed: CERT_PATH is not absolute: " + path)
                return False, ["CERT_PATH not absolute: " + path]

        if key_path:
            path = _strip_quotes(key_path.group(1).strip())
            if path.startswith("{{") or path.startswith("./") or (len(path) > 1 and path[1] != ":"):
                if logger:
                    log_error(logger, "Config validation failed: KEY_PATH is not absolute: " + path)
                return False, ["KEY_PATH not absolute: " + path]

        return True, None


def generate_site_config(root_dir, cfg, logger=None):
    """Generate bin/nginx/conf/site.conf from template.

    P2-A 阻断修复：三分支判断——不存在→生成，有Panel标记→跳过，无Panel标记→备份后接管。

    路径引用修复：所有由程序生成的文件系统绝对路径统一通过 format_nginx_path 格式化，
    转换为正斜杠并以双引号包裹，兼容 WNMP 安装到含空格目录（如 D:/Program Files/WNMP）。
    不对 Nginx 变量表达式（$document_root、$fastcgi_script_name 等）做路径处理。
    """
    from runtime.wnmp_path import resolve_path, format_nginx_path
    from runtime import wnmp_config
    from runtime.wnmp_log import log_error

    # 路径收敛：通过统一路径模块获取输出路径
    output_path = get_nginx_site_conf_path(root_dir)

    # P2-A 阻断修复：三分支判断
    status = _check_existing_config(output_path, "nginx", root_dir, logger)
    if status == "panel_managed":
        return True, output_path
    if status == "needs_takeover":
        # P2-A 数据安全收口：备份失败时中止接管，不继续覆盖
        if not _backup_and_takeover(output_path, "nginx", root_dir, logger):
            return False, "原始配置备份失败，接管已中止: " + output_path

    web_root_raw = wnmp_config.get(cfg, "WEB_ROOT")
    # resolve_path 返回的绝对路径可能含反斜杠，由 format_nginx_path 统一转正斜杠 + 加双引号
    web_root = format_nginx_path(resolve_path(root_dir, web_root_raw))
    php_cgi_host = wnmp_config.get(cfg, "PHP_CGI_HOST")
    php_cgi_port = wnmp_config.get(cfg, "PHP_CGI_PORT")
    http_port = wnmp_config.get_int(cfg, "HTTP_PORT", 80)
    https_port = wnmp_config.get_int(cfg, "HTTPS_PORT", 443)
    enable_https = wnmp_config.get_int(cfg, "ENABLE_HTTPS", 0)

    # P2：模板从 runtime/templates/nginx/ 读取
    template = read_template(root_dir, "nginx", "site.conf.template")
    if template is None:
        return False, "Site config template not found"

    cert_path = format_nginx_path(os.path.join(root_dir, "config", "certs", "server.crt"))
    key_path = format_nginx_path(os.path.join(root_dir, "config", "certs", "server.key"))

    # P2 收口：使用新路径变量，并统一通过 format_nginx_path 加双引号包裹
    nginx_fastcgi_params = format_nginx_path(get_nginx_fastcgi_params_path(root_dir))
    # include 通配符路径：目录后跟 /*.conf，整体由 format_nginx_path 包裹
    nginx_custom_server_glob = format_nginx_path(os.path.join(get_nginx_custom_server_dir(root_dir), "*.conf"))

    variables = {
        # 路径值均已通过 format_nginx_path 加双引号包裹，模板直接替换即可
        "WEB_ROOT": web_root,
        "PHP_CGI_HOST": php_cgi_host,
        "PHP_CGI_PORT": php_cgi_port,
        "HTTP_PORT": str(http_port),
        "HTTPS_PORT": str(https_port),
        "ENABLE_HTTPS": str(enable_https),
        "CERT_PATH": cert_path,
        "KEY_PATH": key_path,
        # P2 收口：新路径变量
        "NGINX_FASTCGI_PARAMS": nginx_fastcgi_params,
        "NGINX_CUSTOM_SERVER_GLOB": nginx_custom_server_glob,
    }

    content = replace_variables(template, variables)

    content = remove_https_block(content, enable_https)

    ok, _ = validate_no_placeholders("site.conf", content, None)
    if not ok:
        return False, "site.conf has unexpanded placeholders"

    ok, errors = validate_site_config_https(content, enable_https, None)
    if not ok:
        return False, "site.conf HTTPS validation failed: " + ", ".join(errors)

    # P2：添加 Panel 管理标记
    content = ensure_panel_managed_header(content, "nginx", "template")

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_config(output_path, content)
    return True, output_path


def _append_user_config(output_path, user_config_path, logger=None):
    """将用户自定义配置追加到生成的配置文件末尾。

    如果用户配置文件存在，读取并追加到生成文件。
    """
    if not os.path.isfile(user_config_path):
        return True

    try:
        with open(user_config_path, "r", encoding="utf-8") as f:
            user_content = f.read().strip()
        if user_content:
            with open(output_path, "a", encoding="utf-8") as f:
                f.write("\n\n; --- User custom configuration (from {}) ---\n".format(
                    os.path.basename(user_config_path)))
                f.write(user_content)
                f.write("\n")
            if logger:
                from runtime.wnmp_log import log_info
                log_info(logger, "Appended user config: " + user_config_path)
        return True
    except Exception as e:
        if logger:
            from runtime.wnmp_log import log_warn
            log_warn(logger, "Failed to append user config {}: {}".format(user_config_path, str(e)))
        return False


def generate_php_config(root_dir, cfg, logger=None):
    """Generate bin/php/php.ini from template, append php.user.ini if exists.

    P2-A 阻断修复：三分支判断——不存在→生成，有Panel标记→跳过，无Panel标记→备份后接管。
    """
    from runtime.wnmp_path import to_forward_slash
    from runtime import wnmp_config

    # 路径收敛：通过统一路径模块获取输出路径
    output_path = get_php_ini_path(root_dir)

    # P2-A 阻断修复：三分支判断
    status = _check_existing_config(output_path, "php", root_dir, logger)
    if status == "panel_managed":
        return True, output_path
    if status == "needs_takeover":
        # P2-A 数据安全收口：备份失败时中止接管，不继续覆盖
        if not _backup_and_takeover(output_path, "php", root_dir, logger):
            return False, "原始配置备份失败，接管已中止: " + output_path

    php_ext_dir = to_forward_slash(os.path.join(root_dir, "bin", "php", "ext"))
    logs_dir = to_forward_slash(os.path.join(root_dir, "logs"))
    mysql_host = wnmp_config.get(cfg, "MYSQL_HOST")
    mysql_port = wnmp_config.get(cfg, "MYSQL_PORT")

    # P2：模板从 runtime/templates/php/ 读取
    template = read_template(root_dir, "php", "php.ini.template")
    if template is None:
        return False, "PHP config template not found"

    variables = {
        "PHP_EXT_DIR": php_ext_dir,
        "LOGS_DIR": logs_dir,
        "MYSQL_HOST": mysql_host,
        "MYSQL_PORT": mysql_port,
    }

    content = replace_variables(template, variables)

    # P2：添加 Panel 管理标记（PHP 使用 ; 注释符）
    content = ensure_panel_managed_header(content, "php", "template")

    # 路径收敛：通过统一路径模块获取输出路径
    output_path = get_php_ini_path(root_dir)
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_config(output_path, content)

    # 追加用户自定义 PHP 配置
    # 路径收敛：通过统一路径模块获取 php.user.ini 路径
    user_config_path = get_php_user_ini_path(root_dir)
    _append_user_config(output_path, user_config_path, logger)

    ok, _ = validate_no_placeholders(output_path, content)
    if not ok:
        return False, "php.ini has unexpanded placeholders"
    return True, output_path


def generate_mysql_config(root_dir, cfg, logger=None):
    """Generate bin/mysql/my.ini from template, append my.user.ini if exists.

    P2-A 阻断修复：三分支判断——不存在→生成，有Panel标记→跳过，无Panel标记→备份后接管。
    """
    from runtime.wnmp_path import resolve_path, to_forward_slash
    from runtime import wnmp_config

    # 路径收敛：通过统一路径模块获取输出路径
    output_path = get_mysql_ini_path(root_dir)

    # P2-A 阻断修复：三分支判断
    status = _check_existing_config(output_path, "mysql", root_dir, logger)
    if status == "panel_managed":
        return True, output_path
    if status == "needs_takeover":
        # P2-A 数据安全收口：备份失败时中止接管，不继续覆盖
        if not _backup_and_takeover(output_path, "mysql", root_dir, logger):
            return False, "原始配置备份失败，接管已中止: " + output_path

    bin_dir = to_forward_slash(os.path.join(root_dir, "bin"))
    mysql_basedir = to_forward_slash(os.path.join(root_dir, "bin", "mysql"))
    mysql_data_dir = to_forward_slash(resolve_path(root_dir, wnmp_config.get(cfg, "MYSQL_DATA_DIR")))
    tmp_dir = to_forward_slash(os.path.join(root_dir, "tmp"))
    logs_dir = to_forward_slash(os.path.join(root_dir, "logs"))
    mysql_host = wnmp_config.get(cfg, "MYSQL_HOST")
    mysql_port = wnmp_config.get(cfg, "MYSQL_PORT")

    # P2：模板从 runtime/templates/mysql/ 读取
    template = read_template(root_dir, "mysql", "my.ini.template")
    if template is None:
        return False, "MySQL config template not found"

    variables = {
        "BIN_DIR": bin_dir,
        "MYSQL_BASEDIR": mysql_basedir,
        "MYSQL_DATA_DIR": mysql_data_dir,
        "MYSQL_DATADIR": mysql_data_dir,
        "TMP_DIR": tmp_dir,
        "LOGS_DIR": logs_dir,
        "MYSQL_HOST": mysql_host,
        "MYSQL_PORT": mysql_port,
    }

    content = replace_variables(template, variables)

    # P2：添加 Panel 管理标记（MySQL 使用 # 注释符）
    content = ensure_panel_managed_header(content, "mysql", "template")

    # 路径收敛：通过统一路径模块获取输出路径
    output_path = get_mysql_ini_path(root_dir)
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_config(output_path, content)

    # 追加用户自定义 MySQL 配置
    # 路径收敛：通过统一路径模块获取 my.user.ini 路径
    user_config_path = get_mysql_user_ini_path(root_dir)
    _append_user_config(output_path, user_config_path, logger)

    ok, _ = validate_no_placeholders(output_path, content)
    if not ok:
        return False, "my.ini has unexpanded placeholders"
    return True, output_path


def generate_php_cgi_config(root_dir, cfg, logger=None):
    """Generate bin/php/php-cgi.ini from template.

    P2-A 阻断修复：三分支判断——不存在→生成，有Panel标记→跳过，无Panel标记→备份后接管。
    php-cgi.ini 属于本项目管理的 PHP-CGI 运行配置，如已存在且无 Panel 标记也按备份后接管处理。
    """
    from runtime import wnmp_config

    # 路径收敛：通过统一路径模块获取输出路径
    output_path = get_php_cgi_ini_path(root_dir)

    # P2-A 阻断修复：三分支判断
    status = _check_existing_config(output_path, "php", root_dir, logger)
    if status == "panel_managed":
        return True, output_path
    if status == "needs_takeover":
        # P2-A 数据安全收口：备份失败时中止接管，不继续覆盖
        if not _backup_and_takeover(output_path, "php", root_dir, logger):
            return False, "原始配置备份失败，接管已中止: " + output_path

    php_cgi_host = wnmp_config.get(cfg, "PHP_CGI_HOST", "127.0.0.1")
    php_cgi_port = wnmp_config.get(cfg, "PHP_CGI_PORT", "9000")
    php_cgi_children = wnmp_config.get(cfg, "PHP_CGI_CHILDREN", "5")
    php_cgi_max_requests = wnmp_config.get(cfg, "PHP_CGI_MAX_REQUESTS", "500")

    content = "; PHP-CGI 运行配置（与 php.ini 分离）\n"
    content += "; php.ini 控制 PHP 运行时行为，php-cgi.ini 控制 PHP-CGI 进程启动参数\n"
    content += "; 修改后需重启 PHP-CGI 组件生效\n"
    content += "; 此文件由首次初始化从 runtime.ini 默认值生成，后续由用户维护\n"
    content += "; children 对应 PHP_FCGI_CHILDREN（Worker 数），max_requests 对应 PHP_FCGI_MAX_REQUESTS\n"
    content += "\n"
    content += "[php-cgi]\n"
    content += "host={}\n".format(php_cgi_host)
    content += "port={}\n".format(php_cgi_port)
    content += "children={}\n".format(php_cgi_children)
    content += "max_requests={}\n".format(php_cgi_max_requests)
    content += "\n"

    # P2：添加 Panel 管理标记（php-cgi 使用 ; 注释符）
    content = ensure_panel_managed_header(content, "php-cgi", "template")

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_config(output_path, content)
    if logger:
        from runtime.wnmp_log import log_info
        log_info(logger, "Generated PHP-CGI runtime config: " + output_path)
    return True, output_path


def generate_all_configs(root_dir, cfg, logger=None):
    """Generate all config files.

    P2：生成前先创建组件配置目录，再从旧集中配置迁移，最后从模板生成。
    首次初始化时：文件不存在才从模板生成；文件已存在则跳过并记录日志。
    任一配置文件生成失败或包含未替换占位符，立即返回 False。
    """
    from runtime.wnmp_log import log_info, log_error, log_success

    # P2：首次初始化时先创建组件配置目录
    _ensure_config_dirs(root_dir)

    # P2：从旧集中配置迁移到新组件目录（非破坏性，不删除旧文件）
    try:
        from runtime.wnmp_component_paths import migrate_component_configs_if_needed
        migrate_result = migrate_component_configs_if_needed(root_dir, logger)
        if migrate_result.get("migrated"):
            log_info(logger, "旧配置迁移完成")
    except Exception as e:
        log_info(logger, "旧配置迁移检查跳过: " + str(e))

    # P2：迁移模板文件到 runtime/templates/（如仍在旧 config 目录）
    try:
        from runtime.wnmp_component_paths import migrate_templates_to_runtime
        migrate_templates_to_runtime(root_dir, logger)
    except Exception as e:
        log_info(logger, "模板迁移检查跳过: " + str(e))

    results = []

    ok, result = generate_nginx_config(root_dir, cfg, logger)
    if ok:
        log_info(logger, "Nginx main config: " + result)
        results.append(("nginx.conf", True))
    else:
        log_error(logger, "Failed to generate Nginx config: " + result)
        results.append(("nginx.conf", False))
        return False

    ok, result = generate_site_config(root_dir, cfg, logger)
    if ok:
        log_info(logger, "Nginx site config: " + result)
        results.append(("site.conf", True))
    else:
        log_error(logger, "Failed to generate site config: " + result)
        results.append(("site.conf", False))
        return False

    ok, result = generate_php_config(root_dir, cfg, logger)
    if ok:
        log_info(logger, "PHP config: " + result)
        results.append(("php.ini", True))
    else:
        log_error(logger, "Failed to generate PHP config: " + result)
        results.append(("php.ini", False))
        return False

    ok, result = generate_php_cgi_config(root_dir, cfg, logger)
    if ok:
        log_info(logger, "PHP-CGI runtime config: " + result)
        results.append(("php-cgi.ini", True))
    else:
        log_error(logger, "Failed to generate PHP-CGI config: " + result)
        results.append(("php-cgi.ini", False))
        return False

    ok, result = generate_mysql_config(root_dir, cfg, logger)
    if ok:
        log_info(logger, "MySQL config: " + result)
        results.append(("my.ini", True))
    else:
        log_error(logger, "Failed to generate MySQL config: " + result)
        results.append(("my.ini", False))
        return False

    log_success(logger, "All config files generated successfully")
    return True
