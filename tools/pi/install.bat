@echo off
rem FIRST-TIME install of the camera node on the Pi: pushes the code, then runs
rem camera_node/deploy/install_pi.sh there (apt dependencies + the tile-camera
rem systemd unit). Pass --with-grading to also install onnxruntime.
rem
rem NEVER RUN YET - install_pi.sh was written 2026-09-20 from documentation and has
rem not met a real Pi. Expect to fix package names. It is interactive (sudo will
rem ask for the account password), so this opens a terminal, not a batch run.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

call "%~dp0push.bat"
if errorlevel 1 exit /b 1

echo.
echo Running install_pi.sh on %PI_HOST% - enter the password when sudo asks.
plink -ssh -t -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/camera_node/deploy && chmod +x install_pi.sh && ./install_pi.sh %*"
