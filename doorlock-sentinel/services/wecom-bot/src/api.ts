import type {BotConfig} from "./config.js";

export interface OutboxMessage {
  id: string;
  topic: string;
  priority: number;
  attempts: number;
  created_at: string;
  payload: Record<string, unknown>;
}

export class RecognitionClient {
  constructor(private readonly config: BotConfig) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.config.recognitionBaseUrl}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        "x-internal-token": this.config.internalToken,
        ...(init?.headers ?? {}),
      },
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) {
      throw new Error(`recognition API returned ${response.status}`);
    }
    return (await response.json()) as T;
  }

  async claim(limit = 10): Promise<OutboxMessage[]> {
    const query = new URLSearchParams({worker: this.config.workerId, limit: String(limit)});
    const body = await this.request<{messages: OutboxMessage[]}>(`/internal/outbox/claim?${query}`);
    return body.messages;
  }

  async ack(messageId: string): Promise<void> {
    await this.request(`/internal/outbox/${encodeURIComponent(messageId)}/ack`, {
      method: "POST",
      body: JSON.stringify({worker: this.config.workerId}),
    });
  }

  async nack(messageId: string, errorCode: string, retryAfterSeconds = 30): Promise<void> {
    await this.request(`/internal/outbox/${encodeURIComponent(messageId)}/nack`, {
      method: "POST",
      body: JSON.stringify({
        worker: this.config.workerId,
        error: errorCode,
        retry_after_seconds: retryAfterSeconds,
      }),
    });
  }
}
