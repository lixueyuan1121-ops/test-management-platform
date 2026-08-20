import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";

export const ndjson = (p) => (existsSync(p)
  ? readFileSync(p, "utf8").split(/\r?\n/).filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
  : []);

// 按 metric 分组抽稀，每组最多 keep 点（等距），回传前压体积。
export function decimate(samples, keep = 2000) {
  const byMetric = new Map();
  for (const s of samples) {
    if (!byMetric.has(s.metric)) byMetric.set(s.metric, []);
    byMetric.get(s.metric).push(s);
  }
  const out = [];
  for (const arr of byMetric.values()) {
    if (arr.length <= keep) { out.push(...arr); continue; }
    const step = arr.length / keep;
    for (let i = 0; i < keep; i++) out.push(arr[Math.floor(i * step)]);
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

export function listSessionDirs(sessionsDir) {
  if (!existsSync(sessionsDir)) return [];
  return readdirSync(sessionsDir).map((d) => join(sessionsDir, d))
    .filter((p) => { try { return statSync(p).isDirectory(); } catch { return false; } });
}

export function readSession(dir) {
  const metaPath = join(dir, "meta.json");
  if (!existsSync(metaPath)) return null;
  const meta = JSON.parse(readFileSync(metaPath, "utf8"));
  return { dir, meta, samples: decimate(ndjson(join(dir, "samples.ndjson"))), events: ndjson(join(dir, "events.ndjson")) };
}
