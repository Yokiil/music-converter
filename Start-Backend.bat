@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m server %*
) else (
    python -m server %*
)
set "RUN_STATUS=%ERRORLEVEL%"
popd
if not "%RUN_STATUS%"=="0" pause
exit /b %RUN_STATUS%
