@echo off
chcp 65001 > nul

echo Starting Novel Translator...
echo Working directory: %~dp0

cd /d "%~dp0"

python run.py %*
set EXIT_CODE=%ERRORLEVEL%

if "%~1"=="" pause
exit /b %EXIT_CODE%
