@echo off
setlocal
cd /d "%~dp0"
REM Use existing .env; skip self-update while testing local code.
node runner.mjs %*
exit /b %errorlevel%
