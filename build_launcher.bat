@echo off
REM build_launcher.bat - WNMPanel.exe 编译脚本（仅供开发者使用）
REM 编译输出：根目录 WNMPanel.exe（唯一用户入口）
REM 编译后自动清理 launcher/WNMPanel.res 临时文件
REM 打包前自检：launcher/ 目录不得保留 WNMPanel.exe

setlocal enabledelayedexpansion

echo ============================================
echo  WNMPanel.exe Build Script
echo ============================================

REM 切换到项目根目录
cd /d "%~dp0"

REM 检查编译器
where gcc >nul 2>&1
if %ERRORLEVEL%==0 (
    set CC=gcc
    set RC=windres
    echo [OK] 使用 gcc 编译器
) else (
    where clang >nul 2>&1
    if %ERRORLEVEL%==0 (
        set CC=clang
        set RC=windres
        echo [OK] 使用 clang 编译器
    ) else (
        echo [ERROR] 未找到 gcc 或 clang，请安装 MinGW-w64 或 LLVM
        exit /b 1
    )
)

REM 步骤 0: 同步版本号（从 VERSION 生成 WNMPanel.rc）
echo.
echo [0/3] 同步版本号 (VERSION -^> WNMPanel.rc) ...
bin\python\python.exe scripts\sync_version.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] 版本同步失败，请检查 VERSION 文件格式
    exit /b 1
)
echo [OK] 版本同步完成

REM 步骤 1: 编译资源文件
echo.
echo [1/3] 编译资源文件 (WNMPanel.rc -^> WNMPanel.res) ...
%RC% launcher/WNMPanel.rc -O coff -o launcher/WNMPanel.res
if %ERRORLEVEL% neq 0 (
    echo [ERROR] 资源编译失败
    exit /b 1
)
echo [OK] launcher/WNMPanel.res 已生成

REM 步骤 2: 编译并链接，输出到根目录
echo.
echo [2/3] 编译并链接 (WNMPanel.exe) ...
%CC% -O2 -municode -mwindows ^
    launcher/WNMPanel.c launcher/WNMPanel.res ^
    -lshlwapi -lwinhttp -lshell32 -luser32 -lws2_32 ^
    -o WNMPanel.exe
if %ERRORLEVEL% neq 0 (
    echo [ERROR] 编译链接失败
    del /f launcher\WNMPanel.res 2>nul
    exit /b 1
)
echo [OK] WNMPanel.exe 已生成

REM 步骤 3: 清理临时文件
echo.
echo [3/3] 清理临时文件 ...
del /f launcher\WNMPanel.res 2>nul
echo [OK] launcher/WNMPanel.res 已清理

REM 步骤 4: 自检 - launcher 目录不得保留 WNMPanel.exe
echo.
echo [自检] 检查 launcher/ 目录 ...
if exist launcher\WNMPanel.exe (
    echo [WARN] launcher/WNMPanel.exe 仍然存在，删除以避免入口不一致
    del /f launcher\WNMPanel.exe
    if exist launcher\WNMPanel.exe (
        echo [ERROR] 无法删除 launcher/WNMPanel.exe，请手动删除
        exit /b 1
    )
)
echo [OK] launcher/ 目录无 WNMPanel.exe

REM 步骤 5: 输出编译结果
echo.
echo ============================================
echo  编译完成！
echo  输出: %CD%\WNMPanel.exe
echo ============================================

REM 输出文件大小
for %%A in (WNMPanel.exe) do echo  大小: %%~zA 字节

REM 输出 SHA256
echo  SHA256:
certutil -hashfile WNMPanel.exe SHA256 | findstr /v ":" | findstr /r "^[0-9a-f]"
echo.
echo  普通用户入口：根目录 WNMPanel.exe
echo  manifest: highestAvailable (管理员 UAC 提权)
echo ============================================

endlocal
