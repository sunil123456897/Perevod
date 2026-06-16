@echo off
chcp 65001 > nul
setlocal enableextensions

rem ============================================================
rem  Novel Translator - launcher
rem  Двойной клик по start.bat открывает GUI.
rem  Аргументы: start.bat --cli --project Fermer ...  (без паузы)
rem ============================================================

cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "USE_VENV=1"

rem 1) Предпочитаем venv проекта, если он есть.
if not exist "%PYTHON_EXE%" (
    echo [WARN] Виртуальное окружение не найдено: %PYTHON_EXE%
    where py > nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py -3"
        set "USE_VENV=0"
        goto :run
    )
    where python > nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=python"
        set "USE_VENV=0"
        goto :run
    )
    echo [ERROR] Python не найден в системе.
    echo         Установите Python 3.10+ или пересоздайте .venv.
    if "%~1"=="" pause
    exit /b 1
)

:run
echo Запуск Novel Translator...
echo Рабочая папка: %~dp0
echo Python: %PYTHON_EXE%

if "%USE_VENV%"=="1" (
    "%PYTHON_EXE%" "%~dp0run.py" %*
) else (
    %PYTHON_EXE% "%~dp0run.py" %*
)
set EXIT_CODE=%ERRORLEVEL%

echo.
if "%EXIT_CODE%"=="0" (
    echo [OK] Программа завершилась успешно ^(код %EXIT_CODE%^).
) else (
    echo [ERROR] Программа завершилась с кодом %EXIT_CODE%.
)

rem Пауза только при запуске двойным кликом (без аргументов) — GUI-режим.
if "%~1"=="" pause
exit /b %EXIT_CODE%
