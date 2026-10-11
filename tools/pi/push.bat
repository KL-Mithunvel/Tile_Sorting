@echo off
rem Copies camera_node\ to the Pi (into ~/%PI_REMOTE_DIR%/camera_node) over SSH,
rem skipping __pycache__ / .pyc. Streams a tar through plink, so there is no
rem hand-maintained file list to forget to extend.
rem
rem This does NOT install or restart anything - see install.bat (first time) and
rem deploy.bat (push + restart).

call "%~dp0secrets.bat"
call "%~dp0config.bat"
set REPO_ROOT=%~dp0..\..

where tar >NUL 2>&1
if errorlevel 1 (
    echo tar.exe not found - it ships with Windows 10 1803 and later.
    exit /b 1
)

echo Pushing camera_node\ to %PI_USER%@%PI_HOST%:~/%PI_REMOTE_DIR%/ ...
tar -cf - --exclude=__pycache__ --exclude=*.pyc -C "%REPO_ROOT%" camera_node | plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST% "mkdir -p %PI_REMOTE_DIR% && tar -xf - -C %PI_REMOTE_DIR%"
if errorlevel 1 (
    echo Push failed - could not reach %PI_HOST% ^(is the Ethernet cable in?^) or tar failed.
    exit /b 1
)

echo Done.
