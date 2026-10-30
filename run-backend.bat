@echo off
cd /d "%~dp0backend"
echo Starting CompetePulse Backend on http://localhost:8792 ...
python -m uvicorn app.main:app --port 8792 --reload
echo.
echo Backend stopped.
pause
