@echo off
rem CA LazyNest – Installation (Fusion vorher schliessen)
cd /d "%~dp0"
if exist "%~dp0install.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
) else (
  echo install.ps1 nicht gefunden – bitte das Repo/ZIP vollstaendig entpacken.
)
pause
