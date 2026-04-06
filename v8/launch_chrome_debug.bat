@echo off
echo Launching Chrome with remote debugging...

taskkill /f /im chrome.exe >nul 2>&1

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
--remote-debugging-port=9222 ^
--user-data-dir="C:\temp\chrome-debug" ^
https://www.tradingview.com/chart

echo Chrome launched with debugging enabled on port 9222.
pause