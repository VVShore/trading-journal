@echo off
:: Launch TradingView Desktop with Chrome DevTools Protocol enabled
:: Required for the chart_bridge.js to connect

echo Launching TradingView with debug port 9222...

:: Try common installation paths
set TV_PATHS[0]=%LOCALAPPDATA%\Programs\TradingView\TradingView.exe
set TV_PATHS[1]=%LOCALAPPDATA%\TradingView\TradingView.exe
set TV_PATHS[2]=%PROGRAMFILES%\TradingView\TradingView.exe

:: Check WindowsApps (Microsoft Store version)
for /f "delims=" %%i in ('dir "%LOCALAPPDATA%\Microsoft\WindowsApps\TradingView*.exe" /b /s 2^>nul') do (
    set TV_STORE=%%i
)

if defined TV_STORE (
    echo Found TradingView at: %TV_STORE%
    start "" "%TV_STORE%" --remote-debugging-port=9222
    echo TradingView launched with debug port 9222.
    echo You can now use the "Read Chart" button in your journal.
    pause
    exit /b 0
)

:: Try other paths
for %%P in ("%LOCALAPPDATA%\Programs\TradingView\TradingView.exe" "%LOCALAPPDATA%\TradingView\TradingView.exe") do (
    if exist %%P (
        echo Found TradingView at: %%P
        start "" %%P --remote-debugging-port=9222
        echo TradingView launched with debug port 9222.
        echo You can now use the "Read Chart" button in your journal.
        pause
        exit /b 0
    )
)

echo.
echo Could not auto-detect TradingView location.
echo Please right-click TradingView in your Start menu, select "Open file location",
echo then drag TradingView.exe here and press Enter:
echo.
set /p TV_EXE="Path to TradingView.exe: "
if exist "%TV_EXE%" (
    start "" "%TV_EXE%" --remote-debugging-port=9222
    echo Launched! Update this script with the correct path for next time.
) else (
    echo File not found. Check the path and try again.
)
pause
