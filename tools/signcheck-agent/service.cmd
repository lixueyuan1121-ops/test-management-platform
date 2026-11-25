@echo off
REM Background loop used by the "SignCheckAgent" scheduled task (see install.cmd).
REM Do not run this by hand; for a foreground run with console output use run.cmd.
setlocal
cd /d "%~dp0"
set "NODE_EXE=%~1"
if "%NODE_EXE%"=="" set "NODE_EXE=node"
if not exist logs mkdir logs

:loop
REM keep the log bounded: rotate once it passes ~5 MB
if exist logs\agent.log for %%F in (logs\agent.log) do if %%~zF gtr 5000000 move /y logs\agent.log logs\agent.log.1 >nul
echo [%date% %time%] starting agent >> logs\agent.log
"%NODE_EXE%" "%~dp0agent.mjs" >> logs\agent.log 2>&1
set "RC=%errorlevel%"
echo [%date% %time%] agent exited with code %RC% >> logs\agent.log
REM exit code 2 = config problem (.env / verifier files). Retry slowly so a fixed .env is picked up without reinstalling.
REM "timeout" does not work without a console, so wait with ping.
if "%RC%"=="2" (ping -n 61 127.0.0.1 >nul) else (ping -n 6 127.0.0.1 >nul)
goto loop
