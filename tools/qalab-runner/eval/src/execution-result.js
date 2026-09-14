'use strict';

// 驱动器的 errorMessage 可能已经带错误码；回写平台时统一只保留一次。
function formatFailureReason(result) {
  if (result.success) return null;
  let message = String(result.errorMessage || result.completeReason || '').trim();
  if (!result.errorCode) return message || null;
  const prefix = `[${result.errorCode}]`;
  while (message.startsWith(prefix)) message = message.slice(prefix.length).trimStart();
  return message ? `${prefix} ${message}` : prefix;
}

module.exports = { formatFailureReason };
