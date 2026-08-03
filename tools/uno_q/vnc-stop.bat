@echo off
rem Closes the SSH tunnel window and kills the remote TigerVNC session.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

echo Closing SSH tunnel window...
taskkill /FI "WINDOWTITLE eq UNO Q VNC Tunnel*" /T /F >nul 2>&1

echo Killing remote VNC session (display %VNC_DISPLAY%)...
plink -ssh -batch -hostkey %UNO_Q_HOSTKEY% -pw %UNO_Q_PASS% %UNO_Q_USER%@%UNO_Q_HOST% "tigervncserver -kill %VNC_DISPLAY%"
