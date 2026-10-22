@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  AURA ETIQUETAS - Inicio local
 echo.
where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
  echo Necesitas instalar Python 3.10 o superior desde python.org.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Preparando entorno local por primera vez...
  %PYTHON% -m venv .venv
  if errorlevel 1 (pause & exit /b 1)
)
echo Verificando dependencias...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt -q
if errorlevel 1 (
  echo No se pudieron instalar las dependencias. Verifica tu conexion.
  pause
  exit /b 1
)
echo Iniciando...
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
