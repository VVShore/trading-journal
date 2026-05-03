@echo off
echo Launching TradingView in Chrome debug mode...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome-debug" --no-first-run --no-default-browser-check https://www.tradingview.com/chart/
echo.
echo Open your journal in Edge or Firefox at http://127.0.0.1:8000
echo Wait for TradingView to fully load before clicking Read Chart.
pause
