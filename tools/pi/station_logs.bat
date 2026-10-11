@echo off
rem Shows the station service's log on the Pi (last 100 lines).
rem   station_logs.bat        last 100 lines, then exit
rem   station_logs.bat -f     follow (Ctrl+C to stop)

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -t -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "journalctl -u tile-acoustic -n 100 --no-pager %*"
