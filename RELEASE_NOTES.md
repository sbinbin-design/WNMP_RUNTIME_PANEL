# Release Notes

## v0.2.1-dev

- 修复 Nginx 在含空格安装路径下生成配置失败的问题。
- 优化 Nginx 配置模板与路径处理逻辑。
- 收口默认站点文件的初始化与启动维护行为。
- 修复默认站点被删除或替换后仍可能重新生成运行配置的问题。
- 完善版本号同步与发布资源管理。
- 修复若干初始化与配置生成相关问题，提升运行稳定性。

## v0.2.0-dev

## 组件配置路径归位

本版本核心更新：Nginx / PHP / MySQL 活跃配置文件正式归位到各自组件目录，Panel 配置仍保留在 config/runtime.ini。

### 新配置路径

- **Nginx**: `bin/nginx/conf/`（nginx.conf、site.conf、vhosts/、custom/http/、custom/server/）
- **PHP**: `bin/php/`（php.ini、php-cgi.ini、php.user.ini）
- **MySQL**: `bin/mysql/my.ini`（my.user.ini 同理）
- **Panel**: `config/runtime.ini`（保持不变）

### 迁移策略（非破坏式）

- 旧 `config/nginx/`、`config/php/`、`config/mysql/` 下文件**保留不删除**，仅作为迁移来源
- 启动时自动检测并复制缺失的配置到新路径，旧文件不会被覆盖或删除
- vhosts/custom 迁移：**只补缺失，不覆盖已有文件**
- mime.types / fastcgi_params：新路径已存在时不覆盖
- 组件目录已有原始配置时：**先备份再接管**，备份保存在 `config/backups/original/<component>/`

### 数据安全

- **不移动** `data/mysql/` 目录
- **不移动** `logs/` 目录
- **不删除** 旧 `config/nginx`、`config/php`、`config/mysql` 目录
- MySQL 数据目录和日志目录保持原有位置

### 升级兼容性

- 已有 v0.1.1-dev 环境直接启动时，会在启动前**自动保障新路径配置存在**
- 避免 `bin/mysql/my.ini` 缺失导致 MySQL 启动失败
- 无需手动迁移，Panel 自动处理

---

## v0.1.1-dev 修复内容（历史版本）

### IE11 兼容修复

- 修复 Windows Server 2016 IE11 下环境信息区域布局重叠问题：改为单列纵向布局，卡片不卡死、不重叠、文字可读、按钮可点击
- 修复 IE11 下 MySQL 初始密码弹窗不可见/不居中问题：使用 absolute + transform 居中方案替代 flex 居中
- 修复 IE11 下右下角 Toast 提示块宽度塌陷和中文逐字换行问题：设置固定安全宽度，确保横向排版稳定
- 修复 IE11 下环境信息路径文本在 125%/150% 缩放时挤压布局问题：允许路径自然换行

### 功能修复

- 修复初始化按钮无响应问题
- 修复中英文切换后部分文案未更新问题

### 文案优化

- MySQL 初始密码弹窗文案优化：明确说明密码仅在本弹窗中自动显示一次，关闭后面板不会再次显示

### 版本号统一管理

- 版本号统一由根目录 VERSION 文件管理，运行时优先读取 VERSION 文件，version.py 仅提供兜底值
- WNMPPanel.exe 内嵌 manifest 版本号由 sync_version.py 从 VERSION 自动生成，确保一致性
- 不在前端文件中写死版本号
