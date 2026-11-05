@echo off
set PYTHONIOENCODING=utf-8
where pythonw >nul 2>nul
if errorlevel 1 (
  start "" python "%~dp0roundtable.py"
) else (
  start "" pythonw "%~dp0roundtable.py"
)
