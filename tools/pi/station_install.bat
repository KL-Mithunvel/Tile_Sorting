@echo off
rem FIRST-TIME install of the station on the Pi: pushes the code, then runs
rem acoustic_node/deploy/install_pi.sh there (apt packages, the MQTT broker
rem mosquitto, the tile-acoustic systemd unit, and the --mqtt drop-in for
rem tile-camera). Install the camera node first (install.bat) so the drop-in
rem can be added; otherwise run this again afterwards.
rem
rem NEVER RUN YET. Interactive: sudo asks for the account password.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

call "%~dp0station_push.bat"
if errorlevel 1 exit /b 1

echo.
echo Running acoustic_node/deploy/install_pi.sh on %PI_HOST% - enter the password when sudo asks.
plink -ssh -t -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/acoustic_node/deploy && chmod +x install_pi.sh && ./install_pi.sh"
