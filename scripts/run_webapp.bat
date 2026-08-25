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

REM Resolve ONE Python and use it for everything below (the install check,
REM the auto-install, and the final launch). Two different ways this goes
REM wrong otherwise, both confirmed live:
REM   1. A machine with more than one Python installed (e.g. python.org +
REM      Microsoft Store) -- a bare "streamlit"/"pip" command on PATH can
REM      silently resolve to a DIFFERENT install than whichever one you
REM      last ran "pip install" with.
REM   2. A venv activated in a VS Code terminal (".venv" folder here) is
REM      only active in THAT terminal. Double-clicking this .bat from
REM      Explorer opens a plain new terminal that has never heard of it and
REM      falls back to the system Python -- which never had the package
REM      installed into it, even though "pip install" appeared to succeed
REM      earlier in the VS Code terminal. This is what actually happened
REM      the first time this script was used for real.
REM A project-local ".venv" next to this script (created by VS Code, or by
REM "python -m venv .venv") is therefore preferred whenever it exists --
REM using it explicitly by path sidesteps both problems, since it does not
REM depend on activation or on PATH order at all.
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] No ".venv" here and "python" was not found on PATH either.
        echo         Install Python from python.org ^(check "Add python.exe to
        echo         PATH" during install^), then run this script again.
        pause
        goto :eof
    )
    set "PYTHON_EXE=python"
)
echo Using Python: %PYTHON_EXE%
echo Checking the install -- this can take several seconds on the first run
echo of the day, especially with antivirus scanning every file Python opens.
echo Nothing wrong if this window looks quiet for a bit; wait for the next line.

"%PYTHON_EXE%" -c "import compression_tool" >nul 2>nul
if errorlevel 1 (
    echo compression_tool is not installed yet for this Python -- installing now.
    echo ^(This only happens once per Python install, or after pulling code that
    echo   changes a dependency.^)
    echo.
    "%PYTHON_EXE%" -m pip install -e ".[webapp]"
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
echo Starting the app now -- again, a quiet gap of several seconds here is
echo normal before the "Local URL" / "Network URL" lines appear below. Do NOT
echo close this window or press Ctrl+C while you are waiting for them.
echo.

"%PYTHON_EXE%" -m streamlit run compression_tool\webapp\app.py --server.address 0.0.0.0 --server.port 8501
echo.
echo [The app has stopped. If you did not close this window yourself, scroll
echo  up to see why -- an error would be printed above this line.]
pause

endlocal
