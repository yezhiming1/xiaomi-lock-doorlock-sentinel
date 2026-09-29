const test = require("node:test");
const assert = require("node:assert/strict");
const labels = require("../src/doorlock_sentinel/static/ui-labels.js");

test("all downloader states are presented in Chinese", () => {
  assert.deepEqual(
    ["discovered", "retrying", "downloaded", "failed"].map(labels.stateLabel),
    ["已发现", "正在重试", "已下载", "下载失败"],
  );
});

test("unknown and empty downloader states never expose a raw value", () => {
  assert.equal(labels.stateLabel("failed_again"), "未知下载状态");
  assert.equal(labels.stateLabel("constructor"), "未知下载状态");
  assert.equal(labels.stateLabel(""), "未知下载状态");
  assert.equal(labels.stateLabel(null), "未知下载状态");
});

test("known download errors use Chinese explanations", () => {
  assert.equal(labels.errorLabel("SEGMENT_FETCH_FAILED"), "录像片段获取失败");
  assert.equal(labels.errorLabel("PLAYLIST_DECODE_FAILED"), "录像播放清单处理失败");
  assert.equal(labels.errorLabel("CLOUD_SESSION_UNAVAILABLE"), "小米云会话不可用，请核对登录状态");
  assert.equal(labels.errorLabel("FFPROBE_TIMEOUT"), "录像校验超时");
});

test("empty error values are Chinese and unknown codes are not echoed", () => {
  assert.equal(labels.errorLabel(null), "无");
  assert.equal(labels.errorLabel("  "), "无");
  assert.equal(labels.errorLabel("none"), "无");
  const unknownCode = "UNKNOWN_PRIVATE_CODE";
  const message = labels.errorLabel(unknownCode);
  assert.equal(message, "暂未收录的下载问题");
  assert.equal(message.includes(unknownCode), false);
});
