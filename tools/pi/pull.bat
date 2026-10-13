@echo off
setlocal enabledelayedexpansion
rem Copies the data the Pi collected back to this laptop, into data\pi\ :
rem
rem   data\pi\recordings\<date_time>\video.mp4 + meta.json   (dashboard Record button)
rem   data\pi\camera_captures\tile_<seq>_<time>.jpg          (a photo of each graded tile)
rem
rem Only recordings you do not already have are downloaded (an unfinished one is
rem fetched again once it is complete). Nothing is deleted on the Pi.
rem
rem   pull.bat              recordings + tile photos
rem   pull.bat --captures   tile photos only (small, quick)
rem
rem Next step: python development\export_tile_dataset.py --videos data\pi\recordings
rem then python development\annotate_tiles.py   (see tools\pi\README.md)

call "%~dp0secrets.bat"
call "%~dp0config.bat"
set REPO_ROOT=%~dp0..\..
set OUT=%REPO_ROOT%\data\pi
set REMOTE_DATA=%PI_REMOTE_DIR%/data
set SSH=plink -ssh -batch -hostkey %PI_HOSTKEY% -pw %PI_PASS% %PI_USER%@%PI_HOST%

rem Windows' own tar, by full path: Git for Windows puts a GNU tar earlier on PATH
rem that reads "C:\..." as host:path and fails.
set TAR=%SystemRoot%\System32\tar.exe
if not exist "%TAR%" (
    echo %TAR% not found - it ships with Windows 10 1803 and later.
    exit /b 1
)
if not exist "%OUT%\recordings" mkdir "%OUT%\recordings"
if not exist "%OUT%\camera_captures" mkdir "%OUT%\camera_captures"

echo Tile photos from %PI_HOST% ...
%SSH% "cd %REMOTE_DATA% 2>/dev/null && [ -d camera_captures ] && tar -cf - camera_captures || true" | "%TAR%" -xf - -C "%OUT%"
if errorlevel 1 (
    echo Could not reach %PI_HOST% ^(is the Ethernet cable in?^).
    exit /b 1
)
if /i "%~1"=="--captures" goto done

echo Recordings ...
set /a GOT=0
set /a HAVE=0
for /f "delims=" %%D in ('%SSH% "ls -1 %REMOTE_DATA%/recordings 2>/dev/null"') do (
    set NEED=1
    if exist "%OUT%\recordings\%%D\meta.json" (
        findstr /C:"complete" "%OUT%\recordings\%%D\meta.json" >NUL 2>&1 && set NEED=0
    )
    if "!NEED!"=="1" (
        echo   downloading %%D
        %SSH% "cd %REMOTE_DATA% && tar -cf - recordings/%%D" | "%TAR%" -xf - -C "%OUT%"
        set /a GOT+=1
    ) else (
        set /a HAVE+=1
    )
)
echo   !GOT! downloaded, !HAVE! already here.

:done
echo.
echo Saved under %OUT%
echo Next:  python development\export_tile_dataset.py --videos data\pi\recordings
echo        python development\annotate_tiles.py
