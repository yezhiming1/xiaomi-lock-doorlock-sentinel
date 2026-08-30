import assert from "node:assert/strict";
import test from "node:test";
import {buildSystemText, renderOutboxMessage} from "../src/cards.js";

test("analysis failure contains only actionable operational fields", () => {
  const result = buildSystemText("system.analysis_failed", {
    file_name: "sample.mp4",
    attempts: 4,
    error_code: "model_unavailable",
  });
  assert.match(result, /分析持续失败/);
  assert.match(result, /4 次/);
  assert.doesNotMatch(result, /token|cookie|password/i);
});

test("download failure points to the management console", () => {
  const result = renderOutboxMessage("system.download_failed", {
    event_time: "2026-08-29T00:00:00Z",
    attempts: 3,
    error_code: "cloud_timeout",
  }) as any;
  assert.equal(result.msgtype, "markdown");
  assert.match(result.markdown.content, /下载持续失败/);
  assert.match(result.markdown.content, /门锁观察簿/);
});
