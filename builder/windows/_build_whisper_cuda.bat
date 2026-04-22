@echo off
setlocal

call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
  echo [ERROR] vcvars64.bat failed
  exit /b 1
)

set "PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin;%PATH%"
set "ROOT=c:\HGR App v1.0.0"
set "SRC=%ROOT%\whisper.cpp"
set "BUILD=%ROOT%\whisper_bundle\build_cuda"

"C:\CMake\bin\cmake.exe" -S "%SRC%" -B "%BUILD%" -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_CUDA=ON ^
  -DWHISPER_SDL2=ON ^
  -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake ^
  -DCMAKE_CUDA_COMPILER="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.4/bin/nvcc.exe" ^
  -DCUDAToolkit_ROOT="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.4"
if errorlevel 1 (
  echo [ERROR] cmake configure failed
  exit /b 1
)

"C:\CMake\bin\cmake.exe" --build "%BUILD%" --config Release --target whisper-stream
if errorlevel 1 (
  echo [ERROR] cmake build failed
  exit /b 1
)

echo [OK] built whisper-stream.exe at %BUILD%\bin\whisper-stream.exe
exit /b 0
