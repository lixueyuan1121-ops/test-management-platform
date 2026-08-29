"""推推机器人发群消息 · 通道验证脚本(手动跑,验证 appid/secret/群id 是否配通)。

用法(secret 只从环境变量传,不写进文件/仓库):
  cd backend
  TUITUI_BOT_APPID=980916183 \
  TUITUI_BOT_SECRET=你的密钥 \
  TUITUI_BOT_GROUP=7652738368545507 \
  .venv/bin/python -m scripts.send_tuitui_test

成功会在群里收到一条测试消息,并打印 errcode=0。
"""
import json
import os
import urllib.request

appid = os.environ.get("TUITUI_BOT_APPID", "").strip()
secret = os.environ.get("TUITUI_BOT_SECRET", "").strip()
group = os.environ.get("TUITUI_BOT_GROUP", "").strip()
base = os.environ.get("TUITUI_BASE_URL", "https://alarm.im.qihoo.net").rstrip("/")

if not (appid and secret and group):
    raise SystemExit("请用环境变量传入 TUITUI_BOT_APPID / TUITUI_BOT_SECRET / TUITUI_BOT_GROUP")

url = f"{base}/message/custom/send?appid={appid}&secret={secret}"
body = {"togroups": [group], "msgtype": "text",
        "text": {"content": "🎉 测评平台 · 推推机器人通道验证\n收到即说明 appid/secret/群id 配置正确 ✅\n后续「一条龙」执行完的分步通知会发到本群。"}}
req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                            headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
        print("HTTP", resp.status)
except Exception as e:
    raise SystemExit(f"请求失败(内网域名需在 360 内网访问): {e}")

data = json.loads(raw)
print("errcode:", data.get("errcode"), "errmsg:", data.get("errmsg"))
print("msgids:", data.get("msgids"))
print("成功 ✅" if str(data.get("errcode")) == "0" else "失败 ❌(检查 secret / 机器人是否在群里 / 是否内网)")
