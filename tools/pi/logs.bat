@echo off
rem Shows the tile-camera service's log on the Pi (last 100 lines).
rem   logs.bat        last 100 lines, then exit
rem   logs.bat -f     keep following new lines (Ctrl+C to stop)
rem Any extra arguments are passed straight to journalctl.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -t -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "journalctl -u %PI_SERVICE% -n 100 --no-pager %*"
