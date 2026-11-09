"""A serial-bound Android hierarchy transport for the native dialog runner."""
import base64
import contextlib
import json
import math
import re
import subprocess
import sys
import time
from collections import OrderedDict

from lxml import etree


RECT = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
TAG = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")


def hierarchy(xml):
    root = etree.fromstring(xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    for node in root.iter("node"):
        cls = node.get("class", "")
        # Appium's Android XPath uses the class as the XML element name.
        if TAG.fullmatch(cls):
            node.tag = cls
    return root


def select_nodes(root, using, value, app_id):
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ValueError("无效定位值")
    if using == "id":
        nodes = root.xpath("//*[@resource-id=$identifier]", identifier=value)
    elif using == "accessibility id":
        nodes = root.xpath("//*[@content-desc=$identifier]", identifier=value)
    elif using == "xpath":
        nodes = root.xpath(value, smart_strings=False)
    else:
        raise ValueError("Android 原生驱动不支持该定位方式")
    if not isinstance(nodes, list) or any(not isinstance(node, etree._Element) for node in nodes):
        raise ValueError("XPath 须定位到实际控件")
    return [node for node in nodes if node.get("package") == app_id]


def rect(node, size=None):
    match = RECT.fullmatch(node.get("bounds", ""))
    if not match:
        raise ValueError("控件边界不可读")
    left, top, right, bottom = map(int, match.groups())
    if size:
        left, top = max(0, left), max(0, top)
        right, bottom = min(size[0], right), min(size[1], bottom)
    if left >= right or top >= bottom:
        raise ValueError("控件没有可操作的屏幕边界")
    return left, top, right, bottom


class Driver:
    def __init__(self, device, serial, app_id, adb):
        self.device, self.serial, self.app_id, self.adb = device, serial, app_id, adb
        self.root, self.updated, self.counter = None, 0.0, 0
        self.handles = OrderedDict()

    def tree(self, fresh=False):
        if fresh or self.root is None or time.monotonic() - self.updated > .20:
            self.root = hierarchy(self.device.dump_hierarchy(compressed=False))
            self.updated = time.monotonic()
        return self.root

    def find(self, locator):
        root = self.tree()
        nodes = select_nodes(root, locator["using"], locator["value"], self.app_id)
        handles = []
        for index, node in enumerate(nodes):
            self.counter += 1
            token = str(self.counter)
            self.handles[token] = (locator.copy(), index, len(nodes), self.identity(node), root.getroottree().getpath(node), node.get("text"))
            handles.append(token)
        while len(self.handles) > 4096:
            self.handles.popitem(last=False)
        return handles

    @staticmethod
    def identity(node):
        return tuple(node.get(k) for k in ("package","class","resource-id","content-desc"))

    def node(self, token, fresh=False):
        handle = self.handles.get(token)
        if not handle:
            raise ValueError("stale element reference")
        locator, index, count, identity, path, old_text = handle
        root = self.tree(fresh)
        nodes = select_nodes(root, locator["using"], locator["value"], self.app_id)
        if len(nodes) != count or index >= len(nodes):
            raise ValueError("stale element reference")
        node = nodes[index]
        if self.identity(node) != identity or (count > 1 and
                (root.getroottree().getpath(node) != path or node.get("text") != old_text)):
            raise ValueError("stale element reference")
        return node

    def actionable(self, token):
        if self.device.app_current().get("package") != self.app_id:
            raise ValueError("目标 App 未处于前台")
        node = self.node(token, fresh=True)
        if node.get("enabled") != "true" or node.get("visible-to-user") == "false":
            raise ValueError("控件当前不可操作")
        return node, rect(node, self.device.window_size())

    def enter_text(self, token, text):
        node, (l, t, r, b) = self.actionable(token)
        if node.get("class") != "android.widget.EditText" and node.get("editable") != "true":
            raise ValueError("所选控件不是文本编辑器")
        if node.get("focused") != "true":
            self.device.click((l+r)//2, (t+b)//2)
            node, _ = self.actionable(token)
            if node.get("focused") != "true":
                raise ValueError("无法确认文本编辑器焦点")
        args = {"packageName": self.app_id, "className": node.get("class"), "focused": True}
        if node.get("resource-id"):
            args["resourceId"] = node.get("resource-id")
        field = self.device(**args)
        if field.count != 1:
            raise ValueError("已聚焦文本编辑器不唯一")
        # The native setText accessibility action supports Chinese without pasteboard or IME changes.
        field.set_text(text)
        self.root = None

    def mobile(self, params):
        name = params["name"]
        if name in ("queryAppState","activateApp","launchApp"):
            if params.get("appId") != self.app_id:
                raise ValueError("只允许操作已绑定的应用")
            foreground = self.device.app_current().get("package") == self.app_id
            if name == "queryAppState":
                if foreground:
                    return 4
                if self.app_id in self.device.app_list_running():
                    return 3
                return 1 if self.app_id in self.device.app_list() else 0
            if not foreground:
                self.device.app_start(self.app_id, stop=False, wait=True)
            self.root = None
            return None
        if name != "scrollGesture" or params.get("direction") not in ("up", "down", "left", "right"):
            raise ValueError("不支持该 Android 手势")
        node, (l,t,r,b) = self.actionable(params["elementId"])
        if node.get("scrollable") != "true":
            raise ValueError("定位到的控件不可滚动")
        distance = params.get("percent")
        if not isinstance(distance,(int,float)) or not math.isfinite(distance) or not 0 < distance <= 1:
            raise ValueError("滚动比例无效")
        # Appium's scroll direction names the content viewport; scroll up moves the finger down.
        x, y = (l+r)//2, (t+b)//2
        dx, dy = max(1,int((r-l-2)*distance/2)), max(1,int((b-t-2)*distance/2))
        if r-l < 4 or b-t < 4:
            raise ValueError("滚动区域过小")
        gestures = {"up":(x,y-dy,x,y+dy), "down":(x,y+dy,x,y-dy),
                    "left":(x-dx,y,x+dx,y), "right":(x+dx,y,x-dx,y)}
        self.device.swipe(*gestures[params["direction"]], duration=.3)
        self.root = None
        return None

    def command(self, method, params):
        if method == "hello":
            return {"platformName":"Android", "deviceUDID":self.device.serial, "automationName":"uiautomator2-native"}
        if method == "findAll":
            return self.find(params)
        if method == "source":
            return etree.tostring(self.tree(fresh=True), encoding="unicode")
        if method == "screenshot":
            data = subprocess.run([self.adb,"-s",self.serial,"exec-out","screencap","-p"],
                                  capture_output=True, timeout=15, check=True).stdout
            if not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
                raise ValueError("设备截图不是 PNG")
            return base64.b64encode(data).decode()
        if method == "mobile":
            return self.mobile(params)
        if method in ("clear","type"):
            return self.enter_text(params["id"], "" if method == "clear" else params["text"])
        if method == "click":
            _, (l,t,r,b) = self.actionable(params["id"])
            self.device.click((l+r)//2,(t+b)//2)
            self.root = None
            return None
        node = self.node(params["id"])
        if method == "text":
            return node.get("text","")
        if method == "attribute":
            name = {"resourceId":"resource-id","contentDescription":"content-desc","className":"class"}.get(params["name"],params["name"])
            return node.get(name)
        if method == "enabled":
            return node.get("enabled") == "true"
        if method == "displayed":
            try:
                rect(node, self.device.window_size())
            except ValueError:
                return False
            return node.get("visible-to-user") != "false"
        raise ValueError("未知 Android 驱动命令")


def main():
    serial, app_id, adb = sys.argv[1:]
    driver = None
    for line in sys.stdin:
        request_id = None
        try:
            req = json.loads(line)
            request_id = req["id"]
            with contextlib.redirect_stdout(sys.stderr):
                if driver is None:
                    import uiautomator2 as u2
                    device = u2.connect(serial)
                    if device.serial != serial:
                        raise ValueError("实际设备序列号不匹配")
                    device.jsonrpc.setConfigurator({"waitForIdleTimeout":300, "waitForSelectorTimeout":500})
                    driver = Driver(device, serial, app_id, adb)
                value = driver.command(req["method"], req["params"])
            message = {"id": request_id, "value":value}
        except Exception as exc:
            # Never echo an XML tree, RPC arguments, message text or account details in platform logs.
            if isinstance(exc, ImportError):
                error = "MOBILE_ANDROID_PYTHON 缺少依赖 uiautomator2/lxml"
            elif isinstance(exc, ValueError) and not isinstance(exc, etree.XPathError):
                error = str(exc)[:150]
            else:
                error = "Android 真机驱动错误 " + type(exc).__name__
            message = {"id":request_id, "error":error}
        print(json.dumps(message, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
