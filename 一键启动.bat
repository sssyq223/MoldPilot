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
"%PYTHON_EXE%" -c "import app.api; import app.agent_worker"
if errorlevel 1 (
    echo [ERROR] Backend import validation failed. Review the Python traceback above.
    goto :failed
)

if /I "%~1"=="--check" (
    echo [OK] Found .env, Python virtual environment, npm and frontend dependencies.
    echo [OK] API and Agent Worker imports are valid.
    echo [INFO] Database, Redis and model connectivity is checked by the running services.
    popd
    exit /b 0
)

echo [1/3] Starting FastAPI: http://127.0.0.1:8000
start "MoldPilot API" /D "%ROOT%" cmd.exe /k ""%PYTHON_EXE%" -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log"
if errorlevel 1 goto :launch_failed

echo [2/3] Starting Agent Worker
start "MoldPilot Agent Worker" /D "%ROOT%" cmd.exe /k ""%PYTHON_EXE%" -m app.agent_worker"
if errorlevel 1 goto :launch_failed

echo [3/3] Starting Vue development server: http://127.0.0.1:5173
start "MoldPilot Web" /D "%WEB_DIR%" cmd.exe /k "npm.cmd run dev -- --host 127.0.0.1"
if errorlevel 1 goto :launch_failed

echo.
echo [OK] The three services were launched in separate windows.
echo If the worker window exits, check .env and the model configuration in the workbench.
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
pause
popd
exit /b 1
