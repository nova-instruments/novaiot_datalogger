@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "PY_CMD="

echo ========================================
echo NOVAIOT Datalogger - Build Windows EXE
echo ========================================
echo.

where py >nul 2>&1
if not errorlevel 1 set "PY_CMD=py"

if not defined PY_CMD (
  where python >nul 2>&1
  if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
  echo [ERRO] Python nao encontrado no PATH.
  echo Instale o Python para Windows e/ou habilite o PATH, depois tente novamente.
  exit /b 1
)

echo Usando interpretador: %PY_CMD%

if not exist "img\logo2026.png" (
  echo [ERRO] Arquivo de logo nao encontrado: img\logo2026.png
  exit /b 1
)

if not exist "app.py" (
  echo [ERRO] app.py nao encontrado nesta pasta.
  exit /b 1
)

if not exist "run_app.py" (
  echo [ERRO] run_app.py nao encontrado nesta pasta.
  exit /b 1
)

if not exist "novaiot_datalogger.exe.spec" (
  echo [ERRO] novaiot_datalogger.exe.spec nao encontrado nesta pasta.
  exit /b 1
)

echo [1/4] Instalando/atualizando dependencias...
%PY_CMD% -m pip install --upgrade pip
if errorlevel 1 goto :fail

%PY_CMD% -m pip install -r requirements.txt
if errorlevel 1 goto :fail

%PY_CMD% -m pip install pyinstaller pillow
if errorlevel 1 goto :fail

echo [2/4] Gerando icone ICO a partir do logo PNG...
%PY_CMD% -c "from PIL import Image; i=Image.open('img/logo2026.png').convert('RGBA'); i.save('img/logo2026.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)]); print('icone gerado: img/logo2026.ico')"
if errorlevel 1 goto :fail

echo [3/4] Limpando builds anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/4] Gerando EXE com PyInstaller...
%PY_CMD% -m PyInstaller novaiot_datalogger.exe.spec
if errorlevel 1 goto :fail

echo.
echo Build concluido com sucesso!
echo Arquivo gerado em: dist\novaiot_datalogger.exe
exit /b 0

:fail
echo.
echo [ERRO] Falha durante o processo de build.
exit /b 1
