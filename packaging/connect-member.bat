@echo off
if exist "%~dp0CojamCore.exe" (
  "%~dp0CojamCore.exe" member-ui
) else (
  "%~dp0Cojam.exe" member-ui
)
if errorlevel 1 pause
