@echo off
rem Двойной клик по этому файлу запускает оверлей на Windows (из исходников).
cd /d "%~dp0"
pip install psutil >nul 2>&1
set PYTHONPATH=src
start "" pythonw -m sysmon_overlay
