@echo off
rem Passes catr's exit code through. Each run ends in "exit /b %ERRORLEVEL%" outside any
rem ( ) block: inside a block, %ERRORLEVEL% is expanded before the command runs.
rem Python: CATR_PYTHON if set, else the py launcher (py -3).
setlocal
if defined CATR_PYTHON goto environment
where py >nul 2>nul
if not errorlevel 1 goto launcher
echo Python launcher (py) not found. Install Python 3 from python.org, or set CATR_PYTHON to a native Windows python.exe. 1>&2
exit /b 1

:launcher
py -3 -I "%~dp0catr.py" %*
exit /b %ERRORLEVEL%

:environment
"%CATR_PYTHON%" -I "%~dp0catr.py" %*
exit /b %ERRORLEVEL%
