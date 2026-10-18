@echo off
rem Pushes pick_place_node (sketch + python) to the UNO Q lab board as the
rem arduino-app-cli app "pick_place_node". The board's generated app.yaml is left untouched.
rem
rem Usage:  push-pick-place.bat [app_name]          (default app_name: pick_place_node)
rem   Set UNO_Q_HOST_OVERRIDE to use another address (e.g. the hotspot) without editing config.bat.
rem
rem One-time on the board first:   arduino-app-cli app new pick_place_node
rem After pushing, on the board:   arduino-app-cli app restart ~/ArduinoApps/pick_place_node -v
rem   (this builds and FLASHES sketch\sketch.ino - the real gantry firmware - and starts
rem    python\main.py, the gantry station, on the "bridge" backend.)
rem Stop other apps first (gantry_fw, encoder/io/stepper smoke tests): they share the MCU.
rem Not pushed on purpose: templates/, static/ (the twin dashboard is not part of the station).

call "%~dp0secrets.bat"
call "%~dp0config.bat"
if defined UNO_Q_HOST_OVERRIDE set UNO_Q_HOST=%UNO_Q_HOST_OVERRIDE%
set REPO_ROOT=%~dp0..\..

set APP_NAME=%~1
if "%APP_NAME%"=="" set APP_NAME=pick_place_node
set SRC=%REPO_ROOT%\pick_place_node
set DEST=ArduinoApps/%APP_NAME%

echo Target board: %UNO_Q_USER%@%UNO_Q_HOST%
echo Remote app dir: ~/%DEST%
echo.

plink -ssh -batch -hostkey %UNO_Q_HOSTKEY% -pw %UNO_Q_PASS% %UNO_Q_USER%@%UNO_Q_HOST% "mkdir -p %DEST%/sketch %DEST%/python/pick_place"
if errorlevel 1 goto :error

echo Pushing sketch ...
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%SRC%\sketch\sketch.ino" "%SRC%\sketch\gantry_core.h" %UNO_Q_USER%@%UNO_Q_HOST%:%DEST%/sketch/
if errorlevel 1 goto :error

echo Pushing python ...
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%SRC%\python\main.py" "%SRC%\python\requirements.txt" %UNO_Q_USER%@%UNO_Q_HOST%:%DEST%/python/
if errorlevel 1 goto :error
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%SRC%\python\pick_place\*.py" "%SRC%\python\pick_place\config.yaml" %UNO_Q_USER%@%UNO_Q_HOST%:%DEST%/python/pick_place/
if errorlevel 1 goto :error

echo.
echo Done. Next, on the board (tools\uno_q\ssh.bat):
echo   arduino-app-cli app restart ~/%DEST% -v
goto :eof

:error
echo.
echo Push aborted - could not reach %UNO_Q_HOST%.
exit /b 1
