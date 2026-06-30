@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "app\main.py"
) else (
    py -3 "app\main.py"
)

if errorlevel 1 (
    echo.
    echo Application failed to start.
    echo Install dependencies with: py -3 -m pip install -r requirements.txt
    pause
)

endlocal
