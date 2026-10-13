@echo off
rem Runs the camera node on the Pi in the FOREGROUND, with this terminal showing its
rem output - for one-off checks. The tile-camera service is stopped first (two
rem programs cannot hold the camera) and started again when you quit.
rem
rem   run.bat --list-devices         which cameras does the Pi see?
rem   run.bat                        the dashboard, output here (Ctrl+C to stop)
rem   run.bat --backend usb          force the USB webcam instead of the CSI module
rem   run.bat --video data/recordings/<folder>/video.mp4 --loop
rem
rem Any arguments go straight to camera.live_dashboard. Needs the sudo password
rem piped, so it must not contain & | < > ^ or %%.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

echo Stopping %PI_SERVICE% (if it is running) ...
plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "echo %PI_PASS%| sudo -S -p '' systemctl stop %PI_SERVICE% 2>/dev/null; true"

echo Running on %PI_HOST% - dashboard at http://%PI_HOST%:5000/  (Ctrl+C to stop)
plink -ssh -t -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/camera_node/python && python3 main.py %*"

echo.
echo Starting %PI_SERVICE% again (ignored if it was never installed) ...
plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "echo %PI_PASS%| sudo -S -p '' systemctl start %PI_SERVICE% 2>/dev/null; true"
