@echo off
rem Opens an interactive terminal on the Raspberry Pi 5 station.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST%
