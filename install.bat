@echo off
setlocal
rem ------------------------------------------------------------------
rem  Ticket tracker - Windows installer. Double-click, or run:
rem    install.bat               install (or update) and start
rem    install.bat uninstall     stop it and remove auto-start (keeps your config)
rem    install.bat port 9000     use another dashboard port (default 8765)
rem  No admin rights needed. Starts at login from your Startup folder.
rem ------------------------------------------------------------------

set "APP=doomsday-tracker"
set "PORT=8765"
set "MODE=install"
set "SRC=%~dp0"
if defined TRACKER_HOME (set "DATA=%TRACKER_HOME%") else (set "DATA=%LOCALAPPDATA%\%APP%")
set "LOG=%DATA%\tracker.log"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LAUNCHER=%STARTUP%\Ticket Tracker.cmd"

:args
if "%~1"=="" goto argsdone
if /i "%~1"=="uninstall"  set "MODE=uninstall"
if /i "%~1"=="-uninstall" set "MODE=uninstall"
if /i "%~1"=="/uninstall" set "MODE=uninstall"
if /i "%~1"=="port"  set "PORT=%~2"
if /i "%~1"=="-port" set "PORT=%~2"
shift
goto args
:argsdone

rem stop a running copy (whatever is listening on the dashboard port)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do taskkill /pid %%p /f >nul 2>&1
rem remove the task an older install.ps1 may have created
schtasks /delete /tn "Ticket Tracker (%APP%)" /f >nul 2>&1

if /i "%MODE%"=="uninstall" goto uninstall

rem --- find Python 3.8+ (py launcher from python.org, or python on PATH)
set "PY="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY goto nopython
"%PY%" -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)" || goto nopython
rem pythonw.exe = the same Python without a console window
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"

rem --- copy the app next to its data
if not exist "%DATA%" mkdir "%DATA%"
for %%f in (district_tracker.py tracker_server.py seatmap.py district_api.py tracker_ui.html) do (
  copy /y "%SRC%%%f" "%DATA%\" >nul || goto copyfail
)
echo Installed app files to: %DATA%

rem --- start at login: a small launcher in the Startup folder
> "%LAUNCHER%" echo @echo off
>> "%LAUNCHER%" echo start "" "%PYW%" "%DATA%\tracker_server.py" --port %PORT% --log "%LOG%"
echo Auto-start at login: %LAUNCHER%

rem --- start it now and open the dashboard
start "" "%PYW%" "%DATA%\tracker_server.py" --port %PORT% --log "%LOG%"
echo Started. Log: %LOG%
echo.
echo Dashboard: http://127.0.0.1:%PORT%
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:%PORT%"
set "RC=0"
goto done

:uninstall
if exist "%LAUNCHER%" del "%LAUNCHER%"
echo Stopped and removed auto-start. Your config and data are still in: %DATA%
set "RC=0"
goto done

:nopython
echo Python 3.8+ was not found.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run install.bat again.
set "RC=1"
goto done

:copyfail
echo Could not copy the app files to %DATA%.
set "RC=1"
goto done

:done
rem keep the window open when double-clicked so you can read the result
echo %CMDCMDLINE% | find /i "/c" >nul && pause
exit /b %RC%
