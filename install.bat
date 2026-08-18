@echo off
REM Double-click entry point. Runs install.ps1 with the execution policy relaxed
REM for this one invocation, so a locked-down machine does not block setup.
REM Prefers PowerShell 7 (pwsh) and falls back to the Windows PowerShell 5.1
REM that ships with every Windows install.
setlocal
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
)
set EXITCODE=%ERRORLEVEL%
echo.
pause
exit /b %EXITCODE%
