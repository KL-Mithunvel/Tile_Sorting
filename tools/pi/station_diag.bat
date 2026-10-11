@echo off
rem Is everything on the network up and answering the way it should? Runs
rem acoustic_node/deploy/diagnose.sh on the Pi: ping, ports, then a diagnostic
rem ping that every running program (station, camera, UNO Q feed, Nano bridge,
rem master) must answer. Read-only; cannot cause a pick.
rem   station_diag.bat
rem   station_diag.bat --require station,camera,gantry,nano

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -t -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/acoustic_node/deploy && ./diagnose.sh %*"
