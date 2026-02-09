@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
echo ============================================================
echo   FTP Sync Server - Sborka
echo ============================================================
echo.

echo [1/4] Ustanovka zavisimostej...
pip install -r requirements.txt
if errorlevel 1 goto :err_deps

echo.
echo [2/4] Sborka ftp_sync_server.exe...
python -m PyInstaller --onefile --noconsole --name ftp_sync_server --hidden-import uvicorn.logging --hidden-import uvicorn.loops --hidden-import uvicorn.loops.auto --hidden-import uvicorn.protocols --hidden-import uvicorn.protocols.http --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.http.h11_impl --hidden-import uvicorn.protocols.http.httptools_impl --hidden-import uvicorn.protocols.websockets --hidden-import uvicorn.protocols.websockets.auto --hidden-import uvicorn.lifespan --hidden-import uvicorn.lifespan.on --hidden-import uvicorn.lifespan.off --noconfirm main.py
if errorlevel 1 goto :err_server

echo.
echo [3/4] Sborka ftp_sync_watchdog.exe...
python -m PyInstaller --onefile --noconsole --name ftp_sync_watchdog --noconfirm service_watchdog.py
if errorlevel 1 goto :err_watchdog

echo.
echo [4/4] Kompilaciya ustanovshchika...

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "!ISCC!"=="" goto :no_inno

echo Inno Setup najden: !ISCC!
"!ISCC!" installer.iss
if errorlevel 1 goto :err_inno

echo.
echo ============================================================
echo   GOTOVO!
echo ============================================================
echo.
echo   dist\ftp_sync_server.exe   - osnovnaya programma
echo   dist\ftp_sync_watchdog.exe  - sluzhba perezapuska
echo   installer_output\FTP_Sync_Setup.exe - ustanovshchik
echo.
pause
exit /b 0

:no_inno
echo.
echo   Inno Setup ne najden!
echo   Skachajte: https://jrsoftware.org/isdl.php
echo   Posle ustanovki zapustite build.bat povtorno.
echo.
echo   EXE-fajly uzhe sobrany v papke dist\
echo.
pause
exit /b 0

:err_deps
echo OSHIBKA: ne udalos' ustanovit' zavisimosti
pause
exit /b 1

:err_server
echo OSHIBKA: sborka ftp_sync_server.exe ne udalas'
pause
exit /b 1

:err_watchdog
echo OSHIBKA: sborka ftp_sync_watchdog.exe ne udalas'
pause
exit /b 1

:err_inno
echo OSHIBKA: kompilaciya ustanovshchika ne udalas'
pause
exit /b 1
