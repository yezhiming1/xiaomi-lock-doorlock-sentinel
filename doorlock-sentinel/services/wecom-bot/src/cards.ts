function text(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function time(value: unknown): string {
  if (!value) return "时间未知";
  const parsed = new Date(String(value));
  return Number.isNaN(parsed.valueOf()) ? "时间未知" : parsed.toLocaleString("zh-CN", {hour12: false});
}

export function buildSystemText(topic: string, payload: Record<string, unknown>): string {
  if (topic === "system.analysis_failed") {
    return [
      "# 门锁录像分析持续失败",
      `文件：${text(payload.file_name)}`,
      `自动尝试：${text(payload.attempts)} 次`,
      `原因代码：${text(payload.error_code)}`,
      "请打开门锁观察簿的“运行”页面查看并重试。",
    ].join("\n\n");
  }
  if (topic === "system.download_failed") {
    return [
      "# 门锁录像下载持续失败",
      `下载器记录时间：${time(payload.event_time)}`,
      `自动尝试：${text(payload.attempts)} 次`,
      `原因代码：${text(payload.error_code)}`,
      "下载器仍会在每日核对中继续检查，请打开门锁观察簿查看状态。",
    ].join("\n\n");
  }
  return [
    "# 门锁录像服务通知",
    `类型：${text(topic)}`,
    "请打开门锁观察簿查看详细记录。",
  ].join("\n\n");
}

export function renderOutboxMessage(
  topic: string,
  payload: Record<string, unknown>,
): Record<string, unknown> {
  return {msgtype: "markdown", markdown: {content: buildSystemText(topic, payload)}};
}
