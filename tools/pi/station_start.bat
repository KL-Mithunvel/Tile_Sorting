@echo off
rem Bring the whole line up IN SYNC: broker -> station (held) -> camera -> verify
rem every node answers -> release the hold and (optionally) start the belt.
rem If any node does not answer, the line stays held and nothing is sorted.
rem   station_start.bat                  full system
rem   station_start.bat --no-unoq        bench: the UNO Q feed is not running
rem   station_start.bat --belt 40        also set the belt to 40 %% once verified
rem   station_start.bat --with-nano      also require the Nano's serial link
rem The password is piped to sudo -S, so it must not contain & | < > ^ or %%.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "cd %PI_REMOTE_DIR%/acoustic_node/deploy && echo %PI_PASS%| ./system.sh --sudo-stdin start %*"
