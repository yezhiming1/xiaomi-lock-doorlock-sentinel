import fs from "node:fs";
import path from "node:path";
import {RecognitionClient, type OutboxMessage} from "./api.js";
import {renderOutboxMessage} from "./cards.js";
import {loadConfig} from "./config.js";
import {createTransport, type BotTransport} from "./transport.js";

const config = loadConfig();
const api = new RecognitionClient(config);
const transport: BotTransport = createTransport(config);
let stopping = false;
let connected = false;
let lastOutboxSuccess: string | null = null;
let lastError: string | null = null;

function log(level: string, message: string, extra: Record<string, unknown> = {}): void {
  console.log(JSON.stringify({time: new Date().toISOString(), level, service: "wecom-bot", message, ...extra}));
}

function writeHeartbeat(): void {
  const target = config.heartbeatPath;
  fs.mkdirSync(path.dirname(target), {recursive: true});
  const temporary = `${target}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify({
    time: new Date().toISOString(),
    pid: process.pid,
    enabled: config.enabled,
    connected,
    lastOutboxSuccess,
    lastError,
  }), {mode: 0o600});
  fs.renameSync(temporary, target);
}

async function deliver(message: OutboxMessage): Promise<void> {
  await transport.sendMessage(config.targetUserId, renderOutboxMessage(message.topic, message.payload));
}

async function pollOutbox(): Promise<void> {
  while (!stopping) {
    try {
      const messages = await api.claim(10);
      if (!messages.length) {
        await new Promise((resolve) => setTimeout(resolve, config.pollMs));
        continue;
      }
      for (const message of messages) {
        try {
          await deliver(message);
          await api.ack(message.id);
          lastOutboxSuccess = new Date().toISOString();
          lastError = null;
        } catch (error) {
          void error;
          lastError = "WECOM_DELIVERY_FAILED";
          await api.nack(message.id, lastError, Math.min(900, 15 * Math.max(1, message.attempts)));
          log("error", "outbox delivery failed", {messageId: message.id, errorCode: lastError});
        }
      }
    } catch (error) {
      void error;
      lastError = "OUTBOX_POLL_FAILED";
      log("error", "outbox polling failed", {errorCode: lastError});
      await new Promise((resolve) => setTimeout(resolve, Math.max(config.pollMs, 5000)));
    }
    writeHeartbeat();
  }
}

async function main(): Promise<void> {
  setInterval(writeHeartbeat, 10_000).unref();
  writeHeartbeat();
  if (!config.enabled) {
    log("info", "WeCom delivery disabled; operational messages remain queued");
    while (!stopping) await new Promise((resolve) => setTimeout(resolve, 30_000));
    return;
  }
  await transport.start((value) => {
    connected = value;
    writeHeartbeat();
  });
  log("info", "WeCom bot service started", {transport: config.transport});
  await pollOutbox();
}

async function shutdown(signal: string): Promise<void> {
  if (stopping) return;
  stopping = true;
  connected = false;
  writeHeartbeat();
  log("info", "shutting down", {signal});
  await transport.stop();
}

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => void shutdown(signal));
}

main().catch((error) => {
  void error;
  lastError = "WECOM_SERVICE_TERMINATED";
  writeHeartbeat();
  log("fatal", "bot process terminated", {errorCode: lastError});
  process.exitCode = 1;
});
