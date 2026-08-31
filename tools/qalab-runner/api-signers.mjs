// api-signers —— 被测系统「客户端内动态签名」在执行机侧的确定性复现。
// 背景:namisoa/claw 等系统每个请求的 access-token/sign/timestamp/signnonce 由客户端
// JS 拦截器现算(见 docs/api-testcase-user-guide.md 适用边界)。过去无法在客户端外复现 →
// api 用例只能改判 gui/e2e。现按已知算法(Postman pre-request 脚本)在此复现,放开 api 执行。
//
// 用 Node 内置 crypto(HmacSHA256 / MD5),不依赖 CryptoJS。
// 每个签名器是纯函数:入参 (req, cfg, clock) → 返回要「合并进请求」的 {query, headers}。
// clock 可注入(测试确定性);默认用真实时钟(每请求现算,与客户端一致)。

import { createHmac, createHash } from "node:crypto";

// 默认时钟:与 Postman 脚本一致 —— timestamp=ISO 串,signnonce=`${now}${now+100000}`。
function defaultClock() {
  const now = Date.now();
  return { timestamp: new Date(now).toISOString(), signnonce: `${now}${now + 100000}` };
}

// per-platform 凭据:cfg.credentials[platform] = {access_token, secret_token}。
// 未按平台配则回落 cfg.access_token / cfg.secret_token(单平台简写)。
function pickCredential(cfg, platform) {
  const byPlat = (cfg.credentials && cfg.credentials[platform]) || null;
  return {
    access_token: (byPlat && byPlat.access_token) || cfg.access_token || "",
    secret_token: (byPlat && byPlat.secret_token) || cfg.secret_token || "",
  };
}

// app 端签名(android/iOS/HarmonyOS):镜像 Postman signApp()。
// 追加 11 个签名 query 参数;signVal 按固定字典序拼接;sign = HmacSHA256(signVal, mid) hex。
function signApp(cfg, clock) {
  const { timestamp, signnonce } = clock;
  const platform = cfg.device_platform;
  const { access_token } = pickCredential(cfg, platform);
  const p = {
    device_platform: platform,
    app_version: cfg.app_version,
    mid: cfg.mid,
    format: cfg.format || "JSON",
    signnonce,
    sign_method: cfg.sign_method || "SHA256",
    sign_version: cfg.sign_version || "2.0.0",
    timestamp,
    api_version: cfg.api_version,
    channel: cfg.channel,
    app_name: cfg.app_name,
  };
  // signVal:字典序(access-token,api_version,app_name,app_version,device_platform,
  // format,mid,sign_method,sign_version,signnonce,timestamp)。顺序与客户端严格一致,勿改。
  const signVal =
    `access-token=${access_token}` +
    `&api_version=${p.api_version}` +
    `&app_name=${p.app_name}` +
    `&app_version=${p.app_version}` +
    `&device_platform=${p.device_platform}` +
    `&format=${p.format}` +
    `&mid=${p.mid}` +
    `&sign_method=${p.sign_method}` +
    `&sign_version=${p.sign_version}` +
    `&signnonce=${p.signnonce}` +
    `&timestamp=${p.timestamp}`;
  const sign = createHmac("sha256", String(cfg.mid)).update(signVal).digest("hex");

  const headers = {
    sign,
    "device-platform": platform,
    "access-token": access_token,
  };
  // 额外固定头(Auth-Token/Sid/Cookie 等):由 cfg.headers 原样带上。
  Object.assign(headers, cfg.headers || {});
  return { query: p, headers, _signVal: signVal };
}

// web 端签名(Web/H5/Applet/PC):镜像 Postman signWeb()。
// zmToken = MD5(devicePlatform+timestamp+zmVer+accessToken+ua)。zmVer 1.3/1.4 追加 path,1.4 追加 body。
function signWeb(cfg, clock, req) {
  const { timestamp } = clock;
  const platform = cfg.device_platform;
  const { access_token } = pickCredential(cfg, platform);
  const zmVer = cfg.zm_ver || "1.1";
  const ua = cfg.ua || "";
  let signStr = `${platform}${timestamp}${zmVer}${access_token}${ua}`;
  if (zmVer === "1.3" || zmVer === "1.4") {
    const path = String((req && req.path) || "").replace(/^\//, "");
    signStr += "/" + path;
  }
  if (zmVer === "1.4" && req && req.body !== undefined) {
    signStr += typeof req.body === "string" ? req.body : JSON.stringify(req.body);
  }
  const zmToken = createHash("md5").update(signStr).digest("hex");
  const headers = {
    "zm-token": zmToken,
    timestamp,
    "zm-ver": zmVer,
    "sec-ver": cfg.sec_ver || "20211123",
    "device-platform": platform,
    "access-token": access_token,
  };
  Object.assign(headers, cfg.headers || {});
  return { query: {}, headers, _signStr: signStr };
}

const WEB_PLATFORMS = new Set(["Web", "H5", "Applet", "PC"]);
const APP_PLATFORMS = new Set(["android", "iOS", "HarmonyOS"]);

// 命名签名方案注册表。auth.scheme 选方案;目前 "namisoa"(纳米Work/安全龙虾)。
// 每个方案:(cfg, req, clock) → {query, headers}。执行器把返回的 query/headers 合并进请求后再发。
export const SIGNERS = {
  namisoa(cfg, req, clock = defaultClock()) {
    const platform = cfg.device_platform;
    if (APP_PLATFORMS.has(platform)) return signApp(cfg, clock);
    if (WEB_PLATFORMS.has(platform)) return signWeb(cfg, clock, req);
    // 未知平台:不签名,只带固定头(避免整批 fail;客户端 default 分支也是不签)。
    return { query: {}, headers: { ...(cfg.headers || {}) } };
  },
};

// 对外主入口:按 auth 配置为单个请求算签名。
// auth 形状:{scheme, device_platform, credentials?/access_token, mid, app_version,
//   api_version, channel, app_name, zm_ver?, sec_ver?, headers?, ...}。
// 返回 {query, headers};scheme 不认识 → 返回空(退化为无签名,由执行器决定是否失败)。
export function computeSignature(auth, req, clock) {
  if (!auth || typeof auth !== "object") return { query: {}, headers: {} };
  const scheme = auth.scheme || "namisoa";
  const signer = SIGNERS[scheme];
  if (!signer) return { query: {}, headers: {}, _unknownScheme: scheme };
  return signer(auth, req || {}, clock);
}
