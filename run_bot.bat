@echo off
rem tg-llm-bot launcher: auto-restart loop + log rotation (run via Startup folder or double-click)
setlocal
rem force UTF-8 logs regardless of how stderr/stdout are redirected
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

rem rotate logs when larger than 20 MB (skip missing files)
for %%F in ("%~dp0bot.err.log") do if exist "%%~F" if %%~zF GTR 20971520 move /y "%%~F" "%%~F.old" >nul 2>&1
for %%F in ("%~dp0bot.out.log") do if exist "%%~F" if %%~zF GTR 20971520 move /y "%%~F" "%%~F.old" >nul 2>&1

:loop
"%~dp0venv\Scripts\python.exe" -u -m bot 1>> "%~dp0bot.out.log" 2>> "%~dp0bot.err.log"
rem Do NOT use `timeout` here: in a hidden window with no console input it can
rem hang forever, which wedges this restart loop after a crash. ping is a safe sleep.
ping -n 11 127.0.0.1 >nul
goto loop
