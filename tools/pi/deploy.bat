@echo off
rem Everyday update: push camera_node\ to the Pi, then restart the tile-camera
rem service so it picks the new code up. Needs install.bat to have been run once.
rem
rem The password is piped to "sudo -S", so it must not contain & | < > ^ or %%.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

call "%~dp0push.bat"
if errorlevel 1 exit /b 1

echo Restarting %PI_SERVICE% on %PI_HOST% ...
plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "echo %PI_PASS%| sudo -S -p '' systemctl restart %PI_SERVICE% && sleep 3 && systemctl is-active %PI_SERVICE%"
if errorlevel 1 (
    echo.
    echo Restart failed. If the unit does not exist yet, run install.bat first.
    echo Otherwise run logs.bat to see why the service is not starting.
    exit /b 1
)

echo Done. Dashboard: http://%PI_HOST%:5000/
