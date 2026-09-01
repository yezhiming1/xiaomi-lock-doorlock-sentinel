import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import test from "node:test";

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const {BEIJING_TIME_ZONE, dayLabel, formatDate} = require(
  resolve(here, "../../../src/doorlock_sentinel/static/time-format.js"),
);

test("event timestamps are always rendered in Beijing time", () => {
  const event = "2026-08-31T16:30:00Z";

  assert.equal(BEIJING_TIME_ZONE, "Asia/Shanghai");
  assert.match(formatDate(event), /00:30/);
  assert.match(formatDate(event, true), /09.*01.*00:30:00/);
});

test("today and yesterday use Beijing calendar boundaries", () => {
  const now = "2026-08-31T17:00:00Z";

  assert.equal(dayLabel("2026-08-31T16:30:00Z", now), "今天");
  assert.equal(dayLabel("2026-08-31T15:30:00Z", now), "昨天");
  assert.equal(dayLabel("not-a-time", now), "时间未知");
});
