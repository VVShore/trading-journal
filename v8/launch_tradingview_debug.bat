@echo off
:: Launch TradingView with debug port for chart_bridge.js
echo Launching TradingView with debug port 9222...

:: Microsoft Store version (most common on Windows 11)
for /f "delims=" %%i in ('dir "%LOCALAPPDATA%\Microsoft\WindowsApps\TradingView*.exe" /b /s 2^>nul') do (
    start "" "%%i" --remote-debugging-port=9222
    echo Launched via Microsoft Store path.
    echo TradingView will open shortly. Use "Read Chart" in the journal once it loads.
    timeout /t 3 /nobreak >nul
    exit /b 0
)

:: Direct install paths
for %%P in (
    "%LOCALAPPDATA%\Programs\TradingView\TradingView.exe"
    "%LOCALAPPDATA%\TradingView\TradingView.exe"
    "%PROGRAMFILES%\TradingView\TradingView.exe"
) do (
    if exist %%P (
        start "" %%P --remote-debugging-port=9222
        echo Launched from: %%P
        exit /b 0
    )
)

echo.
echo Could not auto-detect TradingView.
echo Drag TradingView.exe here and press Enter:
set /p TV_EXE="Path: "
if exist "%TV_EXE%" (
    start "" "%TV_EXE%" --remote-debugging-port=9222
    echo Launched successfully.
) else (
    echo Not found. Check path.
)
pause
