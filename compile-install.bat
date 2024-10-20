@echo off
REM Check if paramiko is installed
pip show paramiko >nul 2>&1
IF ERRORLEVEL 1 (
    echo Installing paramiko...
    pip install paramiko
    IF ERRORLEVEL 1 (
        echo Error: Could not install paramiko. Exiting.
        exit /b 1
    )
)

REM Check if pyinstaller is installed
pip show pyinstaller >nul 2>&1
IF ERRORLEVEL 1 (
    echo Installing pyinstaller...
    pip install pyinstaller
    IF ERRORLEVEL 1 (
        echo Error: Could not install pyinstaller. Exiting.
        exit /b 1
    )
)

REM Prompt to restart the script if installations occurred
pip show paramiko >nul 2>&1 && pip show pyinstaller >nul 2>&1
IF NOT "%ERRORLEVEL%" == "0" (
    echo Please restart this script to ensure the installations take effect.
    pause
    exit /b 0
)

REM Compile the Python script using pyinstaller and the provided spec file
echo Compiling Python script...
pyinstaller --clean --noconfirm portmaster-install.spec
IF ERRORLEVEL 1 (
    echo Error: Could not compile Python script. Exiting.
    exit /b 1
)

REM Create the dist/client directory if it doesn't exist
if not exist ".\dist\client" (
    mkdir ".\dist\client"
)

REM Copy deploy.sh to dist directory
copy ".\deploy.sh" ".\dist\"

REM Copy the portmaster directory to dist
xcopy ".\portmaster" ".\dist\portmaster" /E /I /Y

REM Copy specific files from ./client to dist/client
copy ".\client\portmaster-client.ps1" ".\dist\client\"
copy ".\client\portmaster.conf" ".\dist\client\"
copy ".\client\run_client.bat" ".\dist\client\"

echo Done. Files copied and Python script compiled successfully.
pause
