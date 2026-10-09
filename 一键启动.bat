@echo off
setlocal EnableExtensions
chcp 65001 >nul
rem Keep Python and child consoles on UTF-8 even when Windows starts a fresh console.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONLEGACYWINDOWSSTDIO=0"

set "ROOT=%~dp0"
set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"
set "WEB_DIR=%ROOT%web"
set "WEB_MODULES=%WEB_DIR%\node_modules"

pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cannot enter project directory: %ROOT%
    pause
    exit /b 1
)

echo.
echo ========================================
echo   MoldPilot Local Development Launcher
echo ========================================
echo.

if not exist "%ROOT%.env" (
    echo [ERROR] Missing .env.
    echo Copy .env.example to .env and configure database, Redis, worker and model settings.
    goto :failed
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Missing Python virtual environment: .venv\Scripts\python.exe
    echo Run: py -3.12 -m venv .venv
    echo Then run: .\.venv\Scripts\python.exe -m pip install -r requirements.lock
    goto :failed
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm.cmd was not found. Install Node.js and add npm to PATH.
    goto :failed
)

if not exist "%WEB_MODULES%" (
    echo [ERROR] Missing frontend dependencies: web\node_modules
    echo Run npm ci in the web directory first.
    goto :failed
)

set "PYTHONPATH=%ROOT%backend"

echo [CHECK] Validating backend imports...
"%PYTHON_EXE%" -c "import app.api; import app.agent_worker; import app.message_worker; import app.document_worker"
if errorlevel 1 (
    echo [ERROR] Backend import validation failed. Review the Python traceback above.
    goto :failed
)

echo [CHECK] Verifying local PostgreSQL and schema read-only...
"%PYTHON_EXE%" "%ROOT%scripts\check_runtime.py"
if errorlevel 1 (
    echo [ERROR] Read-only startup preflight failed. No service was launched.
    goto :failed
)

if /I "%~1"=="--check" (
    echo [OK] Found .env, Python virtual environment, npm and frontend dependencies.
    echo [OK] API, Agent Worker, Message Worker and Document Worker imports are valid.
    echo [OK] PostgreSQL schema and required columns were verified read-only; no migrations executed.
    echo [INFO] Redis and model connectivity are checked by the running services.
    popd
    exit /b 0
)

set "PORT_BUSY="
for %%P in (8001 5173) do (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c=Get-NetTCPConnection -State Listen -LocalPort %%P -ErrorAction SilentlyContinue; if($c){exit 1}else{exit 0}"
    if errorlevel 1 set "PORT_BUSY=%%P"
)
if defined PORT_BUSY (
    echo [ERROR] Port %PORT_BUSY% is already in use. Stop the existing MoldPilot service before starting another one.
    goto :failed
)

echo [1/5] Starting FastAPI, Agent Worker, Message Worker and optional Document Worker
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\start_services.ps1" -Root "%ROOT:~0,-1%" -IncludeDocument
if errorlevel 1 goto :launch_failed

echo [4/4] Starting Vue development server: http://127.0.0.1:5173

echo.
echo [OK] Core API, Agent Worker, Message Worker and Web were launched as hidden detached processes.
echo Document Worker starts only when AGENT_OCR_SERVICE_TOKEN is configured in .env.
echo Check .local\logs if a service exits or a dependency is unavailable.
echo The browser will open shortly. The first frontend build may take a moment.
powershell.exe -NoLogo -NoProfile -Command "Start-Sleep -Seconds 3"
echo [INFO] Open http://127.0.0.1:5173/ in the browser.
start "MoldPilot Web" "http://127.0.0.1:5173/"

popd
exit /b 0

:launch_failed
echo.
echo [ERROR] Failed to launch a service window. Check the messages above.

:failed
echo.
pause
popd
exit /b 1
