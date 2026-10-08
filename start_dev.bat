@echo off
setlocal EnableExtensions

rem Windows 11 local acceptance launcher for the warehouse system.
cd /d "%~dp0"
set "PROJECT_ROOT=%~dp0"
set "BACKEND_DIR=%PROJECT_ROOT%backend"
set "VENV_DIR=%PROJECT_ROOT%.venv312"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "ALLOWED_HOSTS=127.0.0.1,localhost,0.0.0.0,*"
set "DEBUG=1"
set "CORS_ALLOW_ALL_ORIGINS=1"

title After-sales Warehouse - Local Dev Server

echo.
echo ================================================
echo   After-sales Warehouse - Local Dev Launcher
echo ================================================
echo.

if not exist "%BACKEND_DIR%\manage.py" (
    echo [ERROR] backend\manage.py was not found.
    echo         Please run this file from the project root.
    pause
    exit /b 1
)

netstat -ano | findstr ":8000" >nul
if not errorlevel 1 (
    echo [ERROR] Port 8000 is already in use.
    echo         Stop the old Django/Python server window with Ctrl+C first.
    echo         Do not continue until the old process is stopped.
    echo         The current project must be started by this file so that
    echo         SQLite and the LAN binding use the same environment.
    echo.
    netstat -ano | findstr LISTENING | findstr ":8000"
    pause
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo         Install Python 3.12 or newer and enable Add Python to PATH.
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo [1/5] Creating project virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

echo [2/5] Checking Python dependencies...
"%PYTHON_EXE%" -c "import django, rest_framework, corsheaders, PIL, dotenv, qrcode, openpyxl, pypinyin" >nul 2>nul
if errorlevel 1 (
    echo       Installing dependencies from backend\requirements.txt...
    "%PYTHON_EXE%" -m pip install -r "%BACKEND_DIR%\requirements.txt"
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

if not exist "%BACKEND_DIR%\.env" (
    echo [3/5] Creating backend\.env from the development template...
    copy /Y "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul
    if errorlevel 1 (
        echo [ERROR] Failed to create backend\.env.
        pause
        exit /b 1
    )
) else (
    echo [3/5] Reusing backend\.env...
)

echo [4/6] Applying database migrations...
pushd "%BACKEND_DIR%"
attrib -R "%BACKEND_DIR%\db.sqlite3" >nul 2>nul
"%PYTHON_EXE%" manage.py migrate --no-input
if errorlevel 1 (
    popd
    echo [ERROR] Database migration failed.
    pause
    exit /b 1
)

echo [5/6] Verifying database write access...
"%PYTHON_EXE%" manage.py shell -c "from django.db import connection; cursor = connection.cursor(); cursor.execute('BEGIN IMMEDIATE'); cursor.execute('ROLLBACK'); print('Database write preflight passed.')"
if errorlevel 1 (
    popd
    echo [ERROR] The configured database cannot be written.
    echo         Check the database file and backend directory permissions.
    pause
    exit /b 1
)

echo [6/6] Starting the development server...
echo.
echo Local URL:  http://127.0.0.1:8000/
echo Admin URL:  http://127.0.0.1:8000/admin/
echo API health: http://127.0.0.1:8000/health/
echo.
echo For another device on the same company Wi-Fi, use this PC's IPv4 address:
ipconfig | findstr /R /C:"IPv4 Address"
echo Example: http://YOUR-PC-IP:8000/
echo Press Ctrl+C in this window to stop the server.
echo.

start "Warehouse Browser" http://127.0.0.1:8000/
"%PYTHON_EXE%" manage.py runserver 0.0.0.0:8000
set "SERVER_EXIT=%ERRORLEVEL%"
popd

echo.
echo Server stopped with exit code %SERVER_EXIT%.
pause
exit /b %SERVER_EXIT%
