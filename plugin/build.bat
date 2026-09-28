@echo off
rem Builds RLVid.dll against the BakkesMod SDK that BakkesMod installs in %APPDATA%.
rem Needs the MSVC C++ build tools (VS 2019/2022 or Build Tools).
setlocal
set "SDK=%APPDATA%\bakkesmod\bakkesmod\bakkesmodsdk"
set "OUT=%~dp0build"

rem Use the first Visual Studio / Build Tools install that actually has vcvars64.bat.
for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -all -products * -property installationPath`) do (
  if not defined VCVARS if exist "%%i\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%%i\VC\Auxiliary\Build\vcvars64.bat"
)
if not defined VCVARS (
  echo No Visual Studio with C++ build tools found.
  exit /b 1
)
call "%VCVARS%" >nul 2>nul || exit /b 1

if not exist "%OUT%" mkdir "%OUT%"
cl /nologo /LD /MD /O2 /EHsc /std:c++20 /W3 /DNDEBUG ^
  /I "%SDK%\include" "%~dp0RLVid.cpp" ^
  /Fo"%OUT%\\" /Fe"%OUT%\RLVid.dll" ^
  /link "%SDK%\lib\pluginsdk.lib" || exit /b 1
echo Built %OUT%\RLVid.dll
