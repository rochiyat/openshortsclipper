@echo off
title OpenShorts Launcher
echo ===================================================
echo             Starting OpenShorts Native
echo ===================================================
echo [1/2] Launching Backend Server on port 8000...
start "OpenShorts - Backend API" cmd /k "title OpenShorts Backend && .\venv\Scripts\activate.bat && uvicorn app:app --host 0.0.0.0 --port 8000 --reload"

echo [2/2] Launching Frontend Dashboard on port 5173...
start "OpenShorts - Frontend Dashboard" cmd /k "title OpenShorts Dashboard && cd dashboard && set VITE_PROXY_TARGET=http://localhost:8000 && npm run dev"

echo.
echo ===================================================
echo Kedua server sedang berjalan di jendela terpisah!
echo Buka browser Anda di: http://localhost:5173
echo ===================================================
timeout /t 5 >nul
