// src/contracts.ts
var ACTIVE_STATES = /* @__PURE__ */ new Set(["queued", "compiling", "running"]);
function validateLimits(timeMs, memoryMiB, caseCount) {
  if (!Number.isFinite(timeMs) || timeMs < 100 || timeMs > 6e4) {
    return "\u65F6\u95F4\u9650\u5236\u5FC5\u987B\u5728 0.1\u201360 \u79D2\u4E4B\u95F4";
  }
  if (!Number.isInteger(memoryMiB) || memoryMiB < 16 || memoryMiB > 256) {
    return "\u5185\u5B58\u9650\u5236\u5FC5\u987B\u5728 16\u2013256 MiB \u4E4B\u95F4";
  }
  if (!Number.isInteger(caseCount) || caseCount < 1 || caseCount > 20) {
    return "\u8F93\u5165\u7EC4\u6570\u5FC5\u987B\u5728 1\u201320 \u4E4B\u95F4";
  }
  return null;
}
export {
  ACTIVE_STATES,
  validateLimits
};
