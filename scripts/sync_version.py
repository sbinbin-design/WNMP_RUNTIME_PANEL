# -*- coding: utf-8 -*-
"""
sync_version.py - 版本号同步脚本

从项目根目录 VERSION 文件读取版本号，自动生成：
  - launcher/WNMPanel.rc（资源文件）
  - launcher/WNMPanel.manifest（UAC manifest）

用法：
    python scripts/sync_version.py

功能：
    1. 读取项目根目录 VERSION 文件（如 0.1.0-dev）
    2. 解析前三段数字用于 Windows FILEVERSION/PRODUCTVERSION（如 0,1,0,0）
    3. 保留完整 VERSION 字符串用于 FileVersion/ProductVersion 字符串
    4. 从 launcher/WNMPanel.rc.template 读取模板，替换占位符后写入 launcher/WNMPanel.rc
    5. 从 launcher/WNMPanel.manifest.template 读取模板，替换占位符后写入 launcher/WNMPanel.manifest

VERSION 格式支持：
    - 0.1.0
    - 0.1.0-dev
    - 0.2.0-beta.1
    前三段必须为数字，否则报错停止。

不引入第三方依赖，仅使用 Python 标准库。
"""
import os
import re
import sys


def read_version(version_file):
    """读取 VERSION 文件内容，去除首尾空白。"""
    if not os.path.isfile(version_file):
        print("[ERROR] VERSION 文件不存在: {}".format(version_file), file=sys.stderr)
        sys.exit(1)
    with open(version_file, "r", encoding="utf-8") as f:
        ver = f.read().strip()
    if not ver:
        print("[ERROR] VERSION 文件内容为空: {}".format(version_file), file=sys.stderr)
        sys.exit(1)
    return ver


def parse_version(version_str):
    """
    解析 VERSION 字符串，提取前三段数字。
    返回 (major, minor, patch) 整数元组。
    解析失败则报错退出。
    """
    # 取前三段数字部分（支持 0.1.0-dev、0.2.0-beta.1 等）
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)', version_str)
    if not m:
        print(
            "[ERROR] VERSION '{}' 无法解析出主版本、次版本、修订版本，"
            "需要至少三段数字（如 0.1.0 或 0.1.0-dev）".format(version_str),
            file=sys.stderr
        )
        sys.exit(1)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def version_comma(major, minor, patch):
    """生成 Windows FILEVERSION/PRODUCTVERSION 四段格式：major,minor,patch,0"""
    return "{},{},{},0".format(major, minor, patch)


def version_dot(major, minor, patch):
    """生成 Windows manifest 四段点分格式：major.minor.patch.0"""
    return "{}.{}.{}.0".format(major, minor, patch)


def generate_rc(template_path, output_path, version_str, file_version_comma):
    """从模板生成 WNMPanel.rc，替换版本相关占位符。"""
    if not os.path.isfile(template_path):
        print("[ERROR] 模板文件不存在: {}".format(template_path), file=sys.stderr)
        sys.exit(1)

    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 替换占位符
    content = content.replace("{{FILE_VERSION_COMMA}}", file_version_comma)
    content = content.replace("{{PRODUCT_VERSION_COMMA}}", file_version_comma)
    content = content.replace("{{VERSION_STRING}}", version_str)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("[OK] 已生成: {}".format(output_path))


def generate_manifest(template_path, output_path, manifest_version):
    """从模板生成 WNMPanel.manifest，替换版本占位符。"""
    if not os.path.isfile(template_path):
        print("[ERROR] 模板文件不存在: {}".format(template_path), file=sys.stderr)
        sys.exit(1)

    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    content = content.replace("{{MANIFEST_VERSION}}", manifest_version)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("[OK] 已生成: {}".format(output_path))


def main():
    # 项目根目录：scripts/ 的上级
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.normpath(os.path.join(script_dir, ".."))

    version_file = os.path.join(project_root, "VERSION")
    rc_template_path = os.path.join(project_root, "launcher", "WNMPanel.rc.template")
    rc_output_path = os.path.join(project_root, "launcher", "WNMPanel.rc")
    manifest_template_path = os.path.join(project_root, "launcher", "WNMPanel.manifest.template")
    manifest_output_path = os.path.join(project_root, "launcher", "WNMPanel.manifest")

    # 1. 读取 VERSION
    version_str = read_version(version_file)
    print("[INFO] VERSION = {}".format(version_str))

    # 2. 解析版本号
    major, minor, patch = parse_version(version_str)
    fv_comma = version_comma(major, minor, patch)
    mv_dot = version_dot(major, minor, patch)
    print("[INFO] FILEVERSION / PRODUCTVERSION = {}".format(fv_comma))
    print("[INFO] ManifestVersion = {}".format(mv_dot))
    print("[INFO] VersionString = {}".format(version_str))

    # 3. 生成 WNMPanel.rc
    generate_rc(rc_template_path, rc_output_path, version_str, fv_comma)

    # 4. 生成 WNMPanel.manifest
    generate_manifest(manifest_template_path, manifest_output_path, mv_dot)

    print("[OK] 版本同步完成。")


if __name__ == "__main__":
    main()
