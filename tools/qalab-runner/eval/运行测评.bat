@echo off
chcp 936 >nul
cd /d "%~dp0"

echo === 开始运行测评 ===
echo 【无头模式运行，运行中不会弹出浏览器窗口，请耐心等待】
echo.
echo 接下来可选择本次运行使用的 模型 / 对话模式 / 深度思考（都可直接回车跳过，用页面默认）。
call _对话选项.bat
echo.
node bin\ai-eval.js run %DIALOG_ARGS%
echo.
echo 执行结束：结果已按条实时回填到表格；本地备份见 output 目录。
pause
