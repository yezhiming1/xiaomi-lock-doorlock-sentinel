import fs from "node:fs";
import os from "node:os";

export type TransportMode = "sdk" | "stdout";

function readSecret(directName: string, fileName: string, required: boolean): string {
  const direct = process.env[directName]?.trim();
  if (direct) return direct;
  const filePath = process.env[fileName]?.trim();
  if (filePath && fs.existsSync(filePath)) {
    const value = fs.readFileSync(filePath, "utf8").trim();
    if (value) return value;
  }
  if (required) throw new Error(`Missing required secret: ${directName} or ${fileName}`);
  return "";
}

function asBool(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

function asInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export interface BotConfig {
  enabled: boolean;
  transport: TransportMode;
  botId: string;
  botSecret: string;
  targetUserId: string;
  recognitionBaseUrl: string;
  internalToken: string;
  pollMs: number;
  heartbeatPath: string;
  workerId: string;
}

export function loadConfig(): BotConfig {
  const enabled = asBool(process.env.WECOM_ENABLED, true);
  const transport = (process.env.WECOM_TRANSPORT ?? "sdk") as TransportMode;
  const productionSdk = enabled && transport === "sdk";
  const botId = process.env.WECOM_BOT_ID?.trim() ?? "";
  const targetUserId = process.env.WECOM_TARGET_USERID?.trim() ?? "";
  if (productionSdk && !botId) throw new Error("WECOM_BOT_ID is required");
  if (productionSdk && !targetUserId) throw new Error("WECOM_TARGET_USERID is required");
  return {
    enabled,
    transport,
    botId,
    botSecret: readSecret("WECOM_BOT_SECRET", "WECOM_BOT_SECRET_FILE", productionSdk),
    targetUserId,
    recognitionBaseUrl: process.env.RECOGNITION_BASE_URL ?? "http://127.0.0.1:8787",
    internalToken: readSecret(
      "RECOGNITION_INTERNAL_TOKEN",
      "RECOGNITION_INTERNAL_TOKEN_FILE",
      enabled,
    ),
    pollMs: asInt(process.env.WECOM_POLL_MS, 2000),
    heartbeatPath: process.env.WECOM_HEARTBEAT_PATH ?? "/run/doorlock/wecom-heartbeat.json",
    workerId: process.env.WECOM_WORKER_ID ?? `${os.hostname()}:${process.pid}:wecom`,
  };
}
