@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%\..\.."
set "ROOT=%CD%"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "SPEC=%ROOT%\builder\windows\hgr_app.spec"
set "ISS=%ROOT%\installers\windows\hgr_app.iss"
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if not exist "%PYTHON%" (
  echo [ERROR] Virtual environment not found at %PYTHON%
  popd
  exit /b 1
)

if not exist "%SPEC%" (
  echo [ERROR] Missing spec file: %SPEC%
  popd
  exit /b 1
)

if not exist "%ISS%" (
  echo [ERROR] Missing Inno Setup script: %ISS%
  popd
  exit /b 1
)

set "WHISPER_OK="
if exist "%ROOT%\whisper.cpp\build\bin\Release\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper.cpp\build\bin\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper.cpp\build_stream\bin\Release\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper.cpp\build_stream\bin\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper.cpp\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper_bundle\build\bin\Release\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper_bundle\build\bin\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper_bundle\build_stream\bin\Release\whisper-cli.exe" set "WHISPER_OK=1"
if exist "%ROOT%\whisper_bundle\build_stream\bin\whisper-cli.exe" set "WHISPER_OK=1"
if not defined WHISPER_OK (
  echo [ERROR] Could not find whisper-cli.exe under whisper.cpp\ or whisper_bundle\.
  echo         Build whisper.cpp first, or place the CLI under either bundle's build\bin or build_stream\bin.
  popd
  exit /b 1
)

set "WHISPER_MODEL_OK="
if exist "%ROOT%\whisper.cpp\models\ggml-medium.en.bin" set "WHISPER_MODEL_OK=1"
if exist "%ROOT%\whisper_bundle\models\ggml-medium.en.bin" set "WHISPER_MODEL_OK=1"
if not defined WHISPER_MODEL_OK (
  echo [ERROR] Missing model: ggml-medium.en.bin
  echo         Checked: whisper.cpp\models and whisper_bundle\models
  popd
  exit /b 1
)

if not exist "%ROOT%\whisper.cpp\models\ggml-silero-v5.1.2.bin" if not exist "%ROOT%\whisper_bundle\models\ggml-silero-v5.1.2.bin" (
  echo [WARN] Optional VAD model not found: ggml-silero-v5.1.2.bin
)

echo [1/4] Cleaning previous build output...
if exist "%ROOT%\build" rmdir /s /q "%ROOT%\build"
if exist "%ROOT%\dist\Touchless" rmdir /s /q "%ROOT%\dist\Touchless"
if exist "%ROOT%\dist\HGR App" rmdir /s /q "%ROOT%\dist\HGR App"

echo [2/4] Building PyInstaller bundle...
"%PYTHON%" -m PyInstaller "%SPEC%" --noconfirm --clean
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed.
  popd
  exit /b 1
)

if not exist "%ROOT%\dist\Touchless\Touchless.exe" (
  echo [ERROR] Expected bundle missing: dist\Touchless\Touchless.exe
  popd
  exit /b 1
)

REM Sign the inner Touchless.exe BEFORE Inno Setup packs it, so both the
REM ZIP-only update path AND the installer carry a signed exe. Skip
REM signing entirely if SKIP_SIGNING=1 in env (useful for dev builds
REM where you don't want to spend signature cost or hit Azure).
if not "%SKIP_SIGNING%"=="1" (
  echo [2.5/4] Signing Touchless.exe...
  call "%ROOT%\signing\sign-file.bat" "%ROOT%\dist\Touchless\Touchless.exe" "Touchless"
  if !errorlevel! neq 0 (
    echo [ERROR] Signing Touchless.exe failed. Set SKIP_SIGNING=1 to bypass for dev builds.
    popd
    exit /b 1
  )
)

if not exist "%ISCC%" (
  echo [ERROR] Inno Setup compiler not found at:
  echo         !ISCC!
  echo         Install Inno Setup 6 or update the ISCC path in build_windows.bat.
  popd
  exit /b 1
)

echo [3/4] Building installer...
"%ISCC%" "%ISS%"
if errorlevel 1 (
  echo [ERROR] Installer build failed.
  popd
  exit /b 1
)

REM Sign the installer Inno Setup just produced. Same skip flag applies.
if not "%SKIP_SIGNING%"=="1" (
  echo [3.5/4] Signing installer...
  call "%ROOT%\signing\sign-file.bat" "%ROOT%\release\Touchless_Installer.exe" "Touchless Installer"
  if !errorlevel! neq 0 (
    echo [ERROR] Signing installer failed. Set SKIP_SIGNING=1 to bypass for dev builds.
    popd
    exit /b 1
  )
)

echo [4/4] Building app-only update zip (small download for incremental updates)...
"%PYTHON%" "%ROOT%\builder\windows\build_app_update_zip.py"
if errorlevel 1 (
  echo [WARN] App-only zip build failed; full installer is still good.
)

echo.
echo Build complete.
echo Bundle:        %ROOT%\dist\Touchless
echo Installer:     %ROOT%\release\Touchless_Installer.exe
echo App update zip:%ROOT%\release\Touchless_App_Update_*.zip
echo.
echo Upload BOTH artifacts to the GitHub release for that version:
echo   - Touchless_Installer.exe   (full install / first download / breaking change)
echo   - Touchless_App_Update_*.zip (small auto-update for existing users)

popd
exit /b 0

REM Author: Konstantin Markov
