@echo off
setlocal
set "ROOT=%~dp0"

echo ----------------------------------------------------------------------
echo Starting CompetePulse - MASH CI Monitoring Agent System
echo ----------------------------------------------------------------------
echo.

echo [1/2] Starting CompetePulse FastAPI Backend on http://localhost:8792 ...
start "CompetePulse - Backend" cmd /k call "%ROOT%run-backend.bat"

echo [2/2] Starting CompetePulse Frontend on http://localhost:5174 ...
start "CompetePulse - Frontend" cmd /k call "%ROOT%run-frontend.bat"

echo.
echo Waiting 6 seconds for servers to initialize...
timeout /t 6 /nobreak >nul

echo Opening browser at http://localhost:5174/competepulse ...
start "" "http://localhost:5174/competepulse"

echo.
echo CompetePulse is running! Keep the backend and frontend terminal windows open.
echo Run stop-app.ps1 to shut down both servers.
pause
