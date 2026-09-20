/**
 * Typed client for the Naka server.
 *
 * These types are hand-written against the FastAPI endpoints rather than
 * generated, so they will drift if the server changes. That is a deliberate
 * trade for now — generating them would mean another build step — but it means
 * the server is the source of truth and this file has to follow it.
 */

export interface OpsStatus {
  models_loaded: boolean;
  llm_up: boolean | null;
  vram_used_mb: number;
  vram_free_mb: number;
  idle_seconds: number;
  in_flight: number;
  transition_locked: boolean;
}

export interface MemoryState {
  facts: string[];
  summary: string;
  recent: { user: string; naka: string }[];
  pending_summary: number;
}

export interface ToolInfo {
  name: string;
  destructive: boolean;
  description: string;
}

export interface ToolsState {
  allowed: ToolInfo[];
  max_steps: number;
  timeout_s: number;
  awaiting_confirmation: { name: string; arguments: Record<string, unknown> } | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Same-origin by default: the UI is served by the server it talks to, which
 * keeps it on loopback and avoids CORS entirely.
 */
export class NakaApi {
  constructor(private readonly base: string = '') {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.base}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText);
      throw new ApiError(detail.slice(0, 200), response.status);
    }
    // The response shape is not validated at runtime. These types describe
    // what the server is expected to return, and a mismatch will surface as
    // undefined at the point of use rather than being caught here. Adding a
    // validator would be the fix if these types and the server ever diverge.
    // oxlint-disable-next-line typescript/no-unsafe-type-assertion
    if (response.status === 204) return undefined as T;
    // oxlint-disable-next-line typescript/no-unsafe-type-assertion
    return (await response.json()) as T;
  }

  health(): Promise<{ status: string; stt: boolean; tts: boolean }> {
    return this.request('/health');
  }

  opsStatus(): Promise<OpsStatus> {
    return this.request('/ops/status');
  }

  /** Releases the GPU. Slow to reverse, so the UI should confirm first. */
  unload(includeLlm = true): Promise<unknown> {
    return this.request(`/ops/unload?include_llm=${includeLlm}`, { method: 'POST' });
  }

  load(): Promise<unknown> {
    return this.request('/ops/load', { method: 'POST' });
  }

  memory(): Promise<MemoryState> {
    return this.request('/memory');
  }

  reloadMemory(): Promise<unknown> {
    return this.request('/memory/reload', { method: 'POST' });
  }

  forgetConversation(): Promise<unknown> {
    return this.request('/memory/forget', { method: 'POST' });
  }

  tools(): Promise<ToolsState> {
    return this.request('/tools');
  }

  /** Stops the agent loop wherever it is. */
  stopAgent(): Promise<unknown> {
    return this.request('/agent/stop', { method: 'POST' });
  }
}

export const api = new NakaApi();
