@echo off
title OpenShorts Backend
echo Starting OpenShorts Backend on port 8000...
call .\venv\Scripts\activate.bat
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
pause
