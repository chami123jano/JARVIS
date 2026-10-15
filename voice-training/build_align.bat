@echo off
rem Build Piper's monotonic alignment extension with the Visual Studio C++ compiler.
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
set TARGET=D:\me\piper-train\.venv\Lib\site-packages\piper\train\vits\monotonic_align
copy /Y "D:\me\piper-train\piper1-gpl\src\piper\train\vits\monotonic_align\core.pyx" "%TARGET%\core.pyx" >nul
cd /d "%TARGET%"
if not exist monotonic_align mkdir monotonic_align
"D:\me\piper-train\.venv\Scripts\cythonize.exe" -i core.pyx
if errorlevel 1 exit /b 1
move /Y core*.pyd monotonic_align\ >nul
echo built
