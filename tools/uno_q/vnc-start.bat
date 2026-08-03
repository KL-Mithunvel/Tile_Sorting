
@echo off
rem Restarts TigerVNC on the UNO Q at the configured resolution (clearing any
rem stale session first - a stale lock file is what silently ignores -geometry),
rem opens an SSH tunnel (the server only binds localhost on-device), and launches
rem the local VNC viewer through it.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

echo Restarting TigerVNC on %UNO_Q_HOST% at %VNC_GEOMETRY% (display %VNC_DISPLAY%)...
plink -ssh -batch -hostkey %UNO_Q_HOSTKEY% -pw %UNO_Q_PASS% %UNO_Q_USER%@%UNO_Q_HOST% "tigervncserver -kill %VNC_DISPLAY% >/dev/null 2>&1; tigervncserver %VNC_DISPLAY% -localhost yes -geometry %VNC_GEOMETRY% -depth %VNC_DEPTH%"
if errorlevel 1 (
    echo Failed to start the VNC server on the UNO Q.
    exit /b 1
)

echo Opening SSH tunnel localhost:%VNC_PORT% -^> %UNO_Q_HOST%:%VNC_PORT% ...
start "UNO Q VNC Tunnel" plink -ssh -batch -hostkey %UNO_Q_HOSTKEY% -pw %UNO_Q_PASS% -L %VNC_PORT%:localhost:%VNC_PORT% -N %UNO_Q_USER%@%UNO_Q_HOST% ^< NUL

timeout /t 2 /nobreak >nul

echo Launching VNC Viewer...
start "" "%VNC_VIEWER%" localhost:%VNC_PORT%
