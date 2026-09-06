## English Version
You can view the English version of this README [here](README_EN.md).
# WNMP Runtime Panel

一个面向 Windows 的本地 Nginx / PHP-CGI / MySQL 运行环境控制面板。

## 项目定位

WNMP Runtime Panel 用于在本机管理 WNMP（Windows + Nginx + PHP-CGI + MySQL）运行环境，提供初始化、启停、状态查看、配置编辑、日志查看、环境信息和开机自启动等能力。通过本地 Web 控制面板操作，不依赖系统 Python，无需命令行经验。

## 功能特性

- **本地 Web 控制面板**：双击 `WNMPanel.exe` 自动启动 Panel Server 并打开浏览器，无需手动配置
- **独立组件管理**：Nginx、PHP-CGI、MySQL 分别支持启停与实时状态检测
- **一键初始化**：首次使用时点击"初始化环境"，自动生成默认配置、默认站点、本地开发证书和 MySQL 数据目录
- **组件配置归位**：Panel 配置保留在 `config\runtime.ini`，Nginx/PHP/MySQL 配置位于各自组件目录，支持面板内编辑
- **Nginx 站点扩展**：`bin\nginx\conf\vhosts\` 目录用于新增独立站点配置；`bin\nginx\conf\custom\` 提供 http 级和 server 级扩展目录
- **运行日志查看**：面板内可查看 Panel 运行日志和动作输出日志
- **开机自启动**：基于 Windows 计划任务实现，任务名来自 `runtime.ini` 的 `SERVICE_NAME`
- **面板版本统一管理**：版本号由项目根目录 `VERSION` 文件统一维护
- **中英文界面**：面板支持中文和英文切换

## 支持环境

| | 支持 | 不支持 |
|---|---|---|
| **客户端** | Windows 10、Windows 11 | Windows 7、Windows 8 / 8.1 |
| **服务器** | Windows Server 2016 / 2019 / 2022 / 2025 | Windows Server 2008 / 2008 R2、Windows Server 2012 / 2012 R2 |

启动器在启动前会检测 Windows 版本，低于基线（NT 10.0，即 Windows 10 / Windows Server 2016）的版本将被阻断启动，并提示升级操作系统。

## 快速开始

### 1. 下载项目

下载或克隆项目到本地任意目录（路径支持包含空格和中文字符）。

### 2. 准备二进制文件

开源源码包通常不包含第三方 Nginx / PHP / MySQL 二进制文件，请自行下载对应 Windows 版本并放入指定目录。如果项目提供了完整运行包，请以发布包说明为准。

需要放入的二进制文件：

- **Python（必需）**：`bin\python\python.exe`
- **Nginx**：`bin\nginx\nginx.exe`
- **PHP**：`bin\php\php-cgi.exe`、`bin\php\php.exe`
- **MySQL**：`bin\mysql\bin\mysqld.exe`
- **OpenSSL（可选）**：`bin\openssl\openssl.exe`

### 3. 启动面板

双击项目根目录下的 `WNMPanel.exe`，Panel Server 将自动启动并打开浏览器访问控制面板。启动器会：

- 以自身所在目录作为项目根目录
- 读取 `config\runtime.ini` 中的 `PANEL_HOST` 和 `PANEL_PORT`
- 检查内置 Python 是否存在
- 如果 Panel 已在运行则直接打开浏览器，不会重复启动

### 4. 初始化环境

首次打开面板后，页面会显示"环境尚未初始化"，点击 **"初始化环境"** 按钮即可自动完成：

- 生成 Nginx、PHP-CGI、MySQL 默认配置
- 创建默认站点和本地开发证书
- 初始化 MySQL root 密码并创建数据目录
- 初始化完成后自动启动所有组件

### 5. 管理组件

初始化完成后，可在面板首页启动、停止、重启各组件，或使用"全部启动"/"全部停止"/"全部重启"进行整体操作。

### 6. 管理配置

Panel 配置保留在 `config\runtime.ini`，Nginx/PHP/MySQL 配置位于各自组件目录，可在面板"设置"页面中编辑。修改配置后通常需要重启或重载对应组件才能生效。

## 目录结构

```
WNMP_RUNTIME/
├── WNMPanel.exe              # C 启动器，双击启动控制面板（唯一用户入口）
├── VERSION                    # 版本号唯一来源
├── build_launcher.bat         # 开发者编译脚本
├── launcher/                  # 启动器源码
│   ├── WNMPanel.c            # C 启动器源码
│   ├── WNMPanel.manifest     # UAC manifest
│   └── wnmp-panel.ico         # 应用图标
├── runtime/                   # Python 运行控制模块
│   ├── panel_server.py        # Panel HTTP 服务器
│   ├── wnmpctl.py             # CLI 控制器（开发排错用）
│   ├── panel/                 # Panel 模块（前端资产、路径管理、环境信息等）
│   └── *.py                   # 各组件控制模块（Nginx/PHP/MySQL/日志/状态等）
├── config/                    # Panel 配置目录
│   ├── runtime.ini            # 面板运行配置
│   ├── nginx/                 # Nginx 旧配置（迁移来源，保留不删）
│   ├── php/                   # PHP 旧配置（迁移来源，保留不删）
│   └── mysql/                 # MySQL 旧配置（迁移来源，保留不删）
├── bin/                       # 二进制文件与组件配置目录
│   ├── python/                # Python 运行时（必需）
│   │   └── python.exe
│   ├── nginx/                  # Nginx Web 服务器
│   │   ├── nginx.exe
│   │   └── conf/               # Nginx 活跃配置目录
│   │       ├── nginx.conf      # 主配置
│   │       ├── site.conf       # 默认站点配置
│   │       ├── vhosts/         # 新增站点目录
│   │       └── custom/         # 扩展配置目录
│   │           ├── http/       # http 级扩展
│   │           └── server/     # server 级扩展
│   ├── php/                    # PHP 运行时
│   │   ├── php.exe             # PHP CLI
│   │   ├── php-cgi.exe         # PHP-CGI FastCGI 进程
│   │   ├── php.ini             # PHP 活跃配置
│   │   └── php-cgi.ini         # PHP-CGI 进程配置
│   ├── mysql/                  # MySQL 数据库
│   │   ├── bin/
│   │   │   └── mysqld.exe      # MySQL 服务器
│   │   └── my.ini              # MySQL 活跃配置
│   └── openssl/                # OpenSSL 工具（可选）
│       └── openssl.exe
├── www/                       # 默认站点目录（初始化时生成）
├── data/mysql/                # MySQL 数据目录（初始化时生成）
├── logs/                      # 运行日志目录
│   ├── panel/                 # Panel 日志（panel_server.log）
│   └── runtime/               # 运行时日志（runtime.log）
└── scripts/                   # 辅助脚本（清理产物、版本同步等）
```
 
## 配置文件说明

### 面板配置：`config\runtime.ini`

面板运行行为由 `config\runtime.ini` 控制，主要配置项：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `PANEL_HOST` | 127.0.0.1 | Panel 监听地址，默认仅本机访问 |
| `PANEL_PORT` | 8787 | Panel 端口 |
| `AUTO_OPEN_BROWSER` | 1 | 启动后是否自动打开浏览器 |
| `PANEL_EXIT_ON_CLOSE` | 1 | 关闭所有面板页面后是否自动退出 Panel Server |
| `AUTO_START` | 0 | 是否启用开机自启动 |
| `WEB_ROOT` | ./www | 默认站点根目录 |
| `SERVICE_NAME` | WNMPRuntime | Windows 计划任务名称 |

以下端口配置仅作为**首次初始化模板的默认值**，初始化完成后以实际配置文件为准：

| 配置项 | 默认值 | 初始化后以哪个文件为准 |
|---|---|---|
| `HTTP_PORT` | 80 | `bin\nginx\conf\site.conf` 或 `bin\nginx\conf\nginx.conf` 的 `listen` 指令 |
| `HTTPS_PORT` | 443 | Nginx 配置中的 `ssl listen` |
| `ENABLE_HTTPS` | 1 | Nginx 配置中是否存在 `ssl listen` |
| `PHP_CGI_PORT` | 9000 | `bin\php\php-cgi.ini` |
| `MYSQL_PORT` | 3306 | `bin\mysql\my.ini` |

> 初始化完成后，修改 `runtime.ini` 中的端口值不会覆盖已生成的组件配置文件。请直接编辑对应的配置文件修改端口。

### Nginx 配置

| 路径 | 说明 |
|---|---|
| `bin\nginx\conf\nginx.conf` | Nginx 主配置文件 |
| `bin\nginx\conf\site.conf` | 默认站点配置 |
| `bin\nginx\conf\vhosts\` | 新增独立站点配置目录，文件内容通常是完整 `server { ... }` 配置块 |
| `bin\nginx\conf\custom\http\` | http 级扩展目录，适合 `upstream`、`map`、`gzip`、`log_format` 等 http 级配置片段 |
| `bin\nginx\conf\custom\server\` | 默认站点 server 级扩展目录，适合 `location`、`rewrite`、`add_header` 等 server 级配置片段 |

> `bin\nginx\conf\custom\server\` 用于对默认站点追加配置片段，**不是**新增独立站点的位置。新增独立站点请使用 `bin\nginx\conf\vhosts\`。

### PHP 配置

| 路径 | 说明 |
|---|---|
| `bin\php\php.ini` | PHP 运行配置 |
| `bin\php\php-cgi.ini` | PHP-CGI 进程配置（监听地址、端口等） |

### MySQL 配置

| 路径 | 说明 |
|---|---|
| `bin\mysql\my.ini` | MySQL 主配置文件 |

## 常用操作

### 启动 / 停止 / 重启组件

在面板首页点击对应组件的操作按钮即可。面板会实时显示当前运行状态。

### 查看版本

在面板首页点击组件的"查看版本"按钮，可查询 Nginx / PHP-CGI / MySQL 的实际版本号。

### 编辑配置

在面板"设置"页面可在线编辑配置文件。修改后点击保存，通常需要重启或重载对应组件使配置生效。

### 打开配置目录

在面板"设置"页面的"环境信息"模块中，可点击打开对应组件的配置目录，方便在文件管理器中管理配置文件。

### 查看运行日志

在面板"运行日志"页面可查看 Panel 运行日志和动作输出日志。

### 开机自启动

在面板"设置"页面可启用或关闭开机自启动。自启动基于 Windows 计划任务实现，任务名来自 `config\runtime.ini` 的 `SERVICE_NAME`。启用后不建议随意修改 `SERVICE_NAME`；如需修改，建议先关闭自启动，修改 `SERVICE_NAME` 后重新启用。

## 安全说明

- Panel Server 默认仅监听 `127.0.0.1`（`PANEL_HOST=127.0.0.1`），仅本机可访问
- **不建议**将 `PANEL_HOST` 修改为 `0.0.0.0`，不建议将 Panel 暴露到公网或不可信局域网
- 如确需远程访问，请自行评估网络隔离、访问控制和安全风险
- MySQL 首次初始化时生成随机 root 密码，请妥善保存

## 版本号管理

Panel 版本号由项目根目录 `VERSION` 文件统一维护，所有版本展示均从此文件读取：

- Panel 页面版本（左下角、关于页面）通过 `/api/panel-version` 从 `runtime\version.py` 读取 `VERSION` 文件
- `WNMPanel.exe` 文件属性版本由 `scripts\sync_version.py` 从 `VERSION` 生成

> Panel 版本号仅代表控制面板自身版本，**不是** Nginx / PHP / MySQL / Python 的组件版本。

## 开发者说明

### 开发排错入口

以下命令仅供开发排错使用，运行时路径由当前项目根目录动态计算：

```bash
# 使用项目内置 Python 启动 Panel Server
bin\python\python.exe runtime\panel_server.py

# CLI 控制
bin\python\python.exe runtime\wnmpctl.py start
bin\python\python.exe runtime\wnmpctl.py stop
bin\python\python.exe runtime\wnmpctl.py status
```

### 编译启动器

`WNMPanel.exe` 使用 C 语言编写，启动器源码位于 `launcher\` 目录。开发者可使用 MinGW-w64 或 LLVM 编译：

```bash
build_launcher.bat
```

该脚本会自动调用 `scripts\sync_version.py` 同步版本号，然后编译并输出 `WNMPanel.exe` 到项目根目录。

### 清理运行产物

打包发布前可清理运行期产生的临时文件：

```bash
python scripts\clean_runtime_artifacts.py --dry-run   # 预览
python scripts\clean_runtime_artifacts.py              # 实际清理
```

该脚本仅清理 `__pycache__`、日志文件、运行时状态文件等运行产物，不会删除用户二进制、配置文件、数据库数据。

## 常见问题

### 为什么提示系统版本不支持？

启动器检测到当前 Windows 版本低于最低要求（Windows 10 / Windows Server 2016），无法保证内置 Python 和组件正常运行。请升级操作系统。

### 修改 `runtime.ini` 中的端口后为什么不生效？

`runtime.ini` 中的端口仅作为首次初始化的默认值。初始化完成后，请直接编辑对应的组件配置文件（`bin\nginx\conf\site.conf`、`bin\php\php-cgi.ini`、`bin\mysql\my.ini`）修改端口。

### Nginx 新站点配置应该放哪里？

新增独立站点配置请放入 `bin\nginx\conf\vhosts\` 目录，文件内容应为完整的 `server { ... }` 配置块。如需对默认站点追加 location、rewrite 等配置片段，请放入 `bin\nginx\conf\custom\server\`。

### 为什么修改配置后需要重启或重载？

Nginx、PHP-CGI、MySQL 不会自动监测配置文件变更。修改配置后请在面板中执行重启或重载操作，使新配置生效。

### 开机自启动使用什么机制？

使用 Windows 计划任务（Scheduled Task），任务名来自 `runtime.ini` 的 `SERVICE_NAME`。任务工作目录为项目根目录。

### 为什么不建议开放到 0.0.0.0？

Panel Server 设计为本地管理工具，没有内置用户认证和访问控制。开放到公网可能导致未授权访问。

### 在哪里查看日志？

Panel Server 日志：`logs\panel\panel_server.log`；运行时日志：`logs\runtime\runtime.log`。面板"运行日志"页面也可直接查看。

## 相关链接

- **项目官网**：[https://dacat.cc/wnmp.html](https://dacat.cc/wnmp.html)
- **技术支持 / 作者主页**：[https://dacat.cc](https://dacat.cc)
## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
