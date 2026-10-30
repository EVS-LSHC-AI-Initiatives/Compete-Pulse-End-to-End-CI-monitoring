@echo off
cd /d "%~dp0frontend"
echo Starting CompetePulse Frontend on http://localhost:5174 ...
node "node_modules\vite\bin\vite.js" --port 5174
echo.
echo Frontend stopped.
pause
