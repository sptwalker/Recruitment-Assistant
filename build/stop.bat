@echo off
cd /d "%~dp0"
echo Stopping Streamlit...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *streamlit*" >nul 2>&1
echo Stopping PostgreSQL...
if exist "pgsql\bin\pg_ctl.exe" (
    set PGDATA=%~dp0pgdata
    "pgsql\bin\pg_ctl.exe" stop -D "%~dp0pgdata" -m fast >nul 2>&1
)
echo All services stopped.
pause
