@echo off
rem SQLite 数据库备份（Windows 开发机用）。
rem 用 sqlite3 .backup 在线备份，不会锁库；保留最近 30 份。
rem 生产服务器（PostgreSQL）请用 deploy/ubuntu22/backup.sh + systemd timer。

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "DB=backend\db.sqlite3"
set "BACKUP_DIR=backups"
set "SQLITE=%~dp0.venv312\Scripts\sqlite3.exe"
set "STAMP=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "STAMP=%STAMP: =0%"
set "TARGET=%BACKUP_DIR%\db-%STAMP%.sqlite3"

if not exist "%DB%" (
    echo [ERROR] 数据库不存在：%DB%
    exit /b 1
)
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

rem 优先用 sqlite3 .backup（在线一致性备份），否则退回文件复制
where sqlite3 >nul 2>nul
if %errorlevel% equ 0 (
    sqlite3 "%DB%" ".backup '%TARGET%'"
) else if exist "%SQLITE%" (
    "%SQLITE%" "%DB%" ".backup '%TARGET%'"
) else (
    echo [INFO] 未找到 sqlite3 命令，退化为文件复制（请先停止服务以保证一致性）
    copy /Y "%DB%" "%TARGET%" >nul
)

if exist "%TARGET%" (
    echo [OK] 备份完成：%TARGET%
) else (
    echo [ERROR] 备份失败
    exit /b 1
)

rem 清理 30 天前的备份
forfiles /P "%BACKUP_DIR%" /M db-*.sqlite3 /D -30 /C "cmd /c del @path" 2>nul
exit /b 0
