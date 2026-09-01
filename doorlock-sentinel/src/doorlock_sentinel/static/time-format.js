(function exposeDoorlockTime(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DoorlockTime = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createDoorlockTime() {
  "use strict";

  const BEIJING_TIME_ZONE = "Asia/Shanghai";
  const DAY_MS = 86400000;

  function asDate(value) {
    if (!value) return null;
    const date = value instanceof Date ? value : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatter(options) {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: BEIJING_TIME_ZONE,
      ...options,
    });
  }

  function formatDate(value, full = false) {
    const date = asDate(value);
    if (!date) return "—";
    const options = full
      ? {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23",
        }
      : { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
    return formatter(options).format(date);
  }

  function calendarDayIndex(date) {
    const parts = formatter({
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const values = Object.fromEntries(
      parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]),
    );
    return Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day)) / DAY_MS;
  }

  function dayLabel(value, nowValue = new Date()) {
    const date = asDate(value);
    const now = asDate(nowValue);
    if (!date || !now) return "时间未知";
    const days = calendarDayIndex(now) - calendarDayIndex(date);
    if (days === 0) return "今天";
    if (days === 1) return "昨天";
    return formatter({ month: "2-digit", day: "2-digit" }).format(date);
  }

  return { BEIJING_TIME_ZONE, dayLabel, formatDate };
});
