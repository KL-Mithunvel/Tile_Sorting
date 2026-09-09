@echo off
rem Pushes pick_place_node\bench_tests\stepper_smoke_test to the UNO Q lab board
rem as an arduino-app-cli app. See that folder's README.md for the full flow.
rem
rem Usage:  push-stepper-test.bat [app_name]
rem   app_name defaults to "stepper_smoke_test".
rem
rem One-time on the board first (generates the app's manifest):
rem   arduino-app-cli app new stepper_smoke_test
rem After pushing, on the board:
rem   arduino-app-cli app restart ~/ArduinoApps/stepper_smoke_test -v
rem   arduino-app-cli monitor
rem
rem Only sketch\sketch.ino and python\main.py are pushed - the board's generated
rem app.yaml (unknown schema) is left untouched.

call "%~dp0secrets.bat"
call "%~dp0config.bat"
set REPO_ROOT=%~dp0..\..

set APP_NAME=%~1
if "%APP_NAME%"=="" set APP_NAME=stepper_smoke_test
set SRC=%REPO_ROOT%\pick_place_node\bench_tests\stepper_smoke_test
set DEST=ArduinoApps/%APP_NAME%

echo Target board: %UNO_Q_USER%@%UNO_Q_HOST%
echo Remote app dir: ~/%DEST%
echo.

echo Ensuring remote directories exist...
plink -ssh -batch -hostkey %UNO_Q_HOSTKEY% -pw %UNO_Q_PASS% %UNO_Q_USER%@%UNO_Q_HOST% "mkdir -p %DEST%/sketch %DEST%/python"
if errorlevel 1 goto :error

echo Pushing sketch\sketch.ino ...
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%SRC%\sketch\sketch.ino" %UNO_Q_USER%@%UNO_Q_HOST%:%DEST%/sketch/
if errorlevel 1 goto :error

echo Pushing python\main.py ...
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%SRC%\python\main.py" %UNO_Q_USER%@%UNO_Q_HOST%:%DEST%/python/
if errorlevel 1 goto :error

echo.
echo Done. Next, on the board (tools\uno_q\ssh.bat):
echo   arduino-app-cli app restart ~/%DEST% -v
echo   arduino-app-cli monitor
goto :eof

:error
echo.
echo Push aborted - could not reach %UNO_Q_HOST%.
echo   - is the board powered and on the laptop hotspot?  ping %UNO_Q_HOST%
echo   - has the app been created?  on the board: arduino-app-cli app new %APP_NAME%
exit /b 1
