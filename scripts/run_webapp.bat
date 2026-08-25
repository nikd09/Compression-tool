@echo off
REM Launches the Compression Tool web app so colleagues on the same
REM corporate network/VPN can reach it at http://<this-PC's-name>:8501 --
REM no VS Code, no Python install, on their end. Just double-click this file.
REM
REM Portable to a different host PC: nothing here is specific to this
REM machine except COMPRESSION_TOOL_WORKSPACE (set once, see below), and
REM that is read from the environment, never hardcoded in this script or in
REM the app's source -- copy this repo to another PC, set the same
REM environment variable there, and it works identically.
REM
REM One-time setup on whichever PC hosts this:
REM   1. Windows Search -> "Edit environment variables for your account"
REM   2. New user variable:
REM        Name:  COMPRESSION_TOOL_WORKSPACE
REM        Value: the shared folder's path, e.g.
REM               C:\Users\<you>\OneDrive - Saint-Gobain\Compression Testing
REM   3. Close and reopen this window (or just re-run this script) so the
REM      new variable is picked up.
REM
REM NOT for exposing this app to the public internet (no ngrok, no tunnel,
REM no port-forwarding through a home/public router) -- this proprietary
REM test data must stay inside the corporate network. There is no login on
REM this app yet: anyone who can reach the address below can read and write
REM every workspace it can see, so only run this on a network you trust.

setlocal

cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] "python" was not found on PATH. Install Python from
    echo         python.org ^(check "Add python.exe to PATH" during install^),
    echo         then run this script again.
    pause
    goto :eof
)

REM Always launch via "python -m streamlit", never a bare "streamlit" command.
REM A bare "streamlit" resolves through PATH independently of "python" and
REM can silently be a DIFFERENT Python installation's copy -- on a machine
REM with more than one Python around (python.org install + Microsoft Store
REM install is a common combination), that copy never received the
REM "pip install -e" below, and the app fails with
REM "ModuleNotFoundError: No module named 'compression_tool'" the moment it
REM starts, even though the install appeared to succeed. Routing everything
REM through the one "python" found above makes that class of mismatch
REM impossible: whatever Python installs the package is the one that runs it.
python -c "import compression_tool" >nul 2>nul
if errorlevel 1 (
    echo compression_tool is not installed yet for this Python -- installing now.
    echo ^(This only happens once per Python install, or after pulling code that
    echo   changes a dependency.^)
    echo.
    python -m pip install -e ".[webapp]"
    if errorlevel 1 (
        echo.
        echo [ERROR] Install failed -- see the error above.
        pause
        goto :eof
    )
    echo.
)

if "%COMPRESSION_TOOL_WORKSPACE%"=="" (
    echo [WARNING] COMPRESSION_TOOL_WORKSPACE is not set on this PC.
    echo           The app will default to .\data next to this script instead
    echo           of the shared folder -- see the setup steps above this line.
    echo.
)

echo Workspace : %COMPRESSION_TOOL_WORKSPACE%
echo This PC's name (share this with colleagues as http://%COMPUTERNAME%:8501):
echo   %COMPUTERNAME%
echo If that name does not resolve for a colleague, run "ipconfig" in another
echo window and share this PC's IPv4 address instead: http://^<that-IP^>:8501
echo.
echo Leave this window open while colleagues are using the tool. Closing it
echo (or this PC sleeping / losing network) takes the app down for everyone.
echo.

python -m streamlit run compression_tool\webapp\app.py --server.address 0.0.0.0 --server.port 8501

endlocal
