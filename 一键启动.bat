@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title 中医处方开具与审核管理系统 - 一键启动

set "VENV=.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "PYEXE="
set "ADDR=127.0.0.1"
set "PORT=8000"

echo ============================================================
echo          中医处方开具与审核管理系统  TCM-RMS
echo ============================================================
echo.

if not exist "manage.py" goto NO_PROJECT

rem ============ 第 1 步：确定用哪个 Python ============
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYEXE=%VENV_PY%"
)

if defined PYEXE (
    echo [1/5] 使用项目自带虚拟环境 .venv
    goto STEP_DEPS
)

rem 虚拟环境缺失或已损坏，先找一个能用的 Python
echo [1/5] 未找到可用虚拟环境，正在查找 Python...
call :FIND_PY
if not defined PYEXE goto NO_PYTHON

echo [2/5] 正在创建虚拟环境 .venv ...
%PYEXE% -m venv "%VENV%"
if errorlevel 1 goto VENV_FAIL
if not exist "%VENV_PY%" goto VENV_FAIL
set "PYEXE=%VENV_PY%"

:STEP_DEPS
rem ============ 第 2 步：检查并安装依赖 ============
%PYEXE% -c "import django, dotenv, openpyxl, cryptography" >nul 2>nul
if not errorlevel 1 goto STEP_ENV

echo [3/5] 缺少依赖，正在安装，首次约需 1-3 分钟...
call :INSTALL_DEPS
if errorlevel 1 goto PIP_FAIL

:STEP_ENV
rem ============ 第 3 步：生成 .env ============
if not exist ".env" (
    echo [4/5] 正在生成 .env 配置文件...
    %PYEXE% init_env.py
    if errorlevel 1 goto ENV_FAIL
)

rem ============ 第 4 步：首次运行建库 + 演示数据 ============
if not exist "db.sqlite3" (
    echo [5/5] 首次运行，正在初始化数据库...
    %PYEXE% manage.py migrate --noinput
    if errorlevel 1 goto DB_FAIL
    %PYEXE% manage.py init_data
    if errorlevel 1 goto DB_FAIL
    %PYEXE% manage.py seed_prescriptions
)

rem ============ 第 5 步：端口占用检测 ============
netstat -ano | findstr /C:"127.0.0.1:%PORT%" | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
    echo.
    echo 服务已在运行，直接为你打开浏览器。
    start "" "http://127.0.0.1:%PORT%/"
    goto END
)

rem 带参数时直接转发给 manage.py，例如：一键启动.bat check / migrate / shell
if not "%~1"=="" (
    %PYEXE% manage.py %*
    goto END
)

echo.
echo ============================================================
echo   本机访问    http://127.0.0.1:%PORT%
echo   管理员账号  admin1
echo   统一密码    rx123456
echo   ^(医师 doctor1~5 / 药师 pharmacist1 / 收费 cashier1 等^)
echo   停止服务    按 Ctrl+C
echo ============================================================
echo.

rem 等 Django 起来再开浏览器，避免出现空白页
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:%PORT%/'"

%PYEXE% manage.py runserver %ADDR%:%PORT%

echo.
echo 服务已停止。
goto END

rem ==================== 子过程 ====================

:FIND_PY
rem 逐个探测候选解释器：本机 PATH 上的 python 可能是残留虚拟环境的
rem exe，一调用就报 failed to locate pyvenv.cfg，所以坏的直接跳过。
call :PROBE "%LocalAppData%\Programs\Python\Python313\python.exe"
if defined PYEXE exit /b 0
call :PROBE "%LocalAppData%\Programs\Python\Python312\python.exe"
if defined PYEXE exit /b 0
call :PROBE "%LocalAppData%\Programs\Python\Python311\python.exe"
if defined PYEXE exit /b 0
call :PROBE "C:\Python313\python.exe"
if defined PYEXE exit /b 0
call :PROBE "C:\Python312\python.exe"
if defined PYEXE exit /b 0
call :PROBE "C:\Python311\python.exe"
if defined PYEXE exit /b 0
call :PROBE "python"
if defined PYEXE exit /b 0

rem py 启动器无法带引号探测，单独处理
py -3 -c "import sys, venv, ensurepip" >nul 2>nul
if not errorlevel 1 (
    set "PYEXE=py -3"
    exit /b 0
)
exit /b 0

:PROBE
if "%~1"=="" exit /b 0
"%~1" -c "import sys, venv, ensurepip" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYEXE=%~1"
exit /b 0

:INSTALL_DEPS
rem 依次尝试镜像源：清华源经某些代理会返回 403，所以准备多个备用
for %%M in (
    https://mirrors.aliyun.com/pypi/simple
    https://mirrors.cloud.tencent.com/pypi/simple
    https://pypi.tuna.tsinghua.edu.cn/simple
    https://pypi.org/simple
) do (
    echo       尝试源 %%M
    %PYEXE% -m pip install --disable-pip-version-check --retries 2 --timeout 30 -q -i %%M Django==5.2.18 cryptography python-dotenv openpyxl >nul 2>nul
    if not errorlevel 1 (
        %PYEXE% -m pip install --disable-pip-version-check --retries 2 --timeout 30 -q -i %%M -r requirements.txt >nul 2>nul
        exit /b 0
    )
)
exit /b 1

rem ==================== 错误处理 ====================

:NO_PROJECT
echo [错误] 当前目录找不到 manage.py。
echo        请把本文件放到项目根目录（和 manage.py 同一层）再双击。
echo.
pause
exit /b 1

:NO_PYTHON
echo.
echo [错误] 没有找到可用的 Python 3.10 及以上版本。
echo        请到 https://www.python.org/downloads/ 安装 Python 3.12 或 3.13，
echo        安装时务必勾选 Add Python to PATH。
echo.
pause
exit /b 1

:VENV_FAIL
echo.
echo [错误] 虚拟环境创建失败：%VENV%
echo        请确认该 Python 可独立运行：%PYEXE% -V
echo.
pause
exit /b 1

:PIP_FAIL
echo.
echo [错误] 依赖安装失败，请检查网络后重试。
echo        也可手动执行：
echo        %VENV_PY% -m pip install -i https://mirrors.aliyun.com/pypi/simple Django==5.2.18 cryptography python-dotenv openpyxl
echo.
pause
exit /b 1

:ENV_FAIL
echo.
echo [错误] .env 生成失败。
echo        可手动复制 .env.example 为 .env，并填入 DJANGO_DEBUG=True。
echo.
pause
exit /b 1

:DB_FAIL
echo.
echo [错误] 数据库初始化失败，请看上面的报错信息。
echo.
pause
exit /b 1

:END
if "%~1"=="" pause
exit /b 0
