@echo off
rem Software self-test of the station ON the Pi against the real broker, with a
rem synthetic microphone and fake camera / striker / UNO Q. Proves broker +
rem service + ledger + final grade + record + acknowledgements end to end.
rem It stops tile-acoustic while it runs and restarts it afterwards.
rem   station_test.bat        30 seconds
rem   station_test.bat 60     longer

call "%~dp0secrets.bat"
call "%~dp0config.bat"
set SECS=%1
if "%SECS%"=="" set SECS=30

call "%~dp0station_push.bat"
if errorlevel 1 exit /b 1

echo.
plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/acoustic_node/deploy && echo %PI_PASS%| ./selftest.sh --sudo-stdin %SECS%"
