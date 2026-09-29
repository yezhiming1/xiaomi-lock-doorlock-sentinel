(function (root, factory) {
  const labels = factory();
  if (typeof module === "object" && module.exports) module.exports = labels;
  if (root) root.DoorlockUiLabels = labels;
})(typeof globalThis === "undefined" ? this : globalThis, function () {
  const states = Object.freeze({
    discovered: "已发现",
    retrying: "正在重试",
    downloaded: "已下载",
    failed: "下载失败",
  });

  const errorMessages = Object.freeze({
    NONE: "无",
    SEGMENT_FETCH_FAILED: "录像片段获取失败",
    PLAYLIST_FETCH_FAILED: "录像索引清单获取失败",
    PLAYLIST_KEY_FETCH_FAILED: "录像解密密钥获取失败",
    CLOUD_SESSION_UNAVAILABLE: "小米云会话不可用，请核对登录状态",
    CLOUD_SESSION_INVALID: "小米云会话无效或已过期",
    XIAOMI_MIOT_SESSION_UNAVAILABLE: "米家云会话不可用",
    CLOUD_DEVICE_QUERY_FAILED: "门锁设备信息获取失败",
    EVENTLIST_REQUEST_FAILED: "录像事件列表请求失败",
    DOWNLOAD_TIMEOUT: "下载超时",
    FFPROBE_FAILED: "下载后的录像校验失败",
    FFPROBE_TIMEOUT: "录像校验超时",
    FFPROBE_EXEC_FAILED: "录像校验程序启动失败",
    MEDIA_TOOLCHAIN_UNAVAILABLE: "录像处理程序不可用",
    LOCAL_IO_FAILED: "本地存储读写失败",
    OUTPUT_PREPARATION_FAILED: "录像保存目录准备失败",
    STATUS_JOURNAL_WRITE_FAILED: "下载状态记录写入失败",
    BACKUP_UNEXPECTED: "下载任务遇到未分类故障",
    EVENT_UNEXPECTED: "录像事件处理失败",
  });

  const errorFamilies = [
    ["FFPROBE_", "下载后的录像校验失败"],
    ["FFMPEG_", "录像转封装失败"],
    ["PLAYLIST_", "录像播放清单处理失败"],
    ["SEGMENT_", "录像片段处理失败"],
    ["CONCAT_", "录像片段合并失败"],
    ["MEDIA_", "录像内容或地址处理失败"],
    ["XIAOMI_", "小米云录像信息校验失败"],
    ["CLOUD_", "云端会话或设备信息异常"],
    ["EVENTLIST_", "云端录像事件列表处理失败"],
    ["EVENT_", "录像事件信息校验失败"],
    ["OUTPUT_", "录像文件保存失败"],
    ["LOCAL_", "本地录像处理失败"],
    ["STATUS_JOURNAL_", "下载状态记录处理失败"],
    ["STATUS_", "下载状态回报无效"],
    ["ATOMIC_PUBLISH_", "录像文件安全保存失败"],
    ["STATE_", "下载器本地状态异常"],
    ["BACKUP_", "下载任务异常"],
    ["FILENAME_", "录像文件命名或迁移冲突"],
    ["RETENTION_", "下载器保留策略执行失败"],
    ["HISTORY_", "历史录像下载任务异常"],
    ["SCHEDULE_", "下载计划设置无效"],
    ["TARGET_", "下载目标检查未通过"],
    ["MAX_", "下载任务设置无效"],
    ["KEEP_", "录像处理设置无效"],
    ["UNSAFE", "录像来源或保存路径未通过安全检查"],
    ["NON_", "录像处理路径未通过安全检查"],
  ];

  function stateLabel(value) {
    if (typeof value !== "string" || !value.trim()) return "未知下载状态";
    const state = value.trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(states, state) ? states[state] : "未知下载状态";
  }

  function errorLabel(value) {
    if (value == null || (typeof value === "string" && !value.trim())) return "无";
    if (typeof value !== "string") return "暂未收录的下载问题";
    const code = value.trim().toUpperCase();
    if (Object.prototype.hasOwnProperty.call(errorMessages, code)) return errorMessages[code];
    const family = errorFamilies.find(([prefix]) => code.startsWith(prefix));
    return family ? family[1] : "暂未收录的下载问题";
  }

  return Object.freeze({ stateLabel, errorLabel });
});
