@echo off
setlocal enabledelayedexpansion
title AudiobookGdrive
cd /d "%~dp0"

REM ============================================================
REM  run_AudiobookGdrive.bat
REM  Khoi dong nhanh AudiobookGdrive tren Windows.
REM  - Tu do tim Python (ke ca khi chua co trong PATH).
REM  - Tu tao virtualenv (.venv) neu chua co.
REM  - Tu cai dat PyQt5 (requirements.txt) neu chua co.
REM  - Chay app (main.py).
REM ============================================================

call :find_python
if "%PY_EXE%"=="" (
    echo [LOI] Khong tim thay Python tren may nay.
    echo.
    echo Hay cai Python 3.9 tro len tu https://www.python.org/downloads/
    echo Luc cai dat, nho tick chon "Add python.exe to PATH" o man hinh dau tien.
    echo Neu da cai roi ma van thay loi nay: mo lai bo cai dat Python tu
    echo Control Panel/Settings ^> Apps, chon "Modify" ^> tick "Add python.exe
    echo to PATH" ^> Install, roi chay lai file nay.
    echo.
    pause
    exit /b 1
)

echo Da tim thay Python: %PY_EXE% %PY_ARGS%
"%PY_EXE%" %PY_ARGS% --version

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Dang tao moi truong ao .venv lan dau...
    "%PY_EXE%" %PY_ARGS% -m venv .venv
    if errorlevel 1 (
        echo [LOI] Khong tao duoc .venv.
        pause
        exit /b 1
    )
)

set "VENV_PY=.venv\Scripts\python.exe"

echo [2/3] Dang kiem tra / cai dat thu vien can thiet (PyQt5)...
"%VENV_PY%" -m pip show PyQt5 >nul 2>nul
if errorlevel 1 (
    "%VENV_PY%" -m pip install --upgrade pip >nul
    "%VENV_PY%" -m pip install PyQt5==5.15.10
    if errorlevel 1 (
        echo [LOI] Cai dat PyQt5 that bai. Kiem tra lai ket noi mang.
        pause
        exit /b 1
    )
)

echo [3/3] Dang khoi dong AudiobookGdrive...
"%VENV_PY%" main.py

if errorlevel 1 (
    echo.
    echo [LOI] Ung dung thoat voi loi. Xem thong bao o tren.
    pause
)

endlocal
exit /b 0


REM ------------------------------------------------------------
REM  :find_python
REM  Dat bien PY_EXE (duong dan file .exe) + PY_ARGS (tham so kem
REM  theo, vd "-3" cho Python Launcher) tro toi 1 Python dung
REM  duoc, thu lan luot nhieu cach vi PATH khong phai luc nao
REM  cung co san ngay sau khi cai Python (nhat la khi nguoi dung
REM  khong tick "Add python.exe to PATH" luc cai, hoac chua mo
REM  lai cua so terminal moi de PATH duoc nap lai):
REM   1. "python" trong PATH
REM   2. Python Launcher "py" (py.exe thuong duoc cai san vao
REM      C:\Windows\ boi bo cai Python tu python.org, hoat dong
REM      ngay ca khi "python" chua co trong PATH)
REM   3. Do truc tiep trong cac thu muc cai dat pho bien
REM ------------------------------------------------------------
:find_python
set "PY_EXE="
set "PY_ARGS="

where python >nul 2>nul
if not errorlevel 1 (
    set "PY_EXE=python"
    goto :eof
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 (
        set "PY_EXE=py"
        set "PY_ARGS=-3"
        goto :eof
    )
)

for %%D in (
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "%LocalAppData%\Programs\Python\Python39\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "C:\Python39\python.exe"
) do (
    if exist "%%~D" (
        set "PY_EXE=%%~D"
        goto :eof
    )
)

goto :eof
