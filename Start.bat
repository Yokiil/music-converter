@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.py
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (python app.py) else (py -3 app.py)
)
set "RUN_STATUS=%ERRORLEVEL%"
if not "%RUN_STATUS%"=="0" (
    echo Launch failed. Install Python 3.10 or newer with Tkinter, or see README.md.
    pause
)
popd
exit /b %RUN_STATUS%
