@echo off
rem Stop the line: belt to 0 %%, striker inhibited, station holding every tile.
rem   station_stop.bat              leave the services running (held)
rem   station_stop.bat --services   also stop the camera and station services

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/acoustic_node/deploy && echo %PI_PASS%| ./system.sh --sudo-stdin stop %*"
