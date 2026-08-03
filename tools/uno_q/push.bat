@echo off
rem Pushes this repo's source code to the UNO Q lab board over pscp.
rem File list is hand-maintained (mirrors the existing weather_daq tooling
rem convention) - extend it as more of the project's code needs to run on-device.

call "%~dp0secrets.bat"
call "%~dp0config.bat"
set REPO_ROOT=%~dp0..\..

echo Ensuring remote directories exist...
plink -ssh -batch -hostkey %UNO_Q_HOSTKEY% -pw %UNO_Q_PASS% %UNO_Q_USER%@%UNO_Q_HOST% "mkdir -p %UNO_Q_REMOTE_DIR%/acoustic_node/python/acoustic %UNO_Q_REMOTE_DIR%/tests"
if errorlevel 1 goto :error

echo Pushing acoustic_node\python\acoustic\ ...
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%REPO_ROOT%\acoustic_node\python\acoustic\*.py" %UNO_Q_USER%@%UNO_Q_HOST%:%UNO_Q_REMOTE_DIR%/acoustic_node/python/acoustic/
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%REPO_ROOT%\acoustic_node\python\acoustic\config.yaml" %UNO_Q_USER%@%UNO_Q_HOST%:%UNO_Q_REMOTE_DIR%/acoustic_node/python/acoustic/

echo Pushing tests\ ...
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%REPO_ROOT%\tests\*.py" %UNO_Q_USER%@%UNO_Q_HOST%:%UNO_Q_REMOTE_DIR%/tests/

echo Pushing requirements.txt and pytest.ini ...
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%REPO_ROOT%\requirements.txt" %UNO_Q_USER%@%UNO_Q_HOST%:%UNO_Q_REMOTE_DIR%/
pscp -hostkey %UNO_Q_HOSTKEY% -l %UNO_Q_USER% -pw %UNO_Q_PASS% "%REPO_ROOT%\pytest.ini" %UNO_Q_USER%@%UNO_Q_HOST%:%UNO_Q_REMOTE_DIR%/

echo Done.
goto :eof

:error
echo Push aborted - could not reach %UNO_Q_HOST% or create remote directories.
exit /b 1
