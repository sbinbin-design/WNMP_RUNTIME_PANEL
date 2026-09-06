## 中文版
You can view the Chinese version of this README [here](README.md).
# WNMP Runtime Panel

A local runtime control panel for Windows-based Nginx / PHP-CGI / MySQL environments.

## Project Overview

WNMP Runtime Panel is used to manage a local WNMP runtime environment, which consists of Windows + Nginx + PHP-CGI + MySQL. It provides initialization, start/stop control, status monitoring, configuration editing, log viewing, environment information, and startup-on-boot management.

All operations are performed through a local Web control panel. It does not rely on the system Python installation and does not require command-line experience.

## Features

- **Local Web Control Panel**: Double-click `WNMPanel.exe` to automatically start the Panel Server and open the browser. No manual configuration is required.
- **Independent Component Management**: Nginx, PHP-CGI, and MySQL can be started, stopped, restarted, and monitored independently.
- **One-Click Initialization**: On first use, click "Initialize Environment" to automatically generate default configuration files, the default site, local development certificates, and the MySQL data directory.
- **Component Configuration Co-location**: Panel config remains in `config\runtime.ini`, while Nginx/PHP/MySQL configs are located in their respective component directories, editable in the panel.
- **Nginx Site Extensions**: `bin\nginx\conf\vhosts\` is used for adding independent site configurations. `bin\nginx\conf\custom\` provides HTTP-level and server-level extension directories.
- **Runtime Log Viewing**: Panel runtime logs and action output logs can be viewed directly in the panel.
- **Startup on Boot**: Implemented through Windows Task Scheduler. The task name comes from `SERVICE_NAME` in `runtime.ini`.
- **Unified Panel Version Management**: The panel version is maintained in the root `VERSION` file.
- **Chinese / English Interface**: The panel supports switching between Chinese and English.

## Supported Environments

| | Supported | Not Supported |
|---|---|---|
| **Client** | Windows 10, Windows 11 | Windows 7, Windows 8 / 8.1 |
| **Server** | Windows Server 2016 / 2019 / 2022 / 2025 | Windows Server 2008 / 2008 R2, Windows Server 2012 / 2012 R2 |

The launcher checks the Windows version before startup. Systems below the baseline version, NT 10.0, which corresponds to Windows 10 / Windows Server 2016, will be blocked with a prompt to upgrade the operating system.

## Quick Start

### 1. Download the Project

Download or clone the project to any local directory. Paths containing spaces or non-English characters are supported.

### 2. Prepare Binary Files

The open-source source package usually does not include third-party Nginx / PHP / MySQL binaries. Please download the corresponding Windows versions yourself and place them into the required directories.

If a complete runtime package is provided, follow the instructions included with the release package.

Required binary files:

- **Python, required**: `bin\python\python.exe`
- **Nginx**: `bin\nginx\nginx.exe`
- **PHP**: `bin\php\php-cgi.exe`, `bin\php\php.exe`
- **MySQL**: `bin\mysql\bin\mysqld.exe`
- **OpenSSL, optional**: `bin\openssl\openssl.exe`

### 3. Start the Panel

Double-click `WNMPanel.exe` in the project root directory. The Panel Server will start automatically and open the browser.

The launcher will:

- Use the directory where it is located as the project root
- Read `PANEL_HOST` and `PANEL_PORT` from `config\runtime.ini`
- Check whether the embedded Python runtime exists
- If the Panel is already running, open the browser directly without starting another server process

### 4. Initialize the Environment

On first launch, the page will display "Environment Not Initialized". Click **"Initialize Environment"** to automatically complete the following tasks:

- Generate default Nginx, PHP-CGI, and MySQL configuration files
- Create the default site and local development certificate
- Initialize the MySQL root password and create the data directory
- Automatically start all core components after initialization

### 5. Manage Components

After initialization, you can start, stop, and restart each component from the panel home page. You can also use "Start All", "Stop All", and "Restart All" for global operations.

### 6. Manage Configuration Files

Panel config remains in `config\runtime.ini`, while Nginx/PHP/MySQL configs are located in their respective component directories. They can be edited on the panel "Settings" page.

After modifying configuration files, you usually need to restart or reload the corresponding component for the changes to take effect.

## Directory Structure

```text
WNMP_RUNTIME/
├── WNMPanel.exe              # C launcher, the only user-facing entry point
├── VERSION                    # Single source of truth for the panel version
├── build_launcher.bat         # Developer build script
├── launcher/                  # Launcher source code
│   ├── WNMPanel.c            # C launcher source
│   ├── WNMPanel.manifest     # UAC manifest
│   └── wnmp-panel.ico         # Application icon
├── runtime/                   # Python runtime control modules
│   ├── panel_server.py        # Panel HTTP server
│   ├── wnmpctl.py             # CLI controller, mainly for development and troubleshooting
│   ├── panel/                 # Panel modules, frontend assets, path management, environment information
│   └── *.py                   # Component control modules for Nginx, PHP, MySQL, logs, status, etc.
├── config/                    # Panel configuration directory
│   ├── runtime.ini            # Panel runtime configuration
│   ├── nginx/                 # Nginx legacy config (migration source, preserved)
│   ├── php/                   # PHP legacy config (migration source, preserved)
│   └── mysql/                 # MySQL legacy config (migration source, preserved)
├── bin/                       # Binary files and component config directory
│   ├── python/                # Python runtime, required
│   │   └── python.exe
│   ├── nginx/                 # Nginx Web server
│   │   ├── nginx.exe
│   │   └── conf/              # Nginx active config directory
│   │       ├── nginx.conf     # Main config
│   │       ├── site.conf      # Default site config
│   │       ├── vhosts/        # Virtual hosts directory
│   │       └── custom/        # Extension config directory
│   │           ├── http/      # HTTP-level extensions
│   │           └── server/    # Server-level extensions
│   ├── php/                   # PHP runtime
│   │   ├── php.exe            # PHP CLI
│   │   ├── php-cgi.exe        # PHP-CGI FastCGI process
│   │   ├── php.ini            # PHP active config
│   │   └── php-cgi.ini        # PHP-CGI process config
│   ├── mysql/                 # MySQL database
│   │   ├── bin/
│   │   │   └── mysqld.exe     # MySQL server
│   │   └── my.ini             # MySQL active config
│   └── openssl/               # OpenSSL tool, optional
│       └── openssl.exe
├── www/                       # Default site directory, generated during initialization
├── data/mysql/                # MySQL data directory, generated during initialization
├── logs/                      # Runtime logs
│   ├── panel/                 # Panel logs, such as panel_server.log
│   └── runtime/               # Runtime logs, such as runtime.log
└── scripts/                   # Helper scripts, cleanup, version sync, etc.
```

## Configuration Files

### Panel Configuration: `config\runtime.ini`

Panel runtime behavior is controlled by `config\runtime.ini`.

| Option | Default | Description |
|---|---|---|
| `PANEL_HOST` | 127.0.0.1 | Panel listening address. Local access only by default. |
| `PANEL_PORT` | 8787 | Panel port |
| `AUTO_OPEN_BROWSER` | 1 | Whether to automatically open the browser after startup |
| `PANEL_EXIT_ON_CLOSE` | 1 | Whether the Panel Server exits automatically after all panel pages are closed |
| `AUTO_START` | 0 | Whether startup on boot is enabled |
| `WEB_ROOT` | ./www | Default site root directory |
| `SERVICE_NAME` | WNMPRuntime | Windows Task Scheduler task name |

The following port settings are only used as default values for the first initialization template. After initialization, the actual component configuration files take precedence.

| Option | Default | Effective File After Initialization |
|---|---|---|
| `HTTP_PORT` | 80 | `bin\nginx\conf\site.conf` or `bin\nginx\conf\nginx.conf` `listen` directive |
| `HTTPS_PORT` | 443 | SSL `listen` directive in the Nginx configuration |
| `ENABLE_HTTPS` | 1 | Whether SSL `listen` exists in the Nginx configuration |
| `PHP_CGI_PORT` | 9000 | `bin\php\php-cgi.ini` |
| `MYSQL_PORT` | 3306 | `bin\mysql\my.ini` |

> After initialization, changing port values in `runtime.ini` will not overwrite the generated component configuration files. Please edit the corresponding configuration files directly.

### Nginx Configuration

| Path | Description |
|---|---|
| `bin\nginx\conf\nginx.conf` | Main Nginx configuration file |
| `bin\nginx\conf\site.conf` | Default site configuration |
| `bin\nginx\conf\vhosts\` | Directory for adding independent site configurations. Files usually contain complete `server { ... }` blocks. |
| `bin\nginx\conf\custom\http\` | HTTP-level extension directory, suitable for `upstream`, `map`, `gzip`, `log_format`, and other HTTP-level snippets |
| `bin\nginx\conf\custom\server\` | Default site server-level extension directory, suitable for `location`, `rewrite`, `add_header`, and other server-level snippets |

> `bin\nginx\conf\custom\server\` is used to append configuration snippets to the default site. It is **not** the place for adding independent sites. Use `bin\nginx\conf\vhosts\` for new independent sites.

### PHP Configuration

| Path | Description |
|---|---|
| `bin\php\php.ini` | PHP runtime configuration |
| `bin\php\php-cgi.ini` | PHP-CGI process configuration, including listening address and port |

### MySQL Configuration

| Path | Description |
|---|---|
| `bin\mysql\my.ini` | Main MySQL configuration file |

## Common Operations

### Start / Stop / Restart Components

Click the corresponding operation buttons on the panel home page. The panel displays the current running status in real time.

### View Component Versions

Click the "Check Version" button on the component card to query the actual versions of Nginx, PHP-CGI, and MySQL.

### Edit Configuration

Configuration files can be edited online on the panel "Settings" page. After saving changes, you usually need to restart or reload the corresponding component for the changes to take effect.

### Open Configuration Directory

In the "Environment Information" module on the "Settings" page, you can open the configuration directory of each component in File Explorer.

### View Runtime Logs

The "Runtime Logs" page displays Panel runtime logs and action output logs.

### Startup on Boot

Startup on boot can be enabled or disabled on the "Settings" page. It is implemented through Windows Task Scheduler, and the task name comes from `SERVICE_NAME` in `config\runtime.ini`.

After enabling startup on boot, it is not recommended to modify `SERVICE_NAME` casually. If you need to change it, disable startup on boot first, update `SERVICE_NAME`, and then enable startup on boot again.

## Security Notes

- The Panel Server listens on `127.0.0.1` by default, using `PANEL_HOST=127.0.0.1`, and is accessible only from the local machine.
- It is **not recommended** to change `PANEL_HOST` to `0.0.0.0`.
- It is **not recommended** to expose the Panel to the public Internet or an untrusted LAN.
- If remote access is required, evaluate network isolation, access control, and security risks yourself.
- A random MySQL root password is generated during the first initialization. Please save it securely.

## Version Management

The Panel version is maintained by the root `VERSION` file. All version displays are read from this file.

- The panel page version, including the footer and About page, is read through `/api/panel-version` from `runtime\version.py`
- The `WNMPanel.exe` file property version is generated from `VERSION` by `scripts\sync_version.py`

> The Panel version only represents the control panel itself. It is **not** the version of Nginx, PHP, MySQL, or Python.

## Developer Notes

### Development and Troubleshooting Entry Points

The following commands are for development and troubleshooting only. Runtime paths are calculated dynamically from the current project root.

```bash
# Start Panel Server using the embedded Python runtime
bin\python\python.exe runtime\panel_server.py

# CLI control
bin\python\python.exe runtime\wnmpctl.py start
bin\python\python.exe runtime\wnmpctl.py stop
bin\python\python.exe runtime\wnmpctl.py status
```

### Build the Launcher

`WNMPanel.exe` is written in C. The launcher source code is located in the `launcher\` directory. Developers can compile it with MinGW-w64 or LLVM.

```bash
build_launcher.bat
```

This script automatically calls `scripts\sync_version.py` to synchronize the version number, then compiles and outputs `WNMPanel.exe` to the project root.

### Clean Runtime Artifacts

Before packaging a release, you can clean temporary files generated during runtime.

```bash
python scripts\clean_runtime_artifacts.py --dry-run   # Preview
python scripts\clean_runtime_artifacts.py             # Clean
```

This script only cleans runtime artifacts such as `__pycache__`, log files, and runtime state files. It does not delete user binaries, configuration files, or database data.

## FAQ

### Why does it say the system version is not supported?

The launcher detected that the current Windows version is below the minimum requirement, Windows 10 / Windows Server 2016. The embedded Python runtime and components cannot be guaranteed to work properly on older systems. Please upgrade the operating system.

### Why do port changes in `runtime.ini` not take effect?

Ports in `runtime.ini` are only used as default values for the first initialization. After initialization, edit the corresponding component configuration files directly, such as `bin\nginx\conf\site.conf`, `bin\php\php-cgi.ini`, or `bin\mysql\my.ini`.

### Where should new Nginx site configurations be placed?

Place new independent site configurations in `bin\nginx\conf\vhosts\`. Each file should contain a complete `server { ... }` block.

If you only need to append `location`, `rewrite`, or similar snippets to the default site, place them in `bin\nginx\conf\custom\server\`.

### Why do I need to restart or reload after modifying configuration files?

Nginx, PHP-CGI, and MySQL do not automatically detect configuration file changes. After modifying configuration files, restart or reload the corresponding component in the panel to apply the new configuration.

### What mechanism is used for startup on boot?

Windows Task Scheduler is used. The task name comes from `SERVICE_NAME` in `runtime.ini`, and the task working directory is the project root.

### Why is opening the Panel on `0.0.0.0` not recommended?

The Panel Server is designed as a local management tool and does not include built-in user authentication or access control. Exposing it to a public network may cause unauthorized access.

### Where can I view logs?

Panel Server log:

```text
logs\panel\panel_server.log
```

Runtime log:

```text
logs\runtime\runtime.log
```

The logs can also be viewed directly from the "Runtime Logs" page in the panel.

## Links

- **Project Website**: [https://dacat.cc/wnmp.html](https://dacat.cc/wnmp.html)
- **Technical Support / Author Homepage**: [https://dacat.cc](https://dacat.cc)

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
