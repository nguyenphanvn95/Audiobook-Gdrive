@echo off
setlocal EnableExtensions
title Build AudiobookGdrive.exe
cd /d "%~dp0"

REM ============================================================
REM  build_exe.bat
REM  Dong goi AudiobookGdrive thanh 1 file .exe doc lap (Windows)
REM  bang PyInstaller (--onefile --windowed).
REM  File ket qua: dist\AudiobookGdrive.exe
REM ============================================================

set "PY_EXE="
set "PY_ARGS="

where python >nul 2>nul
if not errorlevel 1 (
    set "PY_EXE=python"
    goto :python_found
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 (
        set "PY_EXE=py"
        set "PY_ARGS=-3"
        goto :python_found
    )
)

if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    set "PY_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
    goto :python_found
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PY_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :python_found
)
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
    goto :python_found
)
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
    set "PY_EXE=%LocalAppData%\Programs\Python\Python310\python.exe"
    goto :python_found
)
if exist "%LocalAppData%\Programs\Python\Python39\python.exe" (
    set "PY_EXE=%LocalAppData%\Programs\Python\Python39\python.exe"
    goto :python_found
)
if exist "C:\Python313\python.exe" (
    set "PY_EXE=C:\Python313\python.exe"
    goto :python_found
)
if exist "C:\Python312\python.exe" (
    set "PY_EXE=C:\Python312\python.exe"
    goto :python_found
)
if exist "C:\Python311\python.exe" (
    set "PY_EXE=C:\Python311\python.exe"
    goto :python_found
)
if exist "C:\Python310\python.exe" (
    set "PY_EXE=C:\Python310\python.exe"
    goto :python_found
)
if exist "C:\Python39\python.exe" (
    set "PY_EXE=C:\Python39\python.exe"
    goto :python_found
)

echo [LOI] Khong tim thay Python tren may nay.
echo.
echo Hay cai Python 3.9 tro len tu https://www.python.org/downloads/
echo Luc cai dat, nho tick chon "Add python.exe to PATH" o man hinh dau tien.
echo.
pause
exit /b 1

:python_found
echo Da tim thay Python: %PY_EXE% %PY_ARGS%
"%PY_EXE%" %PY_ARGS% --version
if errorlevel 1 (
    echo [LOI] Python khong chay duoc. Kiem tra lai cai dat.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Dang tao moi truong ao .venv...
    "%PY_EXE%" %PY_ARGS% -m venv .venv
    if errorlevel 1 (
        echo [LOI] Khong tao duoc .venv.
        pause
        exit /b 1
    )
)

set "VENV_PY=.venv\Scripts\python.exe"
set "ICON_FILE=%~dp0audiobookgdrive\resources\icon.ico"
set "ICON_PNG=%~dp0audiobookgdrive\resources\icon.png"

if not exist "%ICON_FILE%" (
    echo [LOI] Khong tim thay file icon: %ICON_FILE%
    pause
    exit /b 1
)
echo   - Icon: %ICON_FILE%

echo [2/4] Dang cai dat PyQt5 + PyInstaller...
"%VENV_PY%" -m pip install --upgrade pip >nul
"%VENV_PY%" -m pip install PyQt5==5.15.10 pyinstaller==6.10.0
if errorlevel 1 (
    echo [LOI] Cai dat thu vien that bai. Kiem tra lai ket noi mang.
    pause
    exit /b 1
)

echo [3/4] Dang don cac file / thu muc build cu neu co...

tasklist /FI "IMAGENAME eq AudiobookGdrive.exe" 2>nul | find /I "AudiobookGdrive.exe" >nul
if not errorlevel 1 (
    echo   - Phat hien AudiobookGdrive.exe dang chay, dang tat...
    taskkill /F /IM AudiobookGdrive.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)
taskkill /F /IM AudiobookGdrive.exe >nul 2>&1
timeout /t 1 /nobreak >nul

if exist "build" rmdir /s /q "build" 2>nul
if exist "build" (
    timeout /t 1 /nobreak >nul
    rmdir /s /q "build" 2>nul
)

if exist "dist\AudiobookGdrive.exe" (
    echo   - Dang xoa dist\AudiobookGdrive.exe cu...
    del /f /q "dist\AudiobookGdrive.exe" 2>nul
)
if exist "dist\AudiobookGdrive.exe" (
    ren "dist\AudiobookGdrive.exe" "AudiobookGdrive_old.exe" 2>nul
    del /f /q "dist\AudiobookGdrive_old.exe" 2>nul
)
if exist "dist\AudiobookGdrive.exe" (
    echo.
    echo [LOI] Khong the xoa dist\AudiobookGdrive.exe - file dang bi khoa.
    echo Hay tat app trong Task Manager roi chay lai.
    pause
    exit /b 1
)

if exist "dist" rmdir /s /q "dist" 2>nul
if exist "dist" (
    timeout /t 1 /nobreak >nul
    rmdir /s /q "dist" 2>nul
)

if exist "AudiobookGdrive.spec" del /q "AudiobookGdrive.spec" 2>nul

echo   - Da don sach xong.

echo [4/4] Dang build AudiobookGdrive.exe - co the mat vai phut...
"%VENV_PY%" -m PyInstaller --noconfirm --onefile --windowed --name "AudiobookGdrive" --icon "%ICON_FILE%" --add-data "audiobookgdrive\resources\icon.png;audiobookgdrive\resources" --add-data "audiobookgdrive\resources\icon.ico;audiobookgdrive\resources" --collect-submodules audiobookgdrive main.py

if errorlevel 1 (
    echo.
    echo [LOI] Build that bai. Xem thong bao loi o tren.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Build thanh cong!  File exe nam o:  dist\AudiobookGdrive.exe
echo  Neu Explorer van hien icon cu: F5 hoac xoa cache icon Windows.
echo ============================================================
echo.
pause
endlocal
exit /b 0
