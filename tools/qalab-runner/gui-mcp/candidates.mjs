// 与运行和导出的候选契约共用；成功读取的空候选不得用内置旧表补回。
import { normalizeCandidate, isActiveCandidate } from './runtime-loader.mjs';
export const VALID_BYS = new Set(['testid', 'xpath', 'role', 'label', 'text', 'placeholder', 'css']);
export const validCands = cands => (Array.isArray(cands) ? cands : []).map(normalizeCandidate).filter(Boolean);
export const pickCandidates = (dbCands, builtinCands) => validCands(dbCands === undefined ? builtinCands : dbCands).filter(isActiveCandidate);
