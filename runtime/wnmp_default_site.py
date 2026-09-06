# -*- coding: utf-8 -*-
"""
WNMP Default Site Module - manages default www/index.php and runtime-config.php

职责拆分：
- init 命令：允许创建/覆盖默认检测页
- start 命令：只更新 runtime-config.php（仅限默认目录且 index.php 仍是默认页），不创建/恢复 index.php
"""
import os
from datetime import datetime, timezone


# 版本标记，用于识别默认检测页是否可以安全覆盖
DEFAULT_INDEX_MARKER = "WNMP_RUNTIME_DEFAULT_INDEX"

INDEX_PHP_TEMPLATE = '''<!DOCTYPE html>
<!-- WNMP_RUNTIME_DEFAULT_INDEX -->
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WNMP Runtime</title>
    <meta name="description" content="WNMP Runtime local default site environment check page.">
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;font-size:14px;background:#f0f2f5;color:#333;line-height:1.6}
        .page-wrap{max-width:860px;margin:0 auto;padding:24px 16px 40px}
        /* Header */
        .header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px}
        .header-left h1{font-size:24px;font-weight:700;color:#0f172a;margin-bottom:4px}
        .header-left p{font-size:13px;color:#64748b}
        .lang-switch{display:flex;gap:4px;flex-shrink:0;margin-top:2px}
        .lang-btn{padding:4px 12px;font-size:12px;border:1px solid #d0d5dd;border-radius:6px;background:#fff;color:#64748b;cursor:pointer;transition:all .15s}
        .lang-btn:hover{border-color:#3b82f6;color:#3b82f6}
        .lang-btn.active{background:#3b82f6;color:#fff;border-color:#3b82f6}
        /* Cards */
        .card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);margin-bottom:18px;overflow:hidden;border:1px solid #e5eaf2}
        .card-title{font-size:12px;font-weight:600;color:#6b7280;padding:10px 18px;background:#f9fafb;border-bottom:1px solid #e5e7eb;text-transform:uppercase;letter-spacing:.5px}
        .card-body{padding:16px 18px}
        /* Hero */
        .hero{text-align:center;padding:32px 18px 24px}
        .hero-icon{font-size:40px;margin-bottom:10px;display:block}
        .hero h2{font-size:20px;font-weight:700;color:#0f172a;margin-bottom:6px}
        .hero p{font-size:13px;color:#64748b}
        /* Status grid */
        .status-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
        .status-item{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;background:#f8fafc;border:1px solid #e5eaf2}
        .status-item .label{font-size:13px;color:#475569;font-weight:500}
        .status-item .value{font-size:13px;color:#0f172a;font-weight:600;word-break:break-all;text-align:right;max-width:60%}
        /* Badge */
        .badge{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
        .badge-ok{color:#079455;background:#e8f8ef;border:1px solid #b7ebca}
        .badge-warn{color:#b54708;background:#fff7e6;border:1px solid #fedf89}
        .badge-err{color:#d92d20;background:#fff1f0;border:1px solid #fecdca}
        /* Extension grid */
        .ext-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
        .ext-item{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-radius:8px;background:#f8fafc;border:1px solid #e5eaf2;font-size:13px}
        .ext-item .ext-name{color:#475569;font-weight:500}
        /* Config list */
        .config-list{display:grid;grid-template-columns:1fr 1fr;gap:8px 20px}
        .config-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #f1f5f9}
        .config-row .ck{color:#64748b;font-size:13px}
        .config-row .cv{color:#0f172a;font-size:13px;font-weight:600}
        /* MySQL note */
        .mysql-note{margin-top:10px;padding:8px 12px;border-radius:6px;background:#fff7e6;border:1px solid #fedf89;font-size:12px;color:#92400e}
        /* Warning card */
        .warn-card{background:#fff7e6;border-color:#fedf89}
        .warn-card .card-body{color:#92400e;font-size:13px}
        /* Footer */
        .footer{text-align:center;padding:16px 0 0;color:#94a3b8;font-size:12px}
        /* Responsive */
        @media(max-width:600px){
            .status-grid{grid-template-columns:1fr}
            .ext-grid{grid-template-columns:1fr 1fr}
            .config-list{grid-template-columns:1fr}
            .header{flex-direction:column;gap:12px}
        }
    </style>
</head>
<body>
<div class="page-wrap">
    <!-- Header with language switch -->
    <div class="header">
        <div class="header-left">
            <h1 data-i18n="hero_title">WNMP Runtime</h1>
            <p data-i18n="hero_sub">Local Default Site Environment Check</p>
        </div>
        <div class="lang-switch">
            <button class="lang-btn" data-lang="zh" onclick="switchLang('zh')">中文</button>
            <button class="lang-btn" data-lang="en" onclick="switchLang('en')">English</button>
        </div>
    </div>

    <!-- Hero -->
    <div class="card">
        <div class="hero">
            <span class="hero-icon">&#9989;</span>
            <h2 data-i18n="hero_heading">Environment Initialized</h2>
            <p data-i18n="hero_desc">This is the local default site detection page for WNMP Runtime.</p>
        </div>
    </div>

<?php
// PHP helper: HTML escape
if (!function_exists('wnmp_h')) {
    function wnmp_h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
}

// PHP: Collect runtime config safely
$wnmpConfig = null;
$wnmpConfigFile = __DIR__ . '/runtime-config.php';
if (file_exists($wnmpConfigFile)) {
    $runtimeConfig = [];
    require $wnmpConfigFile;
    if (is_array($runtimeConfig) && !empty($runtimeConfig)) {
        $wnmpConfig = $runtimeConfig;
    }
}

// PHP: MySQL TCP port reachability check
$wnmpMysqlOk = null;
$wnmpMysqlAddr = '';
if ($wnmpConfig && isset($wnmpConfig['MYSQL_HOST']) && isset($wnmpConfig['MYSQL_PORT'])) {
    $mHost = $wnmpConfig['MYSQL_HOST'];
    $mPort = (int)$wnmpConfig['MYSQL_PORT'];
    $wnmpMysqlAddr = wnmp_h($mHost) . ':' . $mPort;
    $sock = @fsockopen($mHost, $mPort, $errno, $errstr, 2);
    if ($sock) { fclose($sock); $wnmpMysqlOk = true; } else { $wnmpMysqlOk = false; }
}
?>

    <!-- Runtime Overview -->
    <div class="card">
        <div class="card-title" data-i18n="section_runtime">Runtime Overview</div>
        <div class="card-body">
            <div class="status-grid">
                <div class="status-item">
                    <span class="label" data-i18n="label_server_software">Server Software</span>
                    <span class="value"><?php echo wnmp_h($_SERVER['SERVER_SOFTWARE'] ?? 'N/A'); ?></span>
                </div>
                <div class="status-item">
                    <span class="label" data-i18n="label_php_version">PHP Version</span>
                    <span class="value"><?php echo phpversion(); ?></span>
                </div>
                <div class="status-item">
                    <span class="label" data-i18n="label_mysql_port">MySQL Port</span>
                    <span class="value">
<?php if ($wnmpMysqlOk === true): ?>
                        <span class="badge badge-ok" data-i18n="mysql_reachable" data-i18n-args="<?php echo $wnmpMysqlAddr; ?>">Port reachable (<?php echo $wnmpMysqlAddr; ?>)</span>
<?php elseif ($wnmpMysqlOk === false): ?>
                        <span class="badge badge-warn" data-i18n="mysql_unreachable" data-i18n-args="<?php echo $wnmpMysqlAddr; ?>">Port not reachable (<?php echo $wnmpMysqlAddr; ?>)</span>
<?php elseif ($wnmpConfig === null): ?>
                        <span class="badge badge-warn" data-i18n="config_not_found">runtime-config.php not found</span>
<?php else: ?>
                        <span class="badge badge-warn" data-i18n="config_incomplete">runtime-config.php incomplete</span>
<?php endif; ?>
                    </span>
                </div>
                <div class="status-item">
                    <span class="label" data-i18n="label_document_root">Document Root</span>
                    <span class="value" style="font-size:11px"><?php echo wnmp_h($_SERVER['DOCUMENT_ROOT'] ?? 'N/A'); ?></span>
                </div>
            </div>
<?php if ($wnmpMysqlOk !== null): ?>
            <div class="mysql-note" data-i18n="mysql_pwd_note">Root password is not stored locally. Please save it during initial setup.</div>
<?php endif; ?>
        </div>
    </div>

    <!-- PHP Extensions -->
    <div class="card">
        <div class="card-title" data-i18n="section_extensions">PHP Extensions</div>
        <div class="card-body">
            <div class="ext-grid">
<?php
$wnmpExts = ['openssl'=>'OpenSSL','mysqli'=>'MySQLi','pdo_mysql'=>'PDO MySQL','curl'=>'cURL','mbstring'=>'MBString'];
foreach ($wnmpExts as $ext => $name):
    $loaded = extension_loaded($ext);
?>
                <div class="ext-item">
                    <span class="ext-name"><?php echo $name; ?></span>
<?php if ($loaded): ?>
                    <span class="badge badge-ok" data-i18n="status_available">Available</span>
<?php else: ?>
                    <span class="badge badge-warn" data-i18n="status_unavailable">Not Available</span>
<?php endif; ?>
                </div>
<?php endforeach; ?>
            </div>
        </div>
    </div>

    <!-- Runtime Config -->
<?php if ($wnmpConfig !== null): ?>
    <div class="card">
        <div class="card-title" data-i18n="section_config">Runtime Config</div>
        <div class="card-body">
            <div class="config-list">
                <div class="config-row">
                    <span class="ck" data-i18n="label_http_port">HTTP Port</span>
                    <span class="cv"><?php echo wnmp_h($wnmpConfig['HTTP_PORT'] ?? 'N/A'); ?></span>
                </div>
                <div class="config-row">
                    <span class="ck" data-i18n="label_https_port">HTTPS Port</span>
<?php $httpsVal = $wnmpConfig['HTTPS_PORT'] ?? ''; $httpsEnabled = !empty($httpsVal); ?>
<?php if ($httpsEnabled): ?>
                    <span class="cv"><?php echo wnmp_h($httpsVal); ?></span>
<?php else: ?>
                    <span class="cv" data-i18n="https_disabled">Disabled</span>
<?php endif; ?>
                </div>
                <div class="config-row">
                    <span class="ck">MySQL</span>
                    <span class="cv"><?php echo wnmp_h(($wnmpConfig['MYSQL_HOST'] ?? 'N/A') . ':' . ($wnmpConfig['MYSQL_PORT'] ?? 'N/A')); ?></span>
                </div>
                <div class="config-row">
                    <span class="ck" data-i18n="config_generated">Generated At</span>
                    <span class="cv"><?php echo wnmp_h($wnmpConfig['GENERATED_AT'] ?? 'N/A'); ?></span>
                </div>
            </div>
        </div>
    </div>
<?php else: ?>
    <div class="card warn-card">
        <div class="card-title" data-i18n="section_config">Runtime Config</div>
        <div class="card-body" data-i18n="config_missing_warn">runtime-config.php not found or incomplete. Please initialize the environment via WNMPanel.exe.</div>
    </div>
<?php endif; ?>

    <!-- Footer -->
    <div class="footer">
        <p data-i18n="footer_generated">Generated by WNMP Runtime</p>
    </div>
</div>

<script>
(function(){
    // i18n dictionary
    var dict = {
        zh: {
            hero_title: 'WNMP Runtime',
            hero_sub: '本地默认站点环境检测页',
            hero_heading: '环境已初始化',
            hero_desc: '这是 WNMP Runtime 本地默认站点的环境检测页。',
            section_runtime: '运行环境概览',
            section_extensions: 'PHP 扩展检查',
            section_config: '运行配置',
            label_server_software: '服务器软件',
            label_php_version: 'PHP 版本',
            label_mysql_port: 'MySQL 端口',
            label_document_root: '站点根目录',
            label_http_port: 'HTTP 端口',
            label_https_port: 'HTTPS 端口',
            status_available: '可用',
            status_unavailable: '不可用',
            mysql_reachable: '端口可达（{0}）',
            mysql_unreachable: '端口不可达（{0}）',
            mysql_pwd_note: 'Root 密码不会保存在本地，请在初始化时妥善保存。',
            config_not_found: 'runtime-config.php 未找到',
            config_incomplete: 'runtime-config.php 信息不完整',
            config_generated: '生成时间',
            https_disabled: '未启用',
            config_missing_warn: 'runtime-config.php 未找到或信息不完整，请通过 WNMPanel.exe 初始化环境。',
            footer_generated: '由 WNMP Runtime 生成',
            meta_description: 'WNMP Runtime 本地默认站点环境检测页。'
        },
        en: {
            hero_title: 'WNMP Runtime',
            hero_sub: 'Local Default Site Environment Check',
            hero_heading: 'Environment Initialized',
            hero_desc: 'This is the local default site detection page for WNMP Runtime.',
            section_runtime: 'Runtime Overview',
            section_extensions: 'PHP Extensions',
            section_config: 'Runtime Config',
            label_server_software: 'Server Software',
            label_php_version: 'PHP Version',
            label_mysql_port: 'MySQL Port',
            label_document_root: 'Document Root',
            label_http_port: 'HTTP Port',
            label_https_port: 'HTTPS Port',
            status_available: 'Available',
            status_unavailable: 'Not Available',
            mysql_reachable: 'Port reachable ({0})',
            mysql_unreachable: 'Port not reachable ({0})',
            mysql_pwd_note: 'Root password is not stored locally. Please save it during initial setup.',
            config_not_found: 'runtime-config.php not found',
            config_incomplete: 'runtime-config.php incomplete',
            config_generated: 'Generated At',
            https_disabled: 'Disabled',
            config_missing_warn: 'runtime-config.php not found or incomplete. Please initialize the environment via WNMPanel.exe.',
            footer_generated: 'Generated by WNMP Runtime',
            meta_description: 'WNMP Runtime local default site environment check page.'
        }
    };

    var STORAGE_KEY = 'wnmp_default_site_lang';

    function detectLang() {
        var saved = null;
        try { saved = localStorage.getItem(STORAGE_KEY); } catch(e) {}
        if (saved === 'zh' || saved === 'en') return saved;
        var nav = (navigator.languages && navigator.languages[0]) || navigator.language || navigator.userLanguage || 'en';
        return nav.toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en';
    }

    function applyLang(lang) {
        var d = dict[lang] || dict['en'];
        // Update html lang & title
        document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en-US';
        document.title = d.hero_title + ' - ' + d.hero_sub;
        // Update meta description
        var metaDesc = document.querySelector('meta[name="description"]');
        if (metaDesc && d.meta_description) { metaDesc.setAttribute('content', d.meta_description); }
        // Update data-i18n elements
        var els = document.querySelectorAll('[data-i18n]');
        for (var i = 0; i < els.length; i++) {
            var key = els[i].getAttribute('data-i18n');
            if (d[key] !== undefined) {
                var args = els[i].getAttribute('data-i18n-args') || '';
                var text = d[key];
                if (args && text.indexOf('{0}') !== -1) {
                    text = text.replace('{0}', args);
                }
                els[i].textContent = text;
            }
        }
        // Update lang buttons
        var btns = document.querySelectorAll('.lang-btn');
        for (var j = 0; j < btns.length; j++) {
            btns[j].classList.toggle('active', btns[j].getAttribute('data-lang') === lang);
        }
    }

    window.switchLang = function(lang) {
        applyLang(lang);
        try { localStorage.setItem(STORAGE_KEY, lang); } catch(e) {}
    };

    applyLang(detectLang());
})();
</script>
</body>
</html>
'''


def generate_runtime_config(web_root, cfg, root_dir=None):
    """生成或更新 web_root/runtime-config.php。

    端口优先从实际配置文件解析，解析失败回退 runtime.ini 默认值。
    HTTPS 未启用时 HTTPS_PORT 写空字符串；HTTP 未启用时 HTTP_PORT 写空字符串。
    """
    from runtime import wnmp_config

    config_path = os.path.join(web_root, "runtime-config.php")
    os.makedirs(web_root, exist_ok=True)

    # 从实际配置文件解析端口，解析失败回退 runtime.ini
    if root_dir:
        eff = wnmp_config.get_effective_nginx_listens(root_dir, cfg)
        # HTTP 端口：有 HTTP listen 就写第一个，否则写空字符串
        http_port = str(eff["http"][0]) if eff["http"] else ""
        # HTTPS 端口：有 ssl listen 就写第一个，否则写空字符串
        https_port = str(eff["https"][0]) if eff["https"] else ""
        enable_https = "1" if eff["https"] else "0"
        mysql_port = str(wnmp_config.get_effective_mysql_port(root_dir, cfg))
    else:
        http_port = wnmp_config.get(cfg, "HTTP_PORT")
        https_port = wnmp_config.get(cfg, "HTTPS_PORT")
        mysql_port = wnmp_config.get(cfg, "MYSQL_PORT")
        enable_https = wnmp_config.get(cfg, "ENABLE_HTTPS")

    content = "<?php\n"
    content += "// Auto-generated by WNMP Runtime - DO NOT EDIT\n"
    content += "// 端口值来自实际配置文件解析，与用户配置一致\n"
    content += "$runtimeConfig = [\n"
    content += "    'MYSQL_HOST' => '{}',\n".format(wnmp_config.get(cfg, "MYSQL_HOST"))
    content += "    'MYSQL_PORT' => '{}',\n".format(mysql_port)
    content += "    'HTTP_PORT' => '{}',\n".format(http_port)
    content += "    'HTTPS_PORT' => '{}',\n".format(https_port)
    content += "    'ENABLE_HTTPS' => '{}',\n".format(enable_https)
    content += "    'WEB_ROOT' => '{}',\n".format(wnmp_config.get(cfg, "WEB_ROOT"))
    content += "    'GENERATED_AT' => '{}',\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    content += "];\n"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)

    return config_path


def _is_default_index(web_root):
    """检查 web_root/index.php 是否为默认检测页（含版本标记）。"""
    index_path = os.path.join(web_root, "index.php")
    if not os.path.isfile(index_path):
        return False
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read(512)
        return DEFAULT_INDEX_MARKER in content
    except Exception:
        return False


def init_default_index(web_root, force=False):
    """初始化默认检测页（仅 init 命令调用）。

    - index.php 不存在：生成默认检测页
    - index.php 存在且含默认标记：force=True 时覆盖，否则跳过
    - index.php 是用户文件：绝不覆盖
    """
    index_path = os.path.join(web_root, "index.php")
    if not os.path.isfile(index_path):
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(INDEX_PHP_TEMPLATE)
        return index_path, True, "created"
    if _is_default_index(web_root):
        if force:
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(INDEX_PHP_TEMPLATE)
            return index_path, True, "force-overwritten"
        return index_path, False, "already-exists-default"
    return index_path, False, "user-file-skipped"


def update_runtime_config_for_start(root_dir, cfg, logger=None):
    """start 命令调用：仅在默认目录且 index.php 仍是默认页时更新 runtime-config.php。

    不创建 index.php，不恢复被删除的 index.php。
    """
    from runtime import wnmp_config
    from runtime.wnmp_path import is_default_web_root, resolve_path
    from runtime.wnmp_log import log_info, log_warn

    web_root_raw = wnmp_config.get(cfg, "WEB_ROOT")
    web_root = resolve_path(root_dir, web_root_raw)

    if not is_default_web_root(root_dir, web_root_raw):
        log_info(logger, "Current WEB_ROOT is custom directory, skipping runtime-config.php update: " + web_root)
        return False

    # 只有 index.php 仍是默认检测页时才更新 runtime-config.php
    if _is_default_index(web_root):
        config_path = generate_runtime_config(web_root, cfg, root_dir)
        log_info(logger, "Updated runtime-config.php: " + config_path)
        return True
    else:
        log_info(logger, "Default index.php is not default page or missing, skipping runtime-config.php update")
        return False


def maintain_default_site(root_dir, cfg, logger=None):
    """start 命令调用：不再自动创建或恢复 www/index.php。

    仅更新运行所需的内部配置（runtime-config.php），且仅在默认目录且 index.php 仍是默认页时。
    """
    from runtime import wnmp_config
    from runtime.wnmp_path import is_default_web_root, resolve_path
    from runtime.wnmp_log import log_info

    web_root_raw = wnmp_config.get(cfg, "WEB_ROOT")
    web_root = resolve_path(root_dir, web_root_raw)

    if not is_default_web_root(root_dir, web_root_raw):
        log_info(logger, "Current WEB_ROOT is custom directory, skipping default site maintenance: " + web_root)
        return False

    # start 不再创建/恢复 index.php，只更新 runtime-config.php（条件性）
    update_runtime_config_for_start(root_dir, cfg, logger)
    return True
