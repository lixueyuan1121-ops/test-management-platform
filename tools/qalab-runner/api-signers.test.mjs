import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac, createHash } from "node:crypto";
import { computeSignature, SIGNERS } from "./api-signers.mjs";
import { run } from "./api-executor.mjs";

// 固定时钟:让签名可复现、可断言(真实运行时每请求现算)。
const CLOCK = { timestamp: "2025-02-14T14:30:49.390Z", signnonce: "17000000000001700000100000" };

const APP_AUTH = {
  scheme: "namisoa",
  device_platform: "android",
  access_token: "3456773a-bb4b-c1c9-1d98-6a85f15a7761",
  secret_token: "d2831eac-93f6-dc5d-c107-a41f643e5e78",
  mid: "aaaabbbbcccc6",
  app_version: "4.14.3",
  api_version: "20211122",
  channel: "10001",
  app_name: "com.qihoo.namisoa",
  format: "JSON",
  sign_method: "SHA256",
  sign_version: "2.0.0",
  headers: { "Auth-Token": "jwt-xxx", Sid: "bbb" },
};

test("signApp: 追加 11 个签名 query,sign=HmacSHA256(signVal, mid)", () => {
  const sig = computeSignature(APP_AUTH, { method: "GET", path: "/api/x" }, CLOCK);
  // query 覆盖 Postman signApp 追加的全部字段
  for (const k of ["device_platform", "app_version", "mid", "format", "signnonce",
    "sign_method", "sign_version", "timestamp", "api_version", "channel", "app_name"]) {
    assert.ok(k in sig.query, `缺 query ${k}`);
  }
  assert.equal(sig.query.device_platform, "android");
  assert.equal(sig.query.timestamp, CLOCK.timestamp);
  assert.equal(sig.query.signnonce, CLOCK.signnonce);
  // 头:sign + device-platform + access-token + 固定头
  assert.equal(sig.headers["device-platform"], "android");
  assert.equal(sig.headers["access-token"], APP_AUTH.access_token);
  assert.equal(sig.headers["Auth-Token"], "jwt-xxx");
  assert.equal(sig.headers.Sid, "bbb");
  // signVal 字典序精确复现,并用 mid 做 HMAC 密钥(与 Postman 一致)
  const signVal =
    `access-token=${APP_AUTH.access_token}&api_version=20211122&app_name=com.qihoo.namisoa` +
    `&app_version=4.14.3&device_platform=android&format=JSON&mid=aaaabbbbcccc6` +
    `&sign_method=SHA256&sign_version=2.0.0&signnonce=${CLOCK.signnonce}&timestamp=${CLOCK.timestamp}`;
  assert.equal(sig._signVal, signVal);
  const expect = createHmac("sha256", "aaaabbbbcccc6").update(signVal).digest("hex");
  assert.equal(sig.headers.sign, expect);
});

test("signApp: iOS/HarmonyOS 用各自平台凭据(credentials 映射)", () => {
  const auth = {
    ...APP_AUTH, access_token: undefined, secret_token: undefined,
    device_platform: "iOS",
    credentials: {
      iOS: { access_token: "e3ec6cdb-9172-9f62-db44-38d5d242136e", secret_token: "dbbba05a" },
      HarmonyOS: { access_token: "10344314-f191-2f7d-1479-beaf1219d4a6", secret_token: "6d09a890" },
    },
  };
  const sig = computeSignature(auth, {}, CLOCK);
  assert.equal(sig.headers["access-token"], "e3ec6cdb-9172-9f62-db44-38d5d242136e");
  assert.match(sig._signVal, /access-token=e3ec6cdb/);
});

test("signWeb: zm-token=MD5(...),1.1 不含 path/body", () => {
  const auth = { scheme: "namisoa", device_platform: "Web", access_token: "AT", zm_ver: "1.1", ua: "UA" };
  const sig = computeSignature(auth, { path: "/api/x", body: { a: 1 } }, CLOCK);
  const signStr = `Web${CLOCK.timestamp}1.1ATUA`;
  assert.equal(sig.headers["zm-token"], createHash("md5").update(signStr).digest("hex"));
  assert.equal(sig.headers["zm-ver"], "1.1");
  assert.equal(sig.headers.timestamp, CLOCK.timestamp);
});

test("signWeb: 1.4 追加 path 与 body", () => {
  const auth = { scheme: "namisoa", device_platform: "Web", access_token: "AT", zm_ver: "1.4", ua: "" };
  const sig = computeSignature(auth, { path: "/api/x/y", body: { a: 1 } }, CLOCK);
  const signStr = `Web${CLOCK.timestamp}1.4AT` + "/api/x/y" + JSON.stringify({ a: 1 });
  assert.equal(sig.headers["zm-token"], createHash("md5").update(signStr).digest("hex"));
});

test("computeSignature: 未知 scheme 返回空签名(不抛)", () => {
  const sig = computeSignature({ scheme: "nope", device_platform: "android" }, {});
  assert.deepEqual(sig.query, {});
  assert.equal(sig._unknownScheme, "nope");
});

test("run: auth_type=sign 时每请求注入签名 query + 头", async () => {
  const calls = [];
  const fetchImpl = async (url, opts) => {
    const u = new URL(url);
    calls.push({ url, path: u.pathname, search: u.search, headers: opts.headers });
    return { status: 200, json: async () => ({ code: 0, data: { id: 1 } }) };
  };
  const script = [{ name: "查询", request: { method: "GET", path: "/api/me" },
    asserts: [{ type: "jsonpath", path: "code", op: "eq", value: 0 }] }];
  const r = await run(script, { base_url: "https://svc", auth_type: "sign", auth: APP_AUTH }, () => {}, fetchImpl);
  assert.equal(r.verdict, "pass", r.reason);
  assert.ok("sign" in calls[0].headers, "请求头应含 sign");
  assert.equal(calls[0].headers["access-token"], APP_AUTH.access_token);
  assert.match(calls[0].search, /device_platform=android/);
  assert.match(calls[0].search, /app_name=com\.qihoo\.namisoa/);
});

test("SIGNERS 注册表暴露 namisoa", () => {
  assert.equal(typeof SIGNERS.namisoa, "function");
});
