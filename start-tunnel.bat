@echo off
REM CompetePulse - One-click dev servers and Cloudflare Quick Tunnel
REM Starts the FastAPI backend (port 8792), the Vite dev server (port 5174), and a Cloudflare tunnel for external sharing.

set "PROJECT_DIR=%~dp0"
set "FRONTEND_DIR=%PROJECT_DIR%frontend"
set "BACKEND_DIR=%PROJECT_DIR%backend"
set "CLOUDFLARED=C:\Users\Vikee.Dhakulkar\cloudflared\cloudflared.exe"

echo ======================================================================
echo Starting CompetePulse Platform with Cloudflare Tunnel (Port 5174)
echo ======================================================================
echo.

echo [1/3] Starting CompetePulse FastAPI Backend on http://localhost:8792 ...
start "CompetePulse - Backend" cmd /k "cd /d "%BACKEND_DIR%" && python -m uvicorn app.main:app --port 8792 --reload"

echo [2/3] Starting CompetePulse Vite Dev Server on port 5174...
start "CompetePulse - Frontend" cmd /k "cd /d "%FRONTEND_DIR%" && node "node_modules\vite\bin\vite.js" --port 5174"

echo.
echo Waiting 6 seconds for dev servers to come up...
timeout /t 6 /nobreak >nul

echo [3/3] Starting Cloudflare Tunnel pointing to http://localhost:5174 ...
start "CompetePulse - Cloudflare Tunnel" cmd /k ""%CLOUDFLARED%" tunnel --url http://localhost:5174"

echo.
echo ======================================================================
echo Three windows have opened:
echo  1. "CompetePulse - Backend" (FastAPI on port 8792)
echo  2. "CompetePulse - Frontend" (Vite on port 5174)
echo  3. "CompetePulse - Cloudflare Tunnel" (External Sharing)
echo.
echo Look in the "CompetePulse - Cloudflare Tunnel" window for your public
echo shareable URL (e.g. https://...trycloudflare.com).
echo Close the terminal windows when you wish to stop sharing.
echo ======================================================================
pause
