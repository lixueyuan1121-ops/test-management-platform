// 核心 key 巡检的纯函数(无 playwright 依赖,可本地单测)。
// 「核心 key」= 进入/首页/登录类 key(navHome/navTasks/homepageTitle/loginModal/loginSubmit/userMenu…),
// 单一事实源在 selectors.json 顶层 `coreKeys` 数组(runner 与后端同读同一份,见 backend selectors.core_key_set)。
// 这类 key 一旦失效,进入段自导航、复位就绪门禁、掉登录检测全塌 —— 故重点巡检、失效即告警。

// 从注册表 json 取核心 key 清单;无 coreKeys 字段 / 非数组 / json 为空 → []。
export function pickCoreKeys(json) {
  const ck = json && json.coreKeys;
  return Array.isArray(ck) ? ck : [];
}

// 给定核心 key 清单 + verifyKeys 的结果(key -> 是否可见),返回**不可见**的核心 key(保序)。
// verify 里缺某核心 key(压根没探到)也算失效 —— 巡检要的是"核心 key 是否都在页面上定位得到"。
export function failedCoreKeys(coreKeys, verify) {
  const v = verify || {};
  return (Array.isArray(coreKeys) ? coreKeys : []).filter((k) => !v[k]);
}
