@echo off
rem Non-secret connection settings for the Raspberry Pi 5 station.
rem The password lives in secrets.bat (gitignored) - see secrets.bat.example.
rem No "setlocal" here on purpose: this file is meant to be called from another
rem .bat so these variables stay set in the caller's environment.

rem D-Link control LAN: DHCP reservation for MAC 88:a2:9e:49:e1:ca (verified
rem 2026-10-11, documents/electrical/network_plan.md). The Pi is ALSO on the phone
rem hotspot via Wi-Fi (172.20.10.x, a changing lease) - use this address instead.
set PI_HOST=192.168.1.11
set PI_USER=klm

rem Pinned SSH host key (ssh-ed25519), captured over the LAN on first contact
rem 2026-10-11 and not verified out of band. Passed to every plink call via
rem -hostkey so the scripts have no first-run prompt. If the Pi is re-imaged the
rem key changes: re-derive it with
rem   plink -ssh -batch -pw x %PI_USER%@%PI_HOST%
rem (it prints the new fingerprint in its rejection message) and update below.
set PI_HOSTKEY=SHA256:5xktU4J2su8byYwyFrbv7eSupcToWs2FTdey/QExjvE

rem Where pushed code lands on the Pi (relative to the user's home) and the systemd
rem unit camera_node/deploy/install_pi.sh creates. The unit does not exist until
rem install.bat has been run once.
set PI_REMOTE_DIR=Tile_Sorting
set PI_SERVICE=tile-camera

rem VNC: the Pi's built-in VNC server listens on the LAN directly (no SSH tunnel,
rem unlike the UNO Q). Log in with the Pi account user and password.
set PI_VNC_PORT=5900
set VNC_VIEWER=C:\Program Files\RealVNC\VNC Viewer\vncviewer.exe
