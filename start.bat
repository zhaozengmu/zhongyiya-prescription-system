@echo off
chcp 936 >nul
rem 桌面快捷方式的入口：转发到中文命名的 一键启动.bat，保持单一启动逻辑
cd /d "%~dp0"
call "%~dp0\一键启动.bat" %*
