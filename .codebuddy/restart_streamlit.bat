@echo off
setlocal
cd /d "d:\Users\pc\Documents\walker\CodeBuddy\招聘网站助手"

echo [restart] Cleaning old Streamlit and BOSS WebSocket processes...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R ":8501 .*LISTENING"') do taskkill /PID %%p /F >nul 2>nul
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R ":8765 .*LISTENING"') do taskkill /PID %%p /F >nul 2>nul
for /f "skip=1 tokens=2 delims==" %%p in ('wmic process where "CommandLine like '%%run_streamlit.py%%'" get ProcessId /VALUE 2^>nul') do if not "%%p"=="" taskkill /PID %%p /F >nul 2>nul

timeout /t 2 /nobreak >nul

echo [restart] Starting Streamlit...
python scripts\run_streamlit.py
