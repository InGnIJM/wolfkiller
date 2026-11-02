@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ASCII-only: cmd.exe parses .bat as the system ANSI code page.

start "WolfKiller Backend" /D "%~dp0backend" cmd /k "uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app"
start "WolfKiller Frontend" /D "%~dp0frontend" cmd /k "npm run dev"
start "" "http://localhost:5173"