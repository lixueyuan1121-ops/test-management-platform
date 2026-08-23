@echo off
chcp 936 >nul
cd /d "%~dp0"

echo === 纳米 Work 桌面客户端 · 对话并发验证 ===
echo.
echo 说明：本脚本会自动【关闭并带调试端口重启】已安装的纳米 Work 桌面客户端，
echo       复用它当前登录的账号；在同一窗口内【新建任务连发多条对话】形成并发，
echo       再轮流切换查看各任务、自动诊断是否串台（问A答B / 回答互相污染 / 定位错位等），
echo       并抓取分享链接/耗时/算力豆/正文，回填飞书表格（可用下方选项跳过回填）。
echo       诊断异常会截图存档，报告在 output 目录下 diagnostics_时间戳.html。
echo.
echo 注意：重启客户端会关闭当前客户端窗口，请先保存客户端里正在进行的工作。
echo       若你已【手动带调试端口】开着客户端、不想让脚本重启它，请改用「桌面并发验证-连接已开客户端.bat」。
echo.
set /p NUM=请输入并发条数（同一窗口内同时连发几条对话，直接回车默认 3）:
if "%NUM%"=="" set NUM=3
set /p LIM=最多跑多少条用例（直接回车=全部读到的用例）:
set "LIMIT_ARG="
if not "%LIM%"=="" set "LIMIT_ARG=--limit %LIM%"
set /p WB=是否回填飞书表格？回车=回填；输入 n = 不回填（只跑对话+诊断）:
set "WB_ARG="
if /i "%WB%"=="n" set "WB_ARG=--skip-writeback"
call _对话选项.bat
echo.
node bin\ai-eval.js desktop -p %NUM% %LIMIT_ARG% %WB_ARG% %DIALOG_ARGS%
echo.
echo 执行结束。诊断报告与结果见 output\diagnostics_*.html 及 output\ 下的 JSON。
pause
