@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m pip install --require-hashes --upgrade --target .deps -r requirements-adapters.txt
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (
        python -m pip install --require-hashes --upgrade --target .deps -r requirements-adapters.txt
    ) else (
        py -3 -m pip install --require-hashes --upgrade --target .deps -r requirements-adapters.txt
    )
)
set "RUN_STATUS=%ERRORLEVEL%"
if "%RUN_STATUS%"=="0" (echo Adapters installed locally.) else (echo Installation failed. See README.md.)
popd
pause
exit /b %RUN_STATUS%
