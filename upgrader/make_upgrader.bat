@echo off
chcp 65001 >nul
rem 把固件 .bin 拖到本文件上，即可在 .bin 旁边生成 <名称>_升级.exe
if "%~1"=="" (
  echo 请把固件 .bin 文件拖到 make_upgrader.bat 上。
  pause
  exit /b 1
)
:next
if "%~1"=="" goto done
python "%~dp0make_upgrader.py" "%~1"
shift
goto next
:done
pause
