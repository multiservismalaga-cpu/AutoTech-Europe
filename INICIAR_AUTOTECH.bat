@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% -m pip install -r requirements.txt
if errorlevel 1 (echo No se pudo instalar Python/dependencias.&pause&exit /b 1)
start "AutoTech Europe" %PY% -m uvicorn main:app --host 127.0.0.1 --port 8000
%PY% -c "import time,webbrowser; time.sleep(2); webbrowser.open('http://127.0.0.1:8000')"