import {WSClient} from "@wecom/aibot-node-sdk";
import type {BotConfig} from "./config.js";

export type ConnectionHandler = (connected: boolean) => void;

export interface BotTransport {
  start(handler: ConnectionHandler): Promise<void>;
  stop(): Promise<void>;
  sendMessage(targetUserId: string, body: Record<string, unknown>): Promise<void>;
}

export class StdoutTransport implements BotTransport {
  async start(handler: ConnectionHandler): Promise<void> {
    handler(true);
    console.log(JSON.stringify({level: "info", message: "stdout transport started"}));
  }
  async stop(): Promise<void> {}
  async sendMessage(targetUserId: string, body: Record<string, unknown>): Promise<void> {
    void targetUserId;
    console.log(JSON.stringify({transport: "stdout", code: "MESSAGE_DELIVERED", msgtype: body.msgtype ?? "unknown"}));
  }
}

export class OfficialSdkTransport implements BotTransport {
  private client: InstanceType<typeof WSClient> | null = null;

  constructor(private readonly config: BotConfig) {}

  async start(handler: ConnectionHandler): Promise<void> {
    const client = new WSClient({
      botId: this.config.botId,
      secret: this.config.botSecret,
      maxReconnectAttempts: -1,
      logger: {
        debug: () => {},
        info: () => console.log(JSON.stringify({level: "info", service: "wecom-sdk", code: "SDK_INFO"})),
        warn: () => console.log(JSON.stringify({level: "warn", service: "wecom-sdk", code: "SDK_WARNING"})),
        error: () => console.log(JSON.stringify({level: "error", service: "wecom-sdk", code: "SDK_ERROR"})),
      },
    });
    this.client = client;
    client.on("authenticated", () => handler(true));
    client.on("disconnected", () => handler(false));
    client.on("reconnecting", () => handler(false));
    client.connect();
  }

  async stop(): Promise<void> {
    this.client?.disconnect();
  }

  async sendMessage(targetUserId: string, body: Record<string, unknown>): Promise<void> {
    if (!this.client?.isConnected) throw new Error("WeCom WebSocket is not connected");
    await this.client.sendMessage(targetUserId, body);
  }
}

export function createTransport(config: BotConfig): BotTransport {
  return config.transport === "stdout" ? new StdoutTransport() : new OfficialSdkTransport(config);
}
