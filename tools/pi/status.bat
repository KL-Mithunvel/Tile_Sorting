@echo off
rem One-screen health check of the Pi: service state, temperature/throttling,
rem addresses, routes, disk and memory. Read-only.

call "%~dp0secrets.bat"
call "%~dp0config.bat"

plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "echo '--- service'; systemctl is-active %PI_SERVICE% 2>&1; systemctl status %PI_SERVICE% --no-pager -n 5 2>&1 | head -12; echo '--- thermal (throttled=0x0 is clean)'; vcgencmd measure_temp; vcgencmd get_throttled; echo '--- network'; ip -br addr | grep -E '^(eth0|wlan0)'; ip route | grep default; echo '--- disk / memory'; df -h / | tail -1; free -h | head -2; echo '--- camera'; (rpicam-hello --list-cameras 2>&1 || libcamera-hello --list-cameras 2>&1) | head -4"
