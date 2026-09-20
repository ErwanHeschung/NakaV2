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
  vram_total_mb: number;
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

export interface FactsState {
  facts: string[];
  max: number;
  max_chars: number;
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

export interface NoteInfo {
  name: string;
  modified: number;
  bytes: number;
  preview: string;
}

export interface NoteBody extends NoteInfo {
  content: string;
}

export interface NotesState {
  notes: NoteInfo[];
  directory: string;
}

export interface Timer {
  label: string;
  due: number;
  remaining_seconds: number;
}

export interface TimersState {
  timers: Timer[];
  now: number;
  can_fire: boolean;
  note: string;
}

/** One editable setting, described by the server so the UI can render it. */
export interface SettingField {
  key: string;
  label: string;
  kind: 'text' | 'int' | 'float' | 'bool' | 'choice' | 'key';
  group: string;
  help: string;
  applies: 'live' | 'models';
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  options: string[];
  value: string | number | boolean | null;
}

export interface SettingsState {
  fields: SettingField[];
  groups: string[];
  files: Record<string, string>;
}

export interface SettingsSaved {
  saved: string[];
  needs_model_reload: string[];
  fields: SettingField[];
}

/** What a client needs before it can listen. */
export interface ClientConfig {
  push_to_talk_key: string;
  listen_when_open: boolean;
  sample_rate: number;
  agentic: boolean;
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

  clientConfig(): Promise<ClientConfig> {
    return this.request('/client');
  }

  /** Starts a model reload without waiting for it, on the first key down. */
  wake(): Promise<unknown> {
    return this.request('/ops/wake', { method: 'POST' });
  }

  memory(): Promise<MemoryState> {
    return this.request('/memory');
  }

  facts(): Promise<FactsState> {
    return this.request('/memory/facts');
  }

  addFact(text: string): Promise<FactsState> {
    return this.request('/memory/facts', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
  }

  /**
   * `expect` is what the panel last displayed. The reconciler rewrites the
   * list after every turn, so the server refuses an edit aimed at a line that
   * has since changed rather than silently rewriting the wrong one.
   */
  editFact(number: number, text: string, expect: string): Promise<FactsState> {
    return this.request(`/memory/facts/${number}`, {
      method: 'PUT',
      body: JSON.stringify({ text, expect }),
    });
  }

  deleteFact(number: number, expect: string): Promise<FactsState> {
    return this.request(
      `/memory/facts/${number}?expect=${encodeURIComponent(expect)}`,
      { method: 'DELETE' },
    );
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

  notes(): Promise<NotesState> {
    return this.request('/notes');
  }

  note(name: string): Promise<NoteBody> {
    return this.request(`/notes/${encodeURIComponent(name)}`);
  }

  saveNote(name: string, content: string): Promise<NoteInfo> {
    return this.request(`/notes/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
  }

  deleteNote(name: string): Promise<unknown> {
    return this.request(`/notes/${encodeURIComponent(name)}`, { method: 'DELETE' });
  }

  timers(): Promise<TimersState> {
    return this.request('/timers');
  }

  startTimer(label: string, durationSeconds: number): Promise<TimersState> {
    return this.request('/timers', {
      method: 'POST',
      body: JSON.stringify({ label, duration_seconds: durationSeconds }),
    });
  }

  cancelTimer(label: string): Promise<TimersState> {
    return this.request(`/timers/${encodeURIComponent(label)}`, {
      method: 'DELETE',
    });
  }

  settings(): Promise<SettingsState> {
    return this.request('/settings');
  }

  /** Server validates and writes atomically: a bad value saves nothing. */
  saveSettings(changes: Record<string, unknown>): Promise<SettingsSaved> {
    return this.request('/settings', {
      method: 'PATCH',
      body: JSON.stringify({ changes }),
    });
  }

  /** Stops the agent loop wherever it is. */
  stopAgent(): Promise<unknown> {
    return this.request('/agent/stop', { method: 'POST' });
  }
}

export const api = new NakaApi();
