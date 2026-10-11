@echo off
rem Copies everything the STATION needs to the Pi (into ~/%PI_REMOTE_DIR%/): the camera
rem node, the acoustic node (station service, fusion logic, deploy + diagnostic
rem scripts) and the fake peers used by the self-test. Does not install or restart
rem anything - see station_install.bat (first time) and station_start.bat.

call "%~dp0secrets.bat"
call "%~dp0config.bat"
set REPO_ROOT=%~dp0..\..

where tar >NUL 2>&1
if errorlevel 1 (
    echo tar.exe not found - it ships with Windows 10 1803 and later.
    exit /b 1
)

echo Pushing camera_node, acoustic_node and the fake peers to %PI_USER%@%PI_HOST%:~/%PI_REMOTE_DIR%/ ...
tar -cf - --exclude=__pycache__ --exclude=*.pyc -C "%REPO_ROOT%" camera_node acoustic_node development/fake_station_peers.py | plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "mkdir -p %PI_REMOTE_DIR% && tar -xf - -C %PI_REMOTE_DIR% && chmod +x %PI_REMOTE_DIR%/acoustic_node/deploy/*.sh %PI_REMOTE_DIR%/acoustic_node/deploy/*.py"
if errorlevel 1 (
    echo Push failed - could not reach %PI_HOST% ^(is the Ethernet cable in?^) or tar failed.
    exit /b 1
)
echo Done.
