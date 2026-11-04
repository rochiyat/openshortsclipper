@echo off
title OpenShorts Dashboard
echo Starting OpenShorts Frontend on port 5173...
cd dashboard
set VITE_PROXY_TARGET=http://localhost:8000
npm run dev
pause
