@echo off
chcp 936 >nul
rem 桌面快捷方式的中转脚本：固定用英文文件名，真正的启动逻辑交给 一键启动.bat
cd /d "%~dp0"
call "%~dp0一键启动.bat" %*
