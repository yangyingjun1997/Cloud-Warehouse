@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "DB_FILE=%~dp0backend\db.sqlite3"
set "TEMP_DB=%~dp0backend\db.sqlite3.repaired"
set "PYTHON_EXE=%~dp0.venv312\Scripts\python.exe"

echo.
echo ================================================
echo   SQLite read-only repair tool
echo ================================================
echo.

if not exist "%DB_FILE%" (
    echo [ERROR] backend\db.sqlite3 was not found.
    pause
    exit /b 1
)
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Project Python environment was not found.
    pause
    exit /b 1
)
netstat -ano | findstr LISTENING | findstr ":8000" >nul
if not errorlevel 1 (
    echo [ERROR] Port 8000 is still in use. Stop the local server first.
    pause
    exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%i"
set "BACKUP_FILE=%~dp0backend\db.sqlite3.before-repair-!STAMP!"

echo This operation keeps a full backup and rebuilds only the file permissions.
set /p "CONFIRM=Type REPAIR to continue: "
if /I not "!CONFIRM!"=="REPAIR" (
    echo Canceled.
    pause
    exit /b 0
)

copy /Y "%DB_FILE%" "!BACKUP_FILE!" >nul || goto :failed
copy /Y "%DB_FILE%" "%TEMP_DB%" >nul || goto :failed
set "DB_NAME=db.sqlite3.repaired"
pushd "%~dp0backend"
"%PYTHON_EXE%" manage.py migrate --noinput || (popd & goto :failed)
popd
move /Y "%TEMP_DB%" "%DB_FILE%" >nul || goto :failed

echo [OK] Database permissions were rebuilt and migrations completed.
echo Backup: !BACKUP_FILE!
pause
exit /b 0

:failed
echo [ERROR] Repair failed. The original database was not deleted.
if exist "%TEMP_DB%" del /Q "%TEMP_DB%"
pause
exit /b 1
