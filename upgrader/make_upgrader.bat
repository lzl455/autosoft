@echo off
rem Drag one or more firmware .bin files onto this file.
rem Keep this file pure ASCII: no chcp, no non-ASCII text (cmd misreads them and closes).
setlocal
set "PY="
py -3 -c "" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "" >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% "%~dp0make_upgrader.py" --pause %*
exit /b
:nopython
echo Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1
