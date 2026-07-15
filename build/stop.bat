@echo off
cd /d "%~dp0"
echo Stopping Streamlit...
REM 按端口杀：Streamlit 无窗口运行，靠窗口标题杀不掉，只能按占用 8501 的 PID 杀
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1

echo Stopping PostgreSQL...
set "PGCTL="
set "PGDATA_DIR="

REM 优先使用本地路径
if exist "pgsql\bin\pg_ctl.exe" (
    set "PGCTL=%~dp0pgsql\bin\pg_ctl.exe"
)
if exist "%~dp0pgdata\PG_VERSION" (
    set "PGDATA_DIR=%~dp0pgdata"
)

REM 中文安装路径回退：检查 %LOCALAPPDATA%\ResumeAssistantPG
if "%PGCTL%"=="" (
    if exist "%LOCALAPPDATA%\ResumeAssistantPG\pgsql\bin\pg_ctl.exe" (
        set "PGCTL=%LOCALAPPDATA%\ResumeAssistantPG\pgsql\bin\pg_ctl.exe"
    )
)
if "%PGDATA_DIR%"=="" (
    if exist "%LOCALAPPDATA%\ResumeAssistantPG\pgdata\PG_VERSION" (
        set "PGDATA_DIR=%LOCALAPPDATA%\ResumeAssistantPG\pgdata"
    )
)

if defined PGCTL if defined PGDATA_DIR (
    "%PGCTL%" stop -D "%PGDATA_DIR%" -m fast >nul 2>&1
)

REM 清理 junction
if exist "%LOCALAPPDATA%\ResumeAssistantPG\pgsql" (
    rmdir "%LOCALAPPDATA%\ResumeAssistantPG\pgsql" >nul 2>&1
)

echo All services stopped.
