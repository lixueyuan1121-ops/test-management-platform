"""macOS Keychain adapter. Tokens never pass through argv or a plaintext file."""
import ctypes as C
import hashlib
import json
import sys
from contextlib import contextmanager


class MacKeychain:
    def __init__(self, origin, suffix=""):
        if sys.platform != "darwin":
            raise RuntimeError("钥匙串登录助手仅支持 macOS；Windows 请使用 qalab.ps1")
        self.service = "qalab-requirement-test:" + hashlib.sha256(origin.encode()).hexdigest()[:24] + suffix
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sec = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
        signatures = {
            "CFStringCreateWithCString": ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            "CFDataCreate": ([C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
            "CFDictionaryCreateMutable": ([C.c_void_p, C.c_long, C.c_void_p, C.c_void_p], C.c_void_p),
            "CFDictionarySetValue": ([C.c_void_p, C.c_void_p, C.c_void_p], None),
            "CFRelease": ([C.c_void_p], None),
            "CFDataGetLength": ([C.c_void_p], C.c_long),
            "CFDataGetBytePtr": ([C.c_void_p], C.c_void_p),
            "CFGetTypeID": ([C.c_void_p], C.c_ulong),
            "CFDataGetTypeID": ([], C.c_ulong),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.cf, name); fn.argtypes = args; fn.restype = result
        for name, args in {
            "SecItemCopyMatching": [C.c_void_p, C.POINTER(C.c_void_p)],
            "SecItemAdd": [C.c_void_p, C.POINTER(C.c_void_p)],
            "SecItemUpdate": [C.c_void_p, C.c_void_p],
            "SecItemDelete": [C.c_void_p],
        }.items():
            fn = getattr(self.sec, name); fn.argtypes = args; fn.restype = C.c_int32

    def symbol(self, name):
        lib = self.cf if name.startswith("kCF") else self.sec
        return C.c_void_p.in_dll(lib, name).value

    @contextmanager
    def dictionary(self, values):
        # NULL callbacks: all allocated CF values stay alive until the API returns.
        dictionary = self.cf.CFDictionaryCreateMutable(None, 0, None, None)
        if not dictionary:
            raise RuntimeError("无法分配钥匙串查询")
        owned = []
        try:
            for key, value in values.items():
                if isinstance(value, tuple):
                    ref = self.symbol(value[0])
                elif isinstance(value, bytes):
                    buffer = C.create_string_buffer(value)
                    ref = self.cf.CFDataCreate(None, buffer, len(value)); owned.append(ref)
                else:
                    ref = self.cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100); owned.append(ref)
                if not ref:
                    raise RuntimeError("无法分配钥匙串数据")
                self.cf.CFDictionarySetValue(dictionary, self.symbol(key), ref)
            yield dictionary
        finally:
            self.cf.CFRelease(dictionary)
            for ref in owned:
                if ref: self.cf.CFRelease(ref)

    def query(self):
        return {"kSecClass": ("kSecClassGenericPassword",), "kSecAttrService": self.service,
                "kSecAttrAccount": "session"}

    @staticmethod
    def check(status):
        if status:
            raise RuntimeError(f"钥匙串操作失败（OSStatus {status}）；请解锁登录钥匙串并检查授权，不会改用明文保存")

    def load(self):
        query = {**self.query(), "kSecReturnData": ("kCFBooleanTrue",), "kSecMatchLimit": ("kSecMatchLimitOne",)}
        result = C.c_void_p()
        with self.dictionary(query) as ref:
            status = self.sec.SecItemCopyMatching(ref, C.byref(result))
        if status == -25300:
            return None
        self.check(status)
        if not result.value:
            raise RuntimeError("钥匙串返回空数据")
        try:
            if self.cf.CFGetTypeID(result) != self.cf.CFDataGetTypeID():
                raise RuntimeError("钥匙串会话格式异常")
            data = C.string_at(self.cf.CFDataGetBytePtr(result), self.cf.CFDataGetLength(result))
            return json.loads(data.decode("utf-8"))
        finally:
            self.cf.CFRelease(result)

    def save(self, session):
        safe = {k: session[k] for k in ("access_token", "refresh_token") if session.get(k)}
        if not safe.get("access_token"):
            raise RuntimeError("登录响应缺少 access_token")
        values = {"kSecValueData": json.dumps(safe).encode("utf-8")}
        with self.dictionary(self.query()) as query, self.dictionary(values) as data:
            status = self.sec.SecItemUpdate(query, data)
        if status == -25300:
            with self.dictionary({**self.query(), **values}) as data:
                status = self.sec.SecItemAdd(data, None)
        self.check(status)

    def delete(self):
        with self.dictionary(self.query()) as query:
            status = self.sec.SecItemDelete(query)
        if status != -25300:
            self.check(status)
