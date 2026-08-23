@echo off
chcp 936 >nul
cd /d "%~dp0"

echo === 录制测评平台登录态 ===
echo.
echo 说明：稍后会弹出一个浏览器窗口，请在里面用【你自己的测评平台账号】
echo       (work.n.cn / 纳米Work) 完成登录；登录成功后，回到本窗口按【回车】保存。
echo.
set /p ACC=请输入账号名称（英文或拼音，例如 zhangsan）:
if "%ACC%"=="" (
  echo 账号名不能为空。
  pause
  exit /b 1
)
echo.
node bin\ai-eval.js login -a %ACC%
echo.
echo 若浏览器无法弹出或一闪而过，请看《部署手册.md》第七节“录制遇到问题？”（用 CDP 模式录制）。
pause
