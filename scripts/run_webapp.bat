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

cd /d "%~dp0.."
streamlit run compression_tool\webapp\app.py --server.address 0.0.0.0 --server.port 8501

endlocal
