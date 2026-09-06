// -*- coding: utf-8 -*-
// WNMP Panel i18n - 轻量前端国际化模块
// 支持 zh-CN / en-US，按浏览器语言优先显示，localStorage 持久化手动选择

(function (root) {
    'use strict';

    var STORAGE_KEY = 'wnmp_panel_lang';

    // ---- 字典：中文为完整默认字典，英文为第二字典，缺 key 回退中文 ----
    var zhCN = {
        // 导航菜单
        'nav.overview': '环境总览',
        'nav.logs': '运行日志',
        'nav.settings': '设置',
        'nav.about': '关于',
        'nav.language': '语言',

        // 顶部标题
        'topbar.overview': '环境总览',
        'topbar.logs': '运行日志',
        'topbar.settings': '设置',
        'topbar.about': '关于',

        // 整体状态
        'state.running': '运行中',
        'state.stopped': '已停止',
        'state.partial': '部分运行',
        'state.uninitialized': '未初始化',
        'state.initializing': '初始化中',
        'state.starting': '启动中',
        'state.failed': '初始化失败',
        'state.unknown': '未知',
        'state.error': '异常',
        'state.external': '端口被占用',
        'state.pending_reload': '待重载',

        // 卡片状态
        'card_state.running': '运行中',
        'card_state.stopped': '已停止',
        'card_state.external': '端口冲突',
        'card_state.unknown': '未知',
        'card_state.partial': '部分异常',
        'card_state.error': '异常',
        'card_state.pending_reload': '待重载',

        // 指标标签
        'metric.http_port': 'HTTP 端口',
        'metric.https_port': 'HTTPS 端口',
        'metric.run_state': '运行状态',
        'metric.config_state': '配置状态',
        'metric.port': '端口',
        'metric.port_open': '端口开放',

        // 指标值
        'metric.disabled': '未启用',
        'metric.open': '开放',
        'metric.pending': '待生效',
        'metric.closed': '关闭',
        'metric.applied': '已应用',
        'metric.startup_apply': '启动后生效',
        'metric.need_restart': '需重启生效',
        'metric.yes': '是',
        'metric.no': '否',
        'metric.not_detected': '未检测',

        // 版本
        'version.nginx': 'Nginx 版本',
        'version.php': 'PHP-CGI 版本',
        'version.mysql': 'MySQL 版本',
        'version.not_queried': '未查询',
        'version.querying': '查询中...',
        'version.view': '查看版本',
        'version.refresh': '刷新版本',

        // 服务名
        'svc.nginx': 'Nginx',
        'svc.php': 'PHP-CGI',
        'svc.mysql': 'MySQL',

        // 按钮
        'btn.start': '启动',
        'btn.stop': '停止',
        'btn.restart': '重启',
        'btn.start_all': '全部启动',
        'btn.stop_all': '全部停止',
        'btn.restart_all': '全部重启',
        'toolbar.title': '环境操作',
        'btn.reload': '重载',
        'btn.open_site': '打开默认站点',
        'btn.reset_config': '重置配置',
        'btn.init_env': '初始化环境',
        'btn.initting': '正在初始化，请稍候...',
        'btn.retry': '重试检测',
        'btn.refresh': '刷新',
        'btn.save': '保存',
        'btn.reload_file': '重新加载原文件',
        'btn.copy_pwd': '复制密码',
        'btn.hide_pwd': '我已复制并保存，关闭',
        'btn.enable_autostart': '启用开机自启动',
        'btn.disable_autostart': '关闭开机自启动',
        'btn.refresh_status': '刷新状态',

        // 加载视图
        'loading.detecting': '正在检测 WNMP 环境状态...',

        // 错误视图
        'error.title': '状态获取失败',
        'error.message': '状态获取失败，请稍后重试或查看运行日志。',
        'error.view_runtime_log': '查看运行日志',
        'error.view_action_log': '查看动作输出日志',

        // 初始化视图
        'init.title': '环境初始化',
        'init.message': '当前 WNMP 环境尚未初始化，需要先初始化 Nginx、PHP、MySQL 运行环境。',
        'init.detail': '初始化将会：',
        'init.detail.1': '- 生成 Nginx/PHP/MySQL 配置文件',
        'init.detail.2': '- 生成自签名 SSL 证书',
        'init.detail.3': '- 创建默认站点',
        'init.detail.4': '- 初始化 MySQL 数据目录并生成 root 初始密码',
        'init.detail.5': '- 启动所有服务',

        // 初始化阶段
        'init.current_phase': '当前阶段：{phase}',
        'init.phase.preparing_config': '正在生成配置文件',
        'init.phase.mysql_secure_init': '正在初始化 MySQL',
        'init.phase.starting_php_cgi': '正在启动 PHP-CGI',
        'init.phase.starting_nginx': '正在启动 Nginx',
        'init.phase.verifying_services': '正在确认服务端口',
        'init.phase.failed': '初始化失败',
        'init.failed_message': '初始化失败：{message}',
        'init.failed_detail': '初始化过程中发生错误，请查看运行日志获取详细信息。您可以重新点击初始化按钮重试。',

        // 日志视图
        'log.runtime': '运行日志',
        'log.action': '动作输出日志',
        'log.loading': '加载中...',
        'log.empty': '(空)',
        'log.unavailable': '日志不可用',
        'log.parse_error': '日志解析失败',
        'log.fetch_error': '获取日志失败: HTTP ',
        'log.network_error': '网络错误',
        'log.timeout': '获取日志超时',

        // 设置视图
        'settings.autostart': '开机自启动',
        'settings.autostart_status': '状态：',
        'settings.autostart_detecting': '检测中...',
        'settings.autostart_enabled': '已启用',
        'settings.autostart_disabled': '已创建但未启用',
        'settings.autostart_not_found': '未启用',
        'settings.autostart_invalid': '配置异常',
        'settings.autostart_conflict': '同名任务冲突',
        'settings.autostart_detect_failed': '检测失败',
        'settings.autostart_network_error': '网络错误',
        'settings.autostart_timeout': '检测超时',
        'settings.autostart_gate_hint': '请先完成环境初始化，再启用开机自启动。未初始化时启用可能导致 MySQL 初始密码无法显示。',
        'settings.autostart_warning': '计划任务配置异常，请查看运行日志',
        'settings.autostart_detect_error': '检测失败：',
        'settings.panel_exit': 'Panel 自动退出',
        'settings.panel_exit_hint1': '关闭所有 Panel 页面后，Panel Server 将在约 60 秒后自动退出；页面只是切到后台不会退出；Nginx/PHP/MySQL 不受影响。',
        'settings.panel_exit_hint2': '再次双击 WNMPanel.exe 即可重新打开控制面板。',
        'settings.config_editor': '配置文件编辑',
        'settings.config_tab_nginx': 'Nginx 主配置',
        'settings.config_tab_nginx_site': 'Nginx 站点',
        'settings.config_tab_php': 'PHP',
        'settings.config_tab_phpcgi': 'PHP-CGI',
        'settings.config_tab_mysql': 'MySQL',
        'settings.config_tab_runtime': '面板配置 runtime.ini',
        'settings.config_placeholder': '点击上方标签加载配置文件',
        'settings.runtime_hint_title': '面板配置 / runtime.ini',
        'settings.runtime_hint_1': 'runtime.ini 控制 Panel/运行器/初始化默认值/自动启动等面板级配置；初始化后 Nginx/PHP/MySQL 的真实运行端口以各自配置文件为准。',
        'settings.runtime_hint_2': 'HTTP_PORT / HTTPS_PORT 不会自动覆盖已生成的 Nginx 站点配置；修改 Nginx 实际监听端口请编辑 Nginx 站点配置并重载/重启 Nginx。PHP_CGI_PORT / MYSQL_PORT 同理，以组件实际配置为准。',
        'settings.runtime_hint_3': '修改 Panel 端口、Panel host、自动打开浏览器等设置后需重启面板生效。',
        'settings.config_save_hint': '组件配置保存后需重启对应组件或环境生效；runtime.ini 仅控制面板级选项，保存后部分设置需重启面板生效。',

        // 关于视图
        'about.product_info': '产品信息',
        'about.product_desc': '本地 Windows Web 运行环境控制面板',
        'about.panel_version': 'Panel 版本',
        'about.build_id': '构建标识',
        'about.version_note': '此版本号为 Panel 控制面板自身版本，不是 Nginx / PHP / MySQL / Python 的组件版本。',
        'about.support_os': '支持系统',
        'about.support_os_value': 'Windows 10 / 11，Windows Server 2016 及以上',
        'about.run_mode': '运行模式',
        'about.run_mode_value': '本机面板，默认监听 127.0.0.1',
        'about.quick_start': '快速使用',
        'about.quick_start_1': '将 Nginx / PHP / MySQL 二进制文件放入对应 bin 目录',
        'about.quick_start_2': '首次打开面板后点击"初始化环境"生成默认配置',
        'about.quick_start_3': '初始化完成后可在首页启动、停止、重启各组件',
        'about.quick_start_4': 'Panel 配置位于 config/runtime.ini；Nginx/PHP/MySQL 配置位于各自组件目录，建议通过面板编辑',
        'about.quick_start_5': 'Nginx 独立站点配置请放入 bin\\nginx\\conf\\vhosts\\ 目录，文件内容通常是完整 server { ... } 配置块',
        'about.quick_start_6': '如启用开机自启动，面板会创建 Windows 计划任务',
        'about.key_paths': '关键路径',
        'about.root_dir': '项目根目录',
        'about.panel_port': 'Panel 端口',
        'about.config_dir': '配置目录',
        'about.logs_dir': '运行日志',
        'about.www_dir': '默认站点目录',
        'about.vhosts_dir': 'Nginx 站点配置目录',
        'about.dev_tip': '开发与排错',
        'about.dev_tip_cli': '开发排错使用：',
        'about.dev_tip_port': '默认端口来自 ',
        'about.dev_tip_port_suffix': ' 的 PANEL_PORT',
        'about.related_links': '相关链接',
        'about.project_website': '项目官网',
        'about.tech_support': '技术支持',
        'about.dev_build': '开发构建',

        // MySQL 密码模态框
        'modal.mysql_pwd_title': 'MySQL Root 初始密码',
        'modal.mysql_pwd_hint': '密码仅在本弹窗中自动显示一次，请立即复制并妥善保存；关闭后面板不会再次显示该密码。',
        'modal.mysql_pwd_note': '密码仅在本弹窗中自动显示一次，请立即复制并妥善保存；关闭后面板不会再次显示该密码。',

        // Toast / Flash
        'toast.copied': '已复制',
        'toast.copy_failed': '复制失败，请手动选中复制',
        'toast.pwd_hidden': '密码已隐藏，无法复制',
        'toast.close': '关闭',
        'toast.close_aria': '关闭提示',

        // 操作结果
        'action.busy': '已有操作正在执行，请稍候',
        'action.busy_init': '初始化/启动任务仍在执行，请等待当前任务完成',
        'action.busy_component': '当前操作正在执行，请稍候',
        'action.success': '成功',
        'action.failed': '失败',
        'action.result_success': '{name}成功',
        'action.result_failed': '{name}失败',
        'action.log_links': '（{runtime} | {action}）',
        'action.execute_complete': '执行完成',
        'action.execute_failed': '操作失败',
        'action.timeout': '执行超时，请查看运行日志',
        'action.network_error': '网络错误：无法连接 Panel Server，请确认控制面板进程仍在运行',
        'action.network_error_short': '网络错误：无法连接 Panel Server',
        'action.response_parse_error': '响应解析失败',
        'action.not_initialized': '环境尚未初始化，请先初始化环境',
        'action.env_not_init_no_stop': '环境尚未初始化，无需停止',
        'action.init_fail_with_pwd': 'MySQL root 初始密码已生成，请立即保存；但环境启动未完全成功，请查看日志处理后重试。',
        'action.init_fail': '初始化失败',
        'action.autostart_admin': '。启用/关闭开机自启动需要管理员权限，请以管理员权限运行 WNMPanel.exe',
        'action.runtime_log': '运行日志',
        'action.action_log': '动作日志',

        // 操作名称
        'action_name.start_env': '启动环境',
        'action_name.init_env': '初始化环境',
        'action_name.stop_env': '停止环境',
        'action_name.restart_env': '重启环境',
        'action_name.open_site': '打开站点',
        'action_name.start_nginx': '启动 Nginx',
        'action_name.stop_nginx': '停止 Nginx',
        'action_name.restart_nginx': '重启 Nginx',
        'action_name.reload_nginx': '重载 Nginx',
        'action_name.start_php': '启动 PHP-CGI',
        'action_name.stop_php': '停止 PHP-CGI',
        'action_name.restart_php': '重启 PHP-CGI',
        'action_name.start_mysql': '启动 MySQL',
        'action_name.stop_mysql': '停止 MySQL',
        'action_name.restart_mysql': '重启 MySQL',
        'action_name.reset_config': '重置配置',

        // 配置编辑器
        'config.not_generated': '配置文件尚未生成，请先初始化环境',
        'config.load_error': '加载配置文件失败：HTTP ',
        'config.network_error_detail': '网络错误：无法加载配置文件，请确认 Panel Server 正在运行，并查看 logs/panel/panel_server.log',
        'config.timeout_detail': '加载超时：请确认 Panel Server 正在运行',
        'config.parse_error': '配置文件响应解析失败',
        'config.saved': '配置已保存',
        'config.backup_path': '。备份文件：',
        'config.save_failed': '保存失败',
        'config.save_network_error': '网络错误：无法连接 Panel Server，请确认控制面板进程仍在运行，并查看 logs/panel/panel_server.log',
        'config.save_timeout': '保存超时：Nginx 配置校验可能卡住，请查看 panel_server.log',

        // 状态检测
        'status.parse_error': '状态响应解析失败',
        'status.network_error': '网络错误，无法连接 Panel 服务',
        'status.timeout': '状态检测超时，正在重试...',
        'status.fetch_failed': '状态获取失败',
        'status.not_refreshed': ' (状态未刷新)',

        // 错误视图（动态）
        'error.status_parse': '状态响应解析失败',
        'error.network': '网络错误，无法连接 Panel 服务',
        'error.status_timeout': '状态检测超时，正在重试...',
        'error.status_detecting': '状态检测中，服务可能正在随系统启动，请稍候...',
        'error.status_fetch': '状态获取失败',

        // 版本查询
        'version.query_failed': '查询失败',
        'version.network_error': '网络错误',
        'version.timeout': '查询超时',
        'version.query_network_error': '查询网络错误',
        'version.query_timeout': '查询超时',

        // 确认框
        'confirm.reset_config': '将备份并恢复默认 Nginx/PHP/PHP-CGI/MySQL 组件配置，不会删除 MySQL 数据库、不会删除网站目录、不会重置面板配置 runtime.ini。重置后可能需要重启或重载对应组件后生效。',

        // Panel 版本信息
        'panel_version.load_failed': 'Panel 版本信息加载失败',

        // 侧边栏
        'sidebar.panel': 'Panel ',
        'sidebar.version_unknown': '版本未知',
        'sidebar.brand_sub': '本地运行环境控制台',

        // 顶部副标题
        'topbar.subtitle': '本地 Windows Web 运行环境控制台',

        // 初始化卡片（新 UI）
        'init.card_title': '环境尚未初始化',
        'init.card_desc': '初始化将自动生成基础配置并完成默认运行环境准备',
        'init.step1': '生成 Nginx / PHP / MySQL 配置文件',
        'init.step2': '创建默认站点与运行目录',
        'init.step3': '生成本地开发证书',
        'init.step4': '初始化 MySQL 数据目录与 root 初始密码',
        'init.step5': '启动所有核心服务',

        // 卡片状态 - 待初始化
        'card_state.pending_init': '待初始化',

        // 指标值 - 未启动/未生成
        'metric.not_started': '未启动',
        'metric.not_generated': '未生成',

        // 标点符号
        'punct.colon': '：',

        // 后端 message 映射（前端可识别的固定 key）
        'backend.已有操作正在执行，请稍候': '已有操作正在执行，请稍候',
        'backend.环境尚未初始化，请先初始化环境': '环境尚未初始化，请先初始化环境',
        'backend.环境尚未初始化': '环境尚未初始化',
        'backend.执行完成': '执行完成',
        'backend.执行超时，请查看动作输出日志': '执行超时，请查看动作输出日志',
        'backend.执行异常，请查看动作输出日志': '执行异常，请查看动作输出日志',
        'backend.执行失败，请查看运行日志': '执行失败，请查看运行日志',
        'backend.配置已保存': '配置已保存',
        'backend.配置文件保存成功': '配置文件保存成功',
        'backend.执行中...': '执行中...',

        // 环境信息模块（第二阶段前端展示用）
        'env_info.module_title': '环境信息',
        'env_info.subtitle': 'Panel 配置位于 config/runtime.ini；Nginx/PHP/MySQL 配置位于各自组件目录，可通过面板编辑。',
        'env_info.main_config': '主配置文件',
        'env_info.default_site_config': '默认站点配置',
        'env_info.vhosts_dir': '新增站点目录',
        'env_info.http_extensions': 'HTTP 级扩展',
        'env_info.server_extensions': '默认站点扩展',
        'env_info.php_config': 'PHP 配置文件',
        'env_info.php_cgi_config': 'PHP-CGI 进程配置',
        'env_info.mysql_config': 'MySQL 主配置文件',
        'env_info.edit_main_config': '编辑主配置',
        'env_info.edit_default_site': '编辑默认站点',
        'env_info.open_site_dir': '打开站点目录',
        'env_info.open_config_dir': '打开配置目录',
        'env_info.edit_config': '编辑配置',
        'env_info.open_dir': '打开目录',
        'env_info.status_applied': '已应用',
        'env_info.status_pending': '需重启/重载生效',
        'env_info.status_unknown': '未检测',
        'env_info.dir_opened': '目录已打开',
        'env_info.dir_open_failed': '目录打开失败，请检查面板是否以本机方式运行'
    };

    var enUS = {
        // 导航菜单
        'nav.overview': 'Overview',
        'nav.logs': 'Logs',
        'nav.settings': 'Settings',
        'nav.about': 'About',
        'nav.language': 'Language',

        // 顶部标题
        'topbar.overview': 'Overview',
        'topbar.logs': 'Logs',
        'topbar.settings': 'Settings',
        'topbar.about': 'About',

        // 整体状态
        'state.running': 'Running',
        'state.stopped': 'Stopped',
        'state.partial': 'Partial',
        'state.uninitialized': 'Uninitialized',
        'state.unknown': 'Unknown',
        'state.error': 'Error',
        'state.external': 'Port In Use',
        'state.pending_reload': 'Pending Reload',
        'state.initializing': 'Initializing',
        'state.starting': 'Starting',
        'state.failed': 'Init Failed',

        // 卡片状态
        'card_state.running': 'Running',
        'card_state.stopped': 'Stopped',
        'card_state.external': 'Port Conflict',
        'card_state.unknown': 'Unknown',
        'card_state.partial': 'Partial Error',
        'card_state.error': 'Error',
        'card_state.pending_reload': 'Pending Reload',

        // 指标标签
        'metric.http_port': 'HTTP Port',
        'metric.https_port': 'HTTPS Port',
        'metric.run_state': 'Status',
        'metric.config_state': 'Config',
        'metric.port': 'Port',
        'metric.port_open': 'Port Open',

        // 指标值
        'metric.disabled': 'Disabled',
        'metric.open': 'Open',
        'metric.pending': 'Pending',
        'metric.closed': 'Closed',
        'metric.applied': 'Applied',
        'metric.startup_apply': 'Apply on Start',
        'metric.need_restart': 'Needs Restart',
        'metric.yes': 'Yes',
        'metric.no': 'No',
        'metric.not_detected': 'Not Detected',

        // 版本
        'version.nginx': 'Nginx Version',
        'version.php': 'PHP-CGI Version',
        'version.mysql': 'MySQL Version',
        'version.not_queried': 'Not Queried',
        'version.querying': 'Querying...',
        'version.view': 'View Version',
        'version.refresh': 'Refresh Version',

        // 服务名
        'svc.nginx': 'Nginx',
        'svc.php': 'PHP-CGI',
        'svc.mysql': 'MySQL',

        // 按钮
        'btn.start': 'Start',
        'btn.stop': 'Stop',
        'btn.restart': 'Restart',
        'btn.start_all': 'Start All',
        'btn.stop_all': 'Stop All',
        'btn.restart_all': 'Restart All',
        'toolbar.title': 'Environment Actions',
        'btn.reload': 'Reload',
        'btn.open_site': 'Open Default Site',
        'btn.reset_config': 'Reset Config',
        'btn.init_env': 'Initialize Environment',
        'btn.initting': 'Initializing...',
        'btn.retry': 'Retry',
        'btn.refresh': 'Refresh',
        'btn.save': 'Save',
        'btn.reload_file': 'Reload File',
        'btn.copy_pwd': 'Copy Password',
        'btn.hide_pwd': "I've copied and saved it, close",
        'btn.enable_autostart': 'Enable Autostart',
        'btn.disable_autostart': 'Disable Autostart',
        'btn.refresh_status': 'Refresh Status',

        // 加载视图
        'loading.detecting': 'Detecting WNMP environment status...',

        // 错误视图
        'error.title': 'Status Error',
        'error.message': 'Failed to get status. Please try again later or check the logs.',
        'error.view_runtime_log': 'View Runtime Log',
        'error.view_action_log': 'View Action Log',

        // 初始化视图
        'init.title': 'Environment Initialization',
        'init.message': 'The WNMP environment is not initialized. Nginx, PHP, and MySQL need to be initialized first.',
        'init.detail': 'Initialization will:',
        'init.detail.1': '- Generate Nginx/PHP/MySQL config files',
        'init.detail.2': '- Generate self-signed SSL certificate',
        'init.detail.3': '- Create default site',
        'init.detail.4': '- Initialize MySQL data directory and generate root password',
        'init.detail.5': '- Start all services',

        // 初始化阶段
        'init.current_phase': 'Current phase: {phase}',
        'init.phase.preparing_config': 'Generating config files',
        'init.phase.mysql_secure_init': 'Initializing MySQL',
        'init.phase.starting_php_cgi': 'Starting PHP-CGI',
        'init.phase.starting_nginx': 'Starting Nginx',
        'init.phase.verifying_services': 'Verifying service ports',
        'init.phase.failed': 'Initialization failed',
        'init.failed_message': 'Initialization failed: {message}',
        'init.failed_detail': 'An error occurred during initialization. Please check the runtime log for details. You can retry by clicking the initialize button again.',

        // 日志视图
        'log.runtime': 'Runtime Log',
        'log.action': 'Action Output Log',
        'log.loading': 'Loading...',
        'log.empty': '(Empty)',
        'log.unavailable': 'Log unavailable',
        'log.parse_error': 'Log parse error',
        'log.fetch_error': 'Failed to fetch log: HTTP ',
        'log.network_error': 'Network error',
        'log.timeout': 'Log fetch timeout',

        // 设置视图
        'settings.autostart': 'Auto Start on Boot',
        'settings.autostart_status': 'Status: ',
        'settings.autostart_detecting': 'Detecting...',
        'settings.autostart_enabled': 'Enabled',
        'settings.autostart_disabled': 'Created but Disabled',
        'settings.autostart_not_found': 'Not Enabled',
        'settings.autostart_invalid': 'Invalid Config',
        'settings.autostart_conflict': 'Task Name Conflict',
        'settings.autostart_detect_failed': 'Detection Failed',
        'settings.autostart_network_error': 'Network Error',
        'settings.autostart_timeout': 'Detection Timeout',
        'settings.autostart_gate_hint': 'Please initialize the environment before enabling autostart. Enabling without initialization may prevent MySQL password from being displayed.',
        'settings.autostart_warning': 'Task scheduler config error, please check runtime log',
        'settings.autostart_detect_error': 'Detection failed: ',
        'settings.panel_exit': 'Panel Auto Exit',
        'settings.panel_exit_hint1': 'After closing all Panel pages, Panel Server will exit in about 60 seconds. Switching to another tab will not trigger exit. Nginx/PHP/MySQL will not be affected.',
        'settings.panel_exit_hint2': 'Double-click WNMPanel.exe to reopen the panel.',
        'settings.config_editor': 'Config File Editor',
        'settings.config_tab_nginx': 'Nginx Main Config',
        'settings.config_tab_nginx_site': 'Nginx Site',
        'settings.config_tab_php': 'PHP',
        'settings.config_tab_phpcgi': 'PHP-CGI',
        'settings.config_tab_mysql': 'MySQL',
        'settings.config_tab_runtime': 'Panel Config runtime.ini',
        'settings.config_placeholder': 'Click a tab above to load config file',
        'settings.runtime_hint_title': 'Panel Config / runtime.ini',
        'settings.runtime_hint_1': 'runtime.ini controls Panel/runner/initialization defaults/autostart etc. After initialization, actual Nginx/PHP/MySQL ports are determined by their own config files.',
        'settings.runtime_hint_2': 'HTTP_PORT / HTTPS_PORT will not auto-overwrite generated Nginx site config. To change Nginx listening ports, edit the Nginx site config and reload/restart Nginx. Same for PHP_CGI_PORT / MYSQL_PORT.',
        'settings.runtime_hint_3': 'After changing Panel port, Panel host, or auto-open browser settings, restart the panel to take effect.',
        'settings.config_save_hint': 'Component config changes require restarting the component or environment. runtime.ini only controls panel-level options; some settings need a panel restart after saving.',

        // 关于视图
        'about.product_info': 'Product Information',
        'about.product_desc': 'Local Windows Web runtime control panel',
        'about.panel_version': 'Panel Version',
        'about.build_id': 'Build ID',
        'about.version_note': 'This version refers to the Panel itself, not the Nginx/PHP/MySQL/Python component versions.',
        'about.support_os': 'Supported OS',
        'about.support_os_value': 'Windows 10 / 11, Windows Server 2016 and above',
        'about.run_mode': 'Run Mode',
        'about.run_mode_value': 'Local panel, default listen on 127.0.0.1',
        'about.quick_start': 'Quick Start',
        'about.quick_start_1': 'Place Nginx / PHP / MySQL binaries into the corresponding bin directories',
        'about.quick_start_2': 'Click "Initialize Environment" on first launch to generate default configs',
        'about.quick_start_3': 'After initialization, start, stop, or restart components from the Overview page',
        'about.quick_start_4': 'Panel config is in config/runtime.ini; Nginx/PHP/MySQL configs are in their own component directories; editing via the panel is recommended',
        'about.quick_start_5': 'For Nginx standalone site configs, place files in bin\\nginx\\conf\\vhosts\\ with a complete server { ... } block',
        'about.quick_start_6': 'Enabling auto-start will create a Windows Scheduled Task',
        'about.key_paths': 'Key Paths',
        'about.root_dir': 'Project Root',
        'about.panel_port': 'Panel Port',
        'about.config_dir': 'Config Directory',
        'about.logs_dir': 'Log Directory',
        'about.www_dir': 'Default Site Directory',
        'about.vhosts_dir': 'Nginx Site Config Directory',
        'about.dev_tip': 'Development & Troubleshooting',
        'about.dev_tip_cli': 'For debugging: ',
        'about.dev_tip_port': 'Default port from ',
        'about.dev_tip_port_suffix': "'s PANEL_PORT",
        'about.related_links': 'Related Links',
        'about.project_website': 'Project Website',
        'about.tech_support': 'Technical Support',
        'about.dev_build': 'Dev Build',

        // MySQL 密码模态框
        'modal.mysql_pwd_title': 'MySQL Root Initial Password',
        'modal.mysql_pwd_hint': 'The password is shown automatically in this dialog only once. Please copy and store it safely; it will not be shown again after the dialog is closed.',
        'modal.mysql_pwd_note': 'The password is shown automatically in this dialog only once. Please copy and store it safely; it will not be shown again after the dialog is closed.',

        // Toast / Flash
        'toast.copied': 'Copied',
        'toast.copy_failed': 'Copy failed, please select and copy manually',
        'toast.pwd_hidden': 'Password hidden, cannot copy',
        'toast.close': 'Close',
        'toast.close_aria': 'Close notification',

        // 操作结果
        'action.busy': 'Another action is in progress, please wait',
        'action.busy_init': 'Initialization/startup task is still running, please wait for it to complete',
        'action.busy_component': 'Current operation is in progress, please wait',
        'action.success': 'succeeded',
        'action.failed': 'failed',
        'action.result_success': '{name} succeeded',
        'action.result_failed': '{name} failed',
        'action.log_links': '({runtime} | {action})',
        'action.execute_complete': 'Completed',
        'action.execute_failed': 'Operation failed',
        'action.timeout': 'Execution timeout, please check the logs',
        'action.network_error': 'Network error: Cannot connect to Panel Server. Please make sure the panel process is still running',
        'action.network_error_short': 'Network error: Cannot connect to Panel Server',
        'action.response_parse_error': 'Response parse error',
        'action.not_initialized': 'Environment not initialized. Please initialize first.',
        'action.env_not_init_no_stop': 'Environment not initialized, no need to stop',
        'action.init_fail_with_pwd': 'MySQL root initial password has been generated. Please save it now. However, the environment did not start fully. Please check the logs and retry.',
        'action.init_fail': 'Initialization failed',
        'action.autostart_admin': '. Enabling/disabling autostart requires administrator privileges. Please run WNMPanel.exe as administrator',
        'action.runtime_log': 'Runtime Log',
        'action.action_log': 'Action Log',

        // 操作名称
        'action_name.start_env': 'Start Environment',
        'action_name.init_env': 'Initialize Environment',
        'action_name.stop_env': 'Stop Environment',
        'action_name.restart_env': 'Restart Environment',
        'action_name.open_site': 'Open Site',
        'action_name.start_nginx': 'Start Nginx',
        'action_name.stop_nginx': 'Stop Nginx',
        'action_name.restart_nginx': 'Restart Nginx',
        'action_name.reload_nginx': 'Reload Nginx',
        'action_name.start_php': 'Start PHP-CGI',
        'action_name.stop_php': 'Stop PHP-CGI',
        'action_name.restart_php': 'Restart PHP-CGI',
        'action_name.start_mysql': 'Start MySQL',
        'action_name.stop_mysql': 'Stop MySQL',
        'action_name.restart_mysql': 'Restart MySQL',
        'action_name.reset_config': 'Reset Config',

        // 配置编辑器
        'config.not_generated': 'Config file not generated yet. Please initialize the environment first.',
        'config.load_error': 'Failed to load config file: HTTP ',
        'config.network_error_detail': 'Network error: Cannot load config file. Please make sure Panel Server is running and check logs/panel/panel_server.log',
        'config.timeout_detail': 'Load timeout: Please make sure Panel Server is running',
        'config.parse_error': 'Config file response parse error',
        'config.saved': 'Config saved',
        'config.backup_path': '. Backup file: ',
        'config.save_failed': 'Save failed',
        'config.save_network_error': 'Network error: Cannot connect to Panel Server. Please make sure the panel process is still running and check logs/panel/panel_server.log',
        'config.save_timeout': 'Save timeout: Nginx config validation may be stuck. Check panel_server.log',

        // 状态检测
        'status.parse_error': 'Status response parse error',
        'status.network_error': 'Network error, cannot connect to Panel service',
        'status.timeout': 'Status detection timeout, retrying...',
        'status.fetch_failed': 'Failed to get status',
        'status.not_refreshed': ' (not refreshed)',

        // 错误视图（动态）
        'error.status_parse': 'Status response parse error',
        'error.network': 'Network error, cannot connect to Panel service',
        'error.status_timeout': 'Status detection timeout, retrying...',
        'error.status_detecting': 'Detecting status, services may be starting with the system, please wait...',
        'error.status_fetch': 'Failed to get status',

        // 版本查询
        'version.query_failed': 'Query failed',
        'version.network_error': 'Network error',
        'version.timeout': 'Query timeout',
        'version.query_network_error': ' query network error',
        'version.query_timeout': ' query timeout',

        // 确认框
        'confirm.reset_config': 'This will back up and restore default Nginx/PHP/PHP-CGI/MySQL component configs. It will NOT delete MySQL databases, website directories, or reset panel config runtime.ini. You may need to restart or reload components after reset.',

        // Panel 版本信息
        'panel_version.load_failed': 'Failed to load Panel version info',

        // 侧边栏
        'sidebar.panel': 'Panel ',
        'sidebar.version_unknown': 'Version Unknown',
        'sidebar.brand_sub': 'Local Runtime Console',

        // 顶部副标题
        'topbar.subtitle': 'Local Windows Web Runtime Control Panel',

        // 初始化卡片（新 UI）
        'init.card_title': 'Environment Not Initialized',
        'init.card_desc': 'Initialization will auto-generate base configs and prepare the default runtime environment',
        'init.step1': 'Generate Nginx / PHP / MySQL config files',
        'init.step2': 'Create default site and runtime directories',
        'init.step3': 'Generate local development certificate',
        'init.step4': 'Initialize MySQL data directory and root initial password',
        'init.step5': 'Start all core services',

        // 卡片状态 - 待初始化
        'card_state.pending_init': 'Pending Initialization',

        // 指标值 - 未启动/未生成
        'metric.not_started': 'Not Started',
        'metric.not_generated': 'Not Generated',

        // 标点符号
        'punct.colon': ': ',

        // 后端 message 映射
        'backend.已有操作正在执行，请稍候': 'Another action is in progress, please wait',
        'backend.环境尚未初始化，请先初始化环境': 'Environment not initialized. Please initialize first.',
        'backend.环境尚未初始化': 'Environment not initialized.',
        'backend.执行完成': 'Completed',
        'backend.执行超时，请查看动作输出日志': 'Execution timeout, please check action output log',
        'backend.执行异常，请查看动作输出日志': 'Execution error, please check action output log',
        'backend.执行失败，请查看运行日志': 'Execution failed, please check runtime log',
        'backend.配置已保存': 'Config saved',
        'backend.配置文件保存成功': 'Config file saved successfully',
        'backend.执行中...': 'Executing...',

        // Environment Info Module (Phase 2 frontend use)
        'env_info.module_title': 'Environment Info',
        'env_info.subtitle': 'Panel config is in config/runtime.ini; Nginx/PHP/MySQL configs are in their own component directories, editable via the panel.',
        'env_info.main_config': 'Main Config',
        'env_info.default_site_config': 'Default Site Config',
        'env_info.vhosts_dir': 'Virtual Hosts',
        'env_info.http_extensions': 'HTTP Extensions',
        'env_info.server_extensions': 'Server Extensions',
        'env_info.php_config': 'PHP Config',
        'env_info.php_cgi_config': 'PHP-CGI Process Config',
        'env_info.mysql_config': 'MySQL Main Config',
        'env_info.edit_main_config': 'Edit Main Config',
        'env_info.edit_default_site': 'Edit Default Site',
        'env_info.open_site_dir': 'Open Site Directory',
        'env_info.open_config_dir': 'Open Config Directory',
        'env_info.edit_config': 'Edit Config',
        'env_info.open_dir': 'Open Directory',
        'env_info.status_applied': 'Applied',
        'env_info.status_pending': 'Needs Restart/Reload',
        'env_info.status_unknown': 'Not Detected',
        'env_info.dir_opened': 'Directory opened',
        'env_info.dir_open_failed': 'Failed to open directory. Please check if the panel is accessed locally.'
    };

    var dicts = {
        'zh-CN': zhCN,
        'en-US': enUS
    };

    // ---- 当前语言 ----
    var _currentLang = 'zh-CN';

    // ---- 检测浏览器语言 ----
    // 只读第一优先语言，不遍历所有 languages
    function _detectBrowserLang() {
        try {
            var first = (navigator.languages && navigator.languages[0])
                        || navigator.language
                        || navigator.userLanguage
                        || '';
            if (first && first.toLowerCase().indexOf('zh') === 0) return 'zh-CN';
        } catch (e) {}
        return 'en-US';
    }

    // ---- 初始化语言 ----
    function _initLang() {
        var stored = null;
        try { stored = localStorage.getItem(STORAGE_KEY); } catch (e) {}
        if (stored === 'zh-CN' || stored === 'en-US') {
            _currentLang = stored;
        } else {
            _currentLang = _detectBrowserLang();
        }
        _updateHtmlLang();
    }

    // ---- 更新 html lang 属性 ----
    function _updateHtmlLang() {
        try { document.documentElement.lang = _currentLang; } catch (e) {}
    }

    // ---- 获取翻译 ----
    function t(key, params) {
        var dict = dicts[_currentLang] || dicts['zh-CN'];
        var val = dict[key];
        // 回退中文
        if (val === undefined) {
            val = dicts['zh-CN'][key];
        }
        if (val === undefined) {
            return key; // key 本身作为最终回退
        }
        // 参数替换：支持数组 {0},{1}... 和对象 {name},{value}...
        if (params) {
            if (Array.isArray(params)) {
                for (var i = 0; i < params.length; i++) {
                    val = val.replace('{' + i + '}', params[i]);
                }
            } else if (typeof params === 'object') {
                for (var k in params) {
                    if (params.hasOwnProperty(k)) {
                        val = val.replace('{' + k + '}', params[k]);
                    }
                }
            }
        }
        return val;
    }

    // ---- 设置语言 ----
    function setLang(lang) {
        if (lang !== 'zh-CN' && lang !== 'en-US') return;
        _currentLang = lang;
        try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) {}
        _updateHtmlLang();
        applyI18n();
    }

    // ---- 获取当前语言 ----
    function getLang() {
        return _currentLang;
    }

    // ---- 翻译后端 message ----
    // 对已知固定中文 message 做映射翻译，未知/动态消息保留原文
    function translateBackendMessage(msg) {
        if (!msg) return msg;
        // 如果当前是中文，直接返回
        if (_currentLang === 'zh-CN') return msg;
        // 查找后端 message 映射
        var key = 'backend.' + msg;
        var dict = dicts['en-US'];
        if (dict && dict[key] !== undefined) return dict[key];
        // 未匹配映射，保留原文
        return msg;
    }

    // ---- 应用 i18n 到 DOM ----
    // 支持 data-i18n（文本内容）、data-i18n-title、data-i18n-placeholder 属性
    function applyI18n(root) {
        root = root || document;
        // data-i18n: 替换文本内容
        var els = root.querySelectorAll('[data-i18n]');
        for (var i = 0; i < els.length; i++) {
            var key = els[i].getAttribute('data-i18n');
            if (key) els[i].textContent = t(key);
        }
        // data-i18n-title: 替换 title 属性
        var titleEls = root.querySelectorAll('[data-i18n-title]');
        for (var j = 0; j < titleEls.length; j++) {
            var tKey = titleEls[j].getAttribute('data-i18n-title');
            if (tKey) titleEls[j].setAttribute('title', t(tKey));
        }
        // data-i18n-placeholder: 替换 placeholder 属性
        var phEls = root.querySelectorAll('[data-i18n-placeholder]');
        for (var k = 0; k < phEls.length; k++) {
            var pKey = phEls[k].getAttribute('data-i18n-placeholder');
            if (pKey) phEls[k].setAttribute('placeholder', t(pKey));
        }
        // data-i18n-text: 替换文本内容（用于 nav-item 内的 span，避免覆盖整个 nav-item）
        var textEls = root.querySelectorAll('[data-i18n-text]');
        for (var m = 0; m < textEls.length; m++) {
            var txtKey = textEls[m].getAttribute('data-i18n-text');
            if (txtKey) textEls[m].textContent = t(txtKey);
        }
        // 更新语言切换按钮激活态
        _updateLangSwitcher();
    }

    // ---- 更新语言切换按钮激活态 ----
    function _updateLangSwitcher() {
        var btns = document.querySelectorAll('.lang-switch-btn');
        for (var i = 0; i < btns.length; i++) {
            var lang = btns[i].getAttribute('data-lang');
            // IE11 兼容：使用 WNMPCompat.toggleClass 替代 classList.toggle 第二参数
            WNMPCompat.toggleClass(btns[i], 'active', lang === _currentLang);
        }
    }

    // ---- 初始化 ----
    _initLang();

    // ---- 暴露 API ----
    var i18n = {
        t: t,
        setLang: setLang,
        getLang: getLang,
        applyI18n: applyI18n,
        translateBackendMessage: translateBackendMessage
    };

    // 挂载到全局
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = i18n;
    } else {
        root.i18n = i18n;
    }

})(typeof window !== 'undefined' ? window : this);
