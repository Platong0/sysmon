@echo off
rem Двойной клик по этому файлу запускает оверлей на Windows.
cd /d "%~dp0"
pip install psutil >nul 2>&1
start "" pythonw monitor_win.py
