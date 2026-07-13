@echo off
REM ============================================================
REM AutoWpp 2 - Instalador de dependencias (Windows)
REM Requer: Python 3.10+ e Node.js 18+ ja instalados no PATH.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo [1/6] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python nao encontrado. Instale em https://www.python.org/downloads/
    echo       Marque a opcao "Add Python to PATH" durante a instalacao.
    pause & exit /b 1
)
python --version

echo.
echo [2/6] Verificando Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo ERRO: Node.js nao encontrado. Instale em https://nodejs.org/ (versao LTS)
    pause & exit /b 1
)
node --version

echo.
echo [3/6] Instalando dependencias Python (pip)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERRO: falha no pip install.
    pause & exit /b 1
)

echo.
echo [4/6] Instalando dependencias Node.js (npm)...
call npm install
if errorlevel 1 (
    echo ERRO: falha no npm install.
    pause & exit /b 1
)

echo.
echo [5/6] Instalando navegador do Puppeteer (Chrome headless)...
call npx puppeteer browsers install chrome
if errorlevel 1 (
    echo AVISO: nao foi possivel baixar o Chrome do Puppeteer.
    echo        Se voce ja tem o Google Chrome instalado, o bot deve funcionar mesmo assim.
)

echo.
echo [6/6] Criando .env a partir do .env.example (se nao existir)...
if not exist .env (
    copy .env.example .env >nul
    echo .env criado - revise as variaveis antes de rodar.
) else (
    echo .env ja existe - mantido.
)

echo.
echo ============================================================
echo  Instalacao concluida!
echo  - Interface web:  python frontend.py   (http://127.0.0.1:8502)
echo  - CLI:            python orchestrator.py --chips 2 --csv contatos.csv
echo ============================================================
pause
endlocal
