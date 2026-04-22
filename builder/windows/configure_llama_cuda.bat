@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1

cd /d "C:\HGR App v1.0.0\llama.cpp"
set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"
set "PATH=%CUDA_PATH%\bin;%PATH%"
set "NINJA=C:\HGR App v1.0.0\.venv\Scripts\ninja.exe"

if exist build_cuda rmdir /s /q build_cuda

cmake -B build_cuda -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DCMAKE_MAKE_PROGRAM="%NINJA%" ^
  -DCMAKE_CUDA_COMPILER="%CUDA_PATH%\bin\nvcc.exe" ^
  -DCUDAToolkit_ROOT="%CUDA_PATH%" ^
  -DGGML_CUDA=ON ^
  -DLLAMA_BUILD_TESTS=OFF ^
  -DLLAMA_BUILD_EXAMPLES=OFF
exit /b %errorlevel%
