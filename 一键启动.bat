@echo off
setlocal EnableExtensions
chcp 65001 >nul

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
"%PYTHON_EXE%" -c "import app.api; import app.agent_worker; import app.document_worker; import app.message_worker"
if errorlevel 1 (
    echo [ERROR] Backend import validation failed. Review the Python traceback above.
    goto :failed
)

echo [CHECK] Checking local PostgreSQL read-only ^(20 second limit^)...
"%PYTHON_EXE%" "%ROOT%scripts\check_runtime.py"
if errorlevel 1 (
    echo [ERROR] Database preflight failed. No API or worker was started.
    echo Check Windows service moldpilot-postgresql-55432 and the configured database port.
    echo This launcher never applies database migrations.
    goto :failed
)

if /I "%~1"=="--check" (
    echo [OK] Found .env, Python virtual environment, npm and frontend dependencies.
    echo [OK] API, Agent Worker, Document Worker and Message Worker imports are valid.
    echo [OK] PostgreSQL connection, schema versions and required columns passed read-only checks.
    echo [OK] No database migrations were executed.
    echo [INFO] Redis and model connectivity is checked by the running services.
    popd
    exit /b 0
)

echo [1/5] Starting FastAPI: http://127.0.0.1:8000
start "MoldPilot API" /D "%ROOT%" cmd.exe /k ""%PYTHON_EXE%" -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log"
if errorlevel 1 goto :launch_failed

echo [2/5] Starting Agent Worker
start "MoldPilot Agent Worker" /D "%ROOT%" cmd.exe /k ""%PYTHON_EXE%" -m app.agent_worker"
if errorlevel 1 goto :launch_failed

echo [3/5] Starting Document Worker ^(PDF text extraction, PaddleOCR and configured document model^)
start "MoldPilot Document Worker" /D "%ROOT%" cmd.exe /k ""%PYTHON_EXE%" -m app.document_worker"
if errorlevel 1 goto :launch_failed

echo [4/5] Starting Message Worker ^(Redis delivery and workflow timers^)
start "MoldPilot Message Worker" /D "%ROOT%" cmd.exe /k ""%PYTHON_EXE%" -m app.message_worker"
if errorlevel 1 goto :launch_failed

echo [5/5] Starting Vue development server: http://127.0.0.1:5173
start "MoldPilot Web" /D "%WEB_DIR%" cmd.exe /k "npm.cmd run dev -- --host 127.0.0.1"
if errorlevel 1 goto :launch_failed

echo.
echo [OK] The five services were launched in separate windows.
echo If a worker window exits, check .env, Redis and the model configuration in the workbench.
echo The browser will open shortly. The first frontend build may take a moment.
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5173/"

popd
exit /b 0

:launch_failed
echo.
echo [ERROR] Failed to launch a service window. Check the messages above.

:failed
echo.
if /I not "%~1"=="--check" pause
popd
exit /b 1
