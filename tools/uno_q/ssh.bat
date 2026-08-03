@echo off
rem Opens an interactive terminal on the UNO Q lab board.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -hostkey %UNO_Q_HOSTKEY% -pw %UNO_Q_PASS% %UNO_Q_USER%@%UNO_Q_HOST%
