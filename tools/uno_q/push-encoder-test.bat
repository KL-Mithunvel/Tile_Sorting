@echo off
rem Pushes pick_place_node\bench_tests\encoder_smoke_test to the UNO Q lab board
rem as an arduino-app-cli app. See that folder's README.md for the full flow.
rem
rem Usage:  push-encoder-test.bat [app_name]
rem   app_name defaults to "encoder_smoke_test".
rem
rem One-time on the board first (generates the app's manifest):
rem   arduino-app-cli app new encoder_smoke_test
rem   then edit ~/ArduinoApps/encoder_smoke_test/app.yaml so ports lists 5071
rem After pushing, on the board:
rem   arduino-app-cli app restart ~/ArduinoApps/encoder_smoke_test -v
rem
rem Pushes sketch\sketch.ino and the whole python\ folder (recursively, so
rem python\templates\, config.yaml and requirements.txt come along too).
rem The board's generated app.yaml is left untouched.

call "%~dp0secrets.bat"
call "%~dp0config.bat"
set REPO_ROOT=%~dp0..\..

set APP_NAME=%~1
if "%APP_NAME%"=="" set APP_NAME=encoder_smoke_test
set SRC=%REPO_ROOT%\pick_place_node\bench_tests\encoder_smoke_test
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

echo Pushing python\ (recursively) ...
pscp -r -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%SRC%\python\*" %UNO_Q_USER%@%UNO_Q_HOST%:%DEST%/python/
if errorlevel 1 goto :error

echo.
echo Done. Next, on the board (tools\uno_q\ssh.bat):
echo   arduino-app-cli app restart ~/%DEST% -v
echo Then open http://%UNO_Q_HOST%:5071/ from a browser on the same network.
goto :eof

:error
echo.
echo Push aborted - could not reach %UNO_Q_HOST%.
echo   - is the board powered and reachable?  ping %UNO_Q_HOST%
echo   - has the app been created?  on the board: arduino-app-cli app new %APP_NAME%
exit /b 1
