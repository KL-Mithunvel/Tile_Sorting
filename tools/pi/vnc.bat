@echo off
rem Opens the Raspberry Pi 5's desktop in the local VNC viewer.
rem The Pi's VNC server is already running and reachable on the LAN, so this just
rem launches the viewer - nothing is started or restarted on the Pi.
rem Log in with the Pi account user (see config.bat) and its password.

call "%~dp0config.bat"

if not exist "%VNC_VIEWER%" (
    echo VNC viewer not found at "%VNC_VIEWER%".
    echo Install RealVNC Viewer or edit VNC_VIEWER in tools\pi\config.bat.
    exit /b 1
)

echo Connecting to %PI_HOST%:%PI_VNC_PORT% as %PI_USER% ...
start "" "%VNC_VIEWER%" %PI_HOST%:%PI_VNC_PORT%
