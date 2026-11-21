@echo off
rem Drag one or more firmware .bin files onto this file. The upgraders are written next to it.
rem Uses Python (make_upgrader.py) when installed, otherwise Windows PowerShell (make_upgrader.ps1).
rem Keep this file pure ASCII: no chcp, no non-ASCII text, and no bracket blocks around the argument list.
setlocal
set "PY="
py -3 -c "" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "" >nul 2>nul && set "PY=python"
if defined PY goto usepy
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_upgrader.ps1" %*
if "%errorlevel%"=="9009" goto none
exit /b
:usepy
%PY% "%~dp0make_upgrader.py" --pause %*
exit /b
:none
echo Neither Python 3 nor Windows PowerShell was found.
pause
exit /b 1
