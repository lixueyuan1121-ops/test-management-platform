@echo off
chcp 65001 >nul
cd /d "%~dp0"
title perf-agent 性能任务执行机
node perf-agent.mjs %*
