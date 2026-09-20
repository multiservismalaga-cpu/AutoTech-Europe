@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% -m pip install -r requirements.txt
if errorlevel 1 (echo Python no esta disponible. Instala Python 3.11+ y marca Add Python to PATH.&pause&exit /b 1)
%PY% -m pip install pyinstaller
if errorlevel 1 (echo No se pudo instalar PyInstaller.&pause&exit /b 1)
%PY% -m PyInstaller --noconfirm AutoTech_Europe.spec
if errorlevel 1 (echo Fallo la compilacion.&pause&exit /b 1)
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" AutoTech_Europe.iss) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" ("%ProgramFiles%\Inno Setup 6\ISCC.exe" AutoTech_Europe.iss) else echo Inno Setup 6 no esta instalado. Se ha creado dist\AutoTech_Europe.exe.
pause