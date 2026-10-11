@echo off
rem Read-only health of the station on the Pi: services, ports, the station's own
rem view, USB topology (microphone vs camera), audio inputs, serial ports, thermal.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/acoustic_node/deploy && ./status.sh"
