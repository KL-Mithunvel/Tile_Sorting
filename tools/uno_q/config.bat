@echo off
rem Non-secret connection settings for the lab UNO Q board.
rem The SSH password lives in secrets.bat (gitignored) - see secrets.bat.example.
rem No "setlocal" here on purpose: this file is meant to be called from another
rem .bat so these variables stay set in the caller's environment.

set UNO_Q_HOST=172.20.10.2
set UNO_Q_USER=arduino

rem Pinned host key fingerprint (ssh-ed25519, captured 2026-07-28). Passed explicitly
rem to every plink/pscp call via -hostkey instead of relying on PuTTY's registry
rem cache, so these scripts work the same on any machine with no first-run prompt.
rem NOTE: 172.20.10.2 was previously a different device (rpi4b-weather) on this
rem hotspot - if the UNO Q's key ever legitimately changes, re-derive this with:
rem   plink -ssh -batch -pw ^<pw^> %UNO_Q_USER%@%UNO_Q_HOST%
rem (it will print the new fingerprint in its rejection message) and update below.
set UNO_Q_HOSTKEY=SHA256:jZ7xEhuuDcoOrubahQ8cpFpX4R0BeDJzLZRila2/0Z8

rem Where pushed code lands on the device (relative to arduino's home dir)
set UNO_Q_REMOTE_DIR=tile_sorting

rem TigerVNC settings
set VNC_DISPLAY=:1
set VNC_GEOMETRY=1280x720
set VNC_DEPTH=24
set VNC_PORT=5901

rem Local VNC viewer executable
set VNC_VIEWER=C:\Program Files\RealVNC\VNC Viewer\vncviewer.exe
