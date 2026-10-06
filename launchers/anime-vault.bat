@echo off
rem anime-vault launcher. Copies of this file live in the collection folders as download.bat, distribute.bat;
rem the command is taken from the file name. The collection root is the nearest folder upwards that has
rem anime-paths.json; the project path is its "project" field. Keep this file ASCII-only (cmd reads it before chcp).
chcp 65001 >nul
setlocal
set "COMMAND=%~n0"
set "DIR=%~dp0"

:find
if exist "%DIR%anime-paths.json" goto found
for %%I in ("%DIR%..") do set "UP=%%~fI\"
if /I "%UP%"=="%DIR%" goto notfound
set "DIR=%UP%"
goto find

:notfound
echo [ERROR] anime-paths.json not found in this folder or above.
echo         Fix: this .bat must be inside the collection folder (where anime-paths.json is).
pause
exit /b 1

:found
set "PROJECT="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Content -Raw -Encoding UTF8 '%DIR%anime-paths.json' | ConvertFrom-Json).project"`) do set "PROJECT=%%P"
if not exist "%PROJECT%\venv\Scripts\python.exe" (
    echo [ERROR] anime-vault project not found: "%PROJECT%"
    echo         Fix: check "project" in %DIR%anime-paths.json and create the project venv:
    echo         py -3.12 -m venv venv  then  venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
set "PYTHONPATH=%PROJECT%"
"%PROJECT%\venv\Scripts\python.exe" -m anime_vault %COMMAND% --root "%DIR%."
