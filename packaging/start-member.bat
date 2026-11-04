@echo off
cd /d "%~dp0"
python -m bridge.member_ui
if errorlevel 1 pause
