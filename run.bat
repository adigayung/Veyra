@echo off
setlocal
REM ============================================================
REM  Veyra launcher
REM  Selalu memakai interpreter Python dari J:\Veyra\venv,
REM  bukan Python global / Miniconda.
REM ============================================================

REM Pindah ke root project (folder tempat file .bat ini berada),
REM sehingga berlaku walau di-double-click dari lokasi mana pun.
cd /d "%~dp0"

set "VENV_PY=%~dp0venv\Scripts\python.exe"
set "APP_ENTRY=%~dp0desktop.py"

echo [Veyra] Working directory : %CD%
echo [Veyra] Python (venv)    : %VENV_PY%
echo.

REM 1) Pastikan virtual environment Veyra tersedia.
if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment tidak ditemukan:
    echo         %VENV_PY%
    echo.
    echo         Buat terlebih dahulu dari root project:
    echo             python -m venv venv
    echo             venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    goto :done
)

REM 2) Pastikan entry point aplikasi tersedia.
if not exist "%APP_ENTRY%" (
    echo [ERROR] Entry point tidak ditemukan: %APP_ENTRY%
    echo.
    goto :done
)

REM 3) Aktifkan venv (agar proses anak juga memakai interpreter venv).
call "%~dp0venv\Scripts\activate.bat"

REM 4) Pastikan dependency utama dapat di-import dari venv ini.
"%VENV_PY%" -c "import flask, betrayer, webview" 2>nul
if errorlevel 1 (
    echo [ERROR] Dependency utama tidak tersedia di venv ^(flask / betrayer^).
    echo         Install dengan:
    echo             venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    goto :done
)

echo [Veyra] Menjalankan Veyra...
echo.
REM 5) Jalankan entry point Veyra memakai interpreter venv secara eksplisit.
"%VENV_PY%" "%APP_ENTRY%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo [Veyra] Aplikasi berhenti ^(exit code %EXIT_CODE%^).

:done
REM Jangan tutup window agar error / output runtime tetap terlihat.
echo.
endlocal
