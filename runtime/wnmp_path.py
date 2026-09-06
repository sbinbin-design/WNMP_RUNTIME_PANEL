"""
WNMP Path Module - resolve relative/absolute paths to absolute paths
"""
import os


def resolve_path(root_dir, raw_path):
    """Resolve a path value from runtime.ini to an absolute path.

    Handles:
    - ./www, www -> root_dir + www
    - ./data/mysql -> root_dir + data/mysql
    - D:/xxx/www -> absolute path as-is
    - /xxx/www -> absolute path as-is
    """
    if not raw_path:
        return root_dir

    # Normalize backslashes to forward slashes for consistency
    normalized = raw_path.replace("\\", "/")

    # If already absolute (starts with drive letter or /)
    if os.path.isabs(normalized) or (len(normalized) >= 2 and normalized[1] == ":"):
        return os.path.normpath(raw_path)

    # Relative path: resolve against root_dir
    abs_path = os.path.normpath(os.path.join(root_dir, normalized))
    return abs_path


def to_forward_slash(path):
    """Convert path to forward slashes for Nginx config."""
    return path.replace("\\", "/")


def format_nginx_path(path):
    """统一格式化 Nginx 配置中的文件系统路径。

    职责：
    - 将 Windows 反斜杠路径转换为 Nginx 可接受的正斜杠形式
    - 用双引号包裹整个路径值，使含空格的绝对路径不会被 Nginx 配置解析器
      按空白拆分为多个参数（例如 D:/Program Files/WNMP/... 会被误解析）
    - 返回值可直接写入 Nginx 配置指令（include/pid/error_log/access_log/
      *_temp_path/root/ssl_certificate/ssl_certificate_key/fastcgi include 等）

    严禁对 Nginx 内部变量表达式（$document_root、$fastcgi_script_name、
    $host 等）调用本函数——本函数只处理由程序生成的本地文件系统绝对路径。

    修复背景：WNMP 安装到含空格目录（如 D:/Program Files/WNMP）时，
    未经引号包裹的路径会让 nginx -t 报错 invalid log level "Files/..." 等。
    """
    if path is None:
        return '""'
    # 统一转换为正斜杠
    posix_path = str(path).replace("\\", "/")
    # 用双引号包裹整个路径值；无论是否含空格都统一加引号，保证生成结果一致
    return '"' + posix_path + '"'


def is_default_web_root(root_dir, web_root):
    """Check if web_root resolves to the default www directory under root_dir."""
    resolved = resolve_path(root_dir, web_root)
    default_www = os.path.normpath(os.path.join(root_dir, "www"))
    return os.path.normpath(resolved) == default_www
