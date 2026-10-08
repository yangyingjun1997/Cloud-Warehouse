@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "BACKEND_DIR=%~dp0backend"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

title After-sales Warehouse - Create Admin

if not exist "%PYTHON_EXE%" (
    echo Please run start_dev.bat first.
    pause
    exit /b 1
)

if not exist "%BACKEND_DIR%\manage.py" (
    echo backend\manage.py was not found.
    pause
    exit /b 1
)

pushd "%BACKEND_DIR%"
"%PYTHON_EXE%" manage.py createsuperuser
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
if "%EXIT_CODE%"=="0" echo Admin user created successfully.
if not "%EXIT_CODE%"=="0" echo Admin user creation did not complete.
pause
exit /b %EXIT_CODE%
