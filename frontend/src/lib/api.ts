import type { ModelInfo, SavingsData, ServerInfo } from '../types';
import { SUPABASE_ANON_KEY, SUPABASE_URL } from './supabase';
import { serializeToolCallArguments } from './tool-call';

// ---------------------------------------------------------------------------
// Supabase config
// ---------------------------------------------------------------------------

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

export const isTauri = () => typeof window !== 'undefined' && !!window.__TAURI_INTERNALS__;

export type CloudKeyStatus = Record<string, boolean>;

export async function getCloudKeyStatus(): Promise<CloudKeyStatus> {
  if (!isTauri()) {
    // Browser build: ask the server which keys it holds (names only).
    try {
      const res = await apiFetch(`/v1/tools/cloud/keys/status`);
      return res.ok ? await res.json() : {};
    } catch {
      return {};
    }
  }
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    const rows = await invoke<Array<{ key: string; set: boolean }>>('get_cloud_key_status');
    return Object.fromEntries(rows.map((row) => [row.key, row.set]));
  } catch (e: any) {
    throw new Error(e?.message ?? e ?? 'Failed to read cloud key status');
  }
}

export async function saveCloudKey(keyName: string, keyValue: string): Promise<void> {
  if (!isTauri()) {
    throw new Error('Cloud API keys can be saved in the desktop app only.');
  }
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('save_cloud_key', { keyName, keyValue });
  } catch (e: any) {
    throw new Error(e?.message ?? e ?? 'Failed to save cloud key');
  }
}

export type OpenRouterModel = { id: string; name: string; free: boolean };

export async function fetchOpenRouterModels(): Promise<OpenRouterModel[]> {
  const res = await apiFetch(`/v1/tools/cloud/openrouter/models`);
  if (!res.ok) throw new Error(`Failed to load OpenRouter models (${res.status})`);
  return await res.json();
}

// Cached API base URL fetched from the Tauri backend at startup.
// This avoids hardcoding the port — the Rust backend is the single
// source of truth for JARVIS_PORT.
let _tauriApiBase: string | null = null;

/** Pre-fetch the API base URL from the Tauri backend (call once at init). */
export async function initApiBase(): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    _tauriApiBase = await invoke<string>('get_api_base');
  } catch {
    // Command may not exist on older builds; fall through to default.
  }
}

const DESKTOP_API_FALLBACK = 'http://127.0.0.1:8000';

const getSettingsApiUrl = (): string => {
  try {
    const raw = localStorage.getItem('openjarvis-settings');
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed.apiUrl) return parsed.apiUrl.replace(/\/+$/, '');
    }
  } catch {}
  return '';
};

export const getBase = (): string => {
  const settingsUrl = getSettingsApiUrl();
  if (settingsUrl) return settingsUrl;
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  if (isTauri()) return _tauriApiBase || DESKTOP_API_FALLBACK;
  return '';
};

// Resolve the local server API key (OPENJARVIS_API_KEY). When `jarvis serve`
// is started with a key, AuthMiddleware 401s every /v1 and /api request that
// lacks a Bearer token — so the frontend must send it (#266). Sourced from the
// same settings blob as the API URL, with an optional build-time env override.
// Returns '' when unset, so a keyless local server keeps working unchanged.
export const getApiKey = (): string => {
  try {
    const raw = localStorage.getItem('openjarvis-settings');
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed.apiKey) return String(parsed.apiKey);
    }
  } catch {}
  if (import.meta.env.VITE_OPENJARVIS_API_KEY) {
    return import.meta.env.VITE_OPENJARVIS_API_KEY as string;
  }
  return '';
};

// Build request headers with the Bearer Authorization token when a local key
// is configured, merging any caller-supplied headers. Adds no Authorization
// header when no key is set, so keyless local dev is byte-for-byte unchanged.
export const authHeaders = (
  extra: Record<string, string> = {},
): Record<string, string> => {
  const key = getApiKey();
  return key ? { ...extra, Authorization: `Bearer ${key}` } : { ...extra };
};

// Centralized fetch for the local server: prepends getBase() and injects the
// Bearer auth header (when a key is set) on every call. Using this everywhere
// guarantees no /v1 or /api request is sent without auth — the bug in #266 was
// that direct fetch() calls omitted the header and 401'd. `path` is the
// server-relative path (e.g. "/v1/savings").
export const apiFetch = (
  path: string,
  init: RequestInit = {},
): Promise<Response> => {
  const headers = authHeaders(
    (init.headers as Record<string, string> | undefined) ?? {},
  );
  return fetch(`${getBase()}${path}`, { ...init, headers });
};

async function tauriInvoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core');
  const apiUrl = getBase();
  return invoke<T>(command, { apiUrl, ...args });
}

// ---------------------------------------------------------------------------
// Setup status (desktop only)
// ---------------------------------------------------------------------------

export interface SetupStatus {
  phase: string;
  detail: string;
  ollama_ready: boolean;
  server_ready: boolean;
  model_ready: boolean;
  error: string | null;
  source: 'unconfigured' | 'ollama' | 'custom';
  requires_source: boolean;
}

export async function getSetupStatus(): Promise<SetupStatus | null> {
  if (!isTauri()) return null;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    return await invoke<SetupStatus>('get_setup_status');
  } catch {
    return null;
  }
}

/** Start desktop services after an inference source has been persisted. */
export async function startBackend(): Promise<void> {
  if (!isTauri()) throw new Error('The desktop backend is available in the desktop app only.');
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke<void>('start_backend');
  } catch (e: any) {
    throw new Error(e?.message ?? e ?? 'Failed to start the desktop backend');
  }
}

/** Stop an in-flight setup and return the desktop to its inert source chooser. */
export async function resetInferenceSource(): Promise<void> {
  if (!isTauri()) throw new Error('Inference setup recovery is available in the desktop app only.');
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke<void>('reset_inference_source');
  } catch (e: any) {
    throw new Error(e?.message ?? e ?? 'Failed to reset inference setup');
  }
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchModels(): Promise<ModelInfo[]> {
  if (isTauri()) {
    try {
      const result = await tauriInvoke<{ data?: ModelInfo[] }>('fetch_models');
      return result?.data || [];
    } catch {
      // Fall through to fetch
    }
  }
  const res = await apiFetch(`/v1/models`);
  if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`);
  const data = await res.json();
  return data.data || [];
}

export async function fetchRecommendedModel(): Promise<{ model: string; reason: string }> {
  const res = await apiFetch(`/v1/recommended-model`);
  if (!res.ok) return { model: '', reason: 'Failed to fetch' };
  return res.json();
}

export async function pullModel(modelName: string): Promise<void> {
  // In Tauri, go through the Rust backend directly (avoids CORS / timeout
  // issues with long model downloads via fetch).
  if (isTauri()) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('pull_ollama_model', { modelName });
      return;
    } catch (e: any) {
      throw new Error(e?.message || e || 'Download failed');
    }
  }
  const res = await apiFetch(`/v1/models/pull`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: modelName }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Failed to pull model: ${detail}`);
  }
}

export async function deleteModel(modelName: string): Promise<void> {
  if (isTauri()) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('delete_ollama_model', { modelName });
      return;
    } catch (e: any) {
      throw new Error(e?.message || e || 'Delete failed');
    }
  }
  const res = await apiFetch(`/v1/models/${encodeURIComponent(modelName)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Failed to delete model: ${detail}`);
  }
}

const _CLOUD_PREFIXES = ['gpt-', 'o1-', 'o3-', 'o4-', 'claude-', 'gemini-', 'openrouter/'];

export async function preloadModel(modelName: string, owner?: string): Promise<void> {
  // Cloud models don't need Ollama preloading
  if (owner === 'litellm' || _CLOUD_PREFIXES.some(p => modelName.startsWith(p))) {
    return;
  }
  // Trigger Ollama to load the model into memory (empty prompt, no generation).
  const ollamaUrl = 'http://127.0.0.1:11434';
  try {
    const res = await fetch(`${ollamaUrl}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelName, prompt: '', keep_alive: '5m' }),
      signal: AbortSignal.timeout(120_000),
    });
    if (!res.ok) throw new Error(`Preload failed: ${res.status}`);
  } catch (e: any) {
    if (e.name === 'TimeoutError') throw new Error('Model load timed out (120s)');
    throw e;
  }
}

export async function fetchSavings(): Promise<SavingsData> {
  const res = await apiFetch(`/v1/savings`);
  if (!res.ok) throw new Error(`Failed to fetch savings: ${res.status}`);
  return res.json();
}

export async function fetchServerInfo(): Promise<ServerInfo> {
  const res = await apiFetch(`/v1/info`);
  if (!res.ok) throw new Error(`Failed to fetch server info: ${res.status}`);
  return res.json();
}

export async function checkHealth(): Promise<boolean> {
  if (isTauri()) {
    try {
      await tauriInvoke('check_health', { apiUrl: getBase() });
      return true;
    } catch {
      return false;
    }
  }
  // In the browser, hit /health relative to the page origin so the request
  // flows through whatever path is already serving the SPA — the Vite
  // proxy in dev, FastAPI's static mount in prod. This avoids the
  // false-negative "Cannot reach backend" banner when getBase() points at
  // an absolute URL the browser can't reach directly.
  //
  // If /health itself fails for any reason (proxy quirk, stale service
  // worker, etc.) fall back to an arbitrary API endpoint we know the rest
  // of the app polls successfully. If THAT also fails we genuinely can't
  // reach the backend.
  const probe = async (url: string): Promise<boolean> => {
    try {
      const res = await fetch(url, { cache: 'no-store' });
      return res.ok;
    } catch {
      return false;
    }
  };
  // In browser mode Vite serves the SPA on a different port than the API.
  // Probe the configured API base first; the relative fallback supports
  // production deployments where both are served from one origin.
  const base = getBase().replace(/\/+$/, '');
  if (base && await probe(`${base}/health`)) return true;
  if (await probe('/health')) return true;
  if (base && await probe(`${base}/v1/connectors`)) return true;
  return probe('/v1/connectors');
}

export async function fetchEnergy(): Promise<unknown> {
  if (isTauri()) {
    try {
      return await tauriInvoke('fetch_energy', { apiUrl: getBase() });
    } catch {}
  }
  const res = await apiFetch(`/v1/telemetry/energy`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function fetchTelemetry(): Promise<unknown> {
  if (isTauri()) {
    try {
      return await tauriInvoke('fetch_telemetry', { apiUrl: getBase() });
    } catch {}
  }
  const res = await apiFetch(`/v1/telemetry/stats`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function fetchTraces(limit: number = 50): Promise<unknown> {
  if (isTauri()) {
    try {
      return await tauriInvoke('fetch_traces', { apiUrl: getBase(), limit });
    } catch {}
  }
  const res = await apiFetch(`/v1/traces?limit=${limit}`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Speech
// ---------------------------------------------------------------------------

export interface TranscriptionResult {
  text: string;
  language: string | null;
  confidence: number | null;
  duration_seconds: number;
}

export interface SpeechHealth {
  available: boolean;
  backend?: string;
  reason?: string;
}

export async function transcribeAudio(audioBlob: Blob, filename = 'recording.webm'): Promise<TranscriptionResult> {
  if (isTauri()) {
    try {
      const buffer = await audioBlob.arrayBuffer();
      return await tauriInvoke<TranscriptionResult>('transcribe_audio', {
        audioData: Array.from(new Uint8Array(buffer)),
        filename,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(msg || 'Transcription failed');
    }
  }
  const formData = new FormData();
  formData.append('file', audioBlob, filename);
  const res = await apiFetch(`/v1/speech/transcribe`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail : "";
    } catch {
      // Keep the status-only message below when the body is not JSON.
    }
    throw new Error(detail || `Transcription failed: ${res.status}`);
  }
  return res.json();
}

export interface TtsHealth {
  available: boolean;
  backend?: string;
  voice_id?: string;
  speed?: number;
  reason?: string;
}

export async function synthesizeSpeech(
  text: string,
  opts: { voiceId?: string; speed?: number; signal?: AbortSignal } = {},
): Promise<Blob> {
  const res = await apiFetch(`/v1/speech/synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice_id: opts.voiceId, speed: opts.speed }),
    signal: opts.signal,
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail : '';
    } catch {
      // Keep the status-only message below when the body is not JSON.
    }
    throw new Error(detail || `Speech synthesis failed: ${res.status}`);
  }
  return res.blob();
}

export async function fetchTtsHealth(): Promise<TtsHealth> {
  const res = await apiFetch(`/v1/speech/tts/health`);
  if (!res.ok) return { available: false };
  return res.json();
}

export interface VoiceProfileInfo {
  id: string;
  name: string;
  description: string;
  backend: string;
  voice_id: string;
  speed: number;
  pitch: string;
  active: boolean;
}

export interface VoiceProfilesResponse {
  profiles: VoiceProfileInfo[];
  active_profile: string;
}

export interface VoiceOption {
  id: string;
  name: string;
  backend: string;
  gender: string;
  locale: string;
  description: string;
}

export interface SpeechSettingsUpdate {
  tts_backend?: string;
  voice_id?: string;
  voice_speed?: number;
  voice_pitch?: string;
  voice_profile?: string;
}

export async function fetchVoiceProfiles(): Promise<VoiceProfilesResponse> {
  const res = await apiFetch(`/v1/speech/profiles`);
  if (!res.ok) throw new Error(`Failed to fetch voice profiles: ${res.status}`);
  return res.json();
}

export async function applyVoiceProfile(profileId: string): Promise<Record<string, unknown>> {
  const res = await apiFetch(`/v1/speech/profiles/${encodeURIComponent(profileId)}/apply`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to apply voice profile: ${res.status}`);
  return res.json();
}

export async function fetchAvailableVoices(): Promise<VoiceOption[]> {
  const res = await apiFetch(`/v1/speech/voices`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.voices || [];
}

export async function updateSpeechSettings(settings: SpeechSettingsUpdate): Promise<Record<string, unknown>> {
  const res = await apiFetch(`/v1/speech/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error(`Failed to update speech settings: ${res.status}`);
  return res.json();
}

export async function interruptSpeech(): Promise<boolean> {
  try {
    const res = await apiFetch(`/v1/speech/interrupt`, { method: 'POST' });
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data.interrupted);
  } catch {
    return false;
  }
}

export async function fetchSpeechHealth(): Promise<SpeechHealth> {
  if (isTauri()) {
    try {
      return await tauriInvoke<SpeechHealth>('speech_health');
    } catch {
      return { available: false };
    }
  }
  const res = await apiFetch(`/v1/speech/health`);
  if (!res.ok) return { available: false };
  return res.json();
}

// ---------------------------------------------------------------------------
// Agent Manager
// ---------------------------------------------------------------------------

export interface ManagedAgentConfig extends Record<string, unknown> {
  schedule_type?: string;
  schedule_value?: string | number;
}

export interface ManagedAgent {
  id: string;
  name: string;
  agent_type: string;
  config: ManagedAgentConfig;
  status: 'idle' | 'running' | 'paused' | 'error' | 'archived' | 'needs_attention' | 'budget_exceeded' | 'stalled';
  summary_memory: string;
  created_at: number;
  updated_at: number;
  // Runtime stats
  total_runs?: number;
  total_cost?: number;
  total_tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  last_run_at?: number | null;
  // Budget
  budget?: number;
  // Learning
  learning_enabled?: boolean;
  // Live progress
  current_activity?: string;
}

export interface AgentTask {
  id: string;
  agent_id: string;
  description: string;
  status: 'pending' | 'active' | 'completed' | 'failed';
  progress: Record<string, unknown>;
  findings: unknown[];
  created_at: number;
}

export interface ChannelBinding {
  id: string;
  agent_id: string;
  channel_type: string;
  config: Record<string, unknown>;
  session_id: string;
  routing_mode: string;
}

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  source: 'built-in' | 'user';
  agent_type: string;
  [key: string]: unknown;
}

export interface PersistedToolCall {
  tool: string;
  arguments: string;
  result?: string;
  success?: boolean;
  latency?: number;
}

export interface AgentMessage {
  id: string;
  agent_id: string;
  direction: 'user_to_agent' | 'agent_to_user';
  content: string;
  mode: 'immediate' | 'queued';
  status: 'pending' | 'delivered' | 'responded';
  created_at: number;
  tool_calls?: PersistedToolCall[] | null;
}

export async function fetchManagedAgents(): Promise<ManagedAgent[]> {
  const res = await apiFetch(`/v1/managed-agents`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.agents || [];
}

export async function fetchManagedAgent(agentId: string): Promise<ManagedAgent> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function createManagedAgent(body: {
  name: string;
  agent_type?: string;
  template_id?: string;
  config?: Record<string, unknown>;
}): Promise<ManagedAgent> {
  const res = await apiFetch(`/v1/managed-agents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function updateManagedAgent(
  agentId: string,
  body: Partial<{ name: string; agent_type: string; config: Record<string, unknown> }>,
): Promise<ManagedAgent> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function deleteManagedAgent(agentId: string): Promise<void> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function pauseManagedAgent(agentId: string): Promise<void> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/pause`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function resumeManagedAgent(agentId: string): Promise<void> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/resume`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function fetchAgentTasks(agentId: string): Promise<AgentTask[]> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/tasks`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.tasks || [];
}

export async function createAgentTask(agentId: string, description: string): Promise<AgentTask> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ description }),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function fetchAgentChannels(agentId: string): Promise<ChannelBinding[]> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/channels`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.bindings || [];
}

export async function bindAgentChannel(
  agentId: string,
  channelType: string,
  config?: Record<string, unknown>,
): Promise<ChannelBinding> {
  const res = await fetch(
    `${getBase()}/v1/managed-agents/${agentId}/channels`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel_type: channelType,
        config: config || {},
        routing_mode: 'dedicated',
      }),
    },
  );
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function unbindAgentChannel(
  agentId: string,
  bindingId: string,
): Promise<void> {
  const res = await fetch(
    `${getBase()}/v1/managed-agents/${agentId}/channels/${bindingId}`,
    { method: 'DELETE' },
  );
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

// -- SendBlue auto-setup helpers ------------------------------------------

export async function sendblueVerify(
  apiKeyId: string,
  apiSecretKey: string,
): Promise<{ valid: boolean; numbers: string[]; raw: unknown }> {
  const res = await apiFetch(`/v1/channels/sendblue/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key_id: apiKeyId, api_secret_key: apiSecretKey }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Verification failed: ${res.status}`);
  }
  return res.json();
}

export async function sendblueRegisterWebhook(
  apiKeyId: string,
  apiSecretKey: string,
  webhookUrl: string,
): Promise<{ registered: boolean; status: number }> {
  const res = await apiFetch(`/v1/channels/sendblue/register-webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      api_key_id: apiKeyId,
      api_secret_key: apiSecretKey,
      webhook_url: webhookUrl,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Webhook registration failed: ${res.status}`);
  }
  return res.json();
}

export async function sendblueTest(
  apiKeyId: string,
  apiSecretKey: string,
  fromNumber: string,
  toNumber: string,
): Promise<{ sent: boolean; status: number }> {
  const res = await apiFetch(`/v1/channels/sendblue/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      api_key_id: apiKeyId,
      api_secret_key: apiSecretKey,
      from_number: fromNumber,
      to_number: toNumber,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Test message failed: ${res.status}`);
  }
  return res.json();
}

export async function sendblueHealth(): Promise<{ channel_connected: boolean; bridge_wired: boolean; ready: boolean }> {
  const res = await apiFetch(`/v1/channels/sendblue/health`);
  if (!res.ok) return { channel_connected: false, bridge_wired: false, ready: false };
  return res.json();
}

export async function fetchTemplates(): Promise<AgentTemplate[]> {
  const res = await apiFetch(`/v1/templates`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.templates || [];
}

export async function runManagedAgent(agentId: string): Promise<void> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/run`, { method: 'POST' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `Failed: ${res.status}`);
  }
}

export async function recoverManagedAgent(agentId: string): Promise<{ recovered: boolean; checkpoint: unknown }> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/recover`, { method: 'POST' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `Failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchAgentState(agentId: string): Promise<{
  agent: ManagedAgent;
  tasks: AgentTask[];
  channels: ChannelBinding[];
  messages: AgentMessage[];
  checkpoint: unknown;
}> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/state`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export interface AgentToolCallStart {
  tool: string;
  arguments: string;
}

export interface AgentToolCallEnd {
  tool: string;
  success: boolean;
  latency: number;
  result?: string;
}

export async function sendAgentMessage(
  agentId: string,
  content: string,
  mode: 'immediate' | 'queued' = 'queued',
  callbacks?: {
    onProgress?: (label: string) => void;
    onContentDelta?: (delta: string, fullContent: string) => void;
    onToolCallStart?: (info: AgentToolCallStart) => void;
    onToolCallEnd?: (info: AgentToolCallEnd) => void;
    onDone?: (fullContent: string, usage?: Record<string, number>, telemetry?: Record<string, unknown>) => void;
  },
): Promise<AgentMessage> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, mode, stream: true }),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);

  // If streaming, consume the SSE response so the agent runs
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('text/event-stream') && res.body) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let fullContent = '';
    let buffer = '';
    let lastUsage: Record<string, number> | undefined;
    let lastTelemetry: Record<string, unknown> | undefined;
    let currentEvent: string | undefined;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
            continue;
          }
          if (!line.startsWith('data: ')) {
            if (line.trim() === '') currentEvent = undefined;
            continue;
          }
          const data = line.slice(6);
          if (data === '[DONE]') {
            currentEvent = undefined;
            continue;
          }
          const evName = currentEvent;
          currentEvent = undefined;

          if (evName === 'tool_call_start') {
            try {
              const parsed = JSON.parse(data);
              callbacks?.onToolCallStart?.({
                tool: parsed.tool,
                arguments: serializeToolCallArguments(parsed.arguments),
              });
            } catch {
              /* skip */
            }
            continue;
          }
          if (evName === 'tool_call_end') {
            try {
              const parsed = JSON.parse(data);
              callbacks?.onToolCallEnd?.({
                tool: parsed.tool,
                success: !!parsed.success,
                latency: typeof parsed.latency === 'number' ? parsed.latency : 0,
                result: parsed.result,
              });
            } catch {
              /* skip */
            }
            continue;
          }

          try {
            const chunk = JSON.parse(data);
            // Deep-research branch still uses tool_progress in a data chunk
            const toolProgress = chunk.choices?.[0]?.tool_progress;
            if (toolProgress) {
              callbacks?.onProgress?.(toolProgress);
            }
            const delta = chunk.choices?.[0]?.delta?.content || '';
            if (delta) {
              fullContent += delta;
              callbacks?.onContentDelta?.(delta, fullContent);
            }
            if (chunk.usage) lastUsage = chunk.usage;
            if (chunk.telemetry) lastTelemetry = chunk.telemetry;
          } catch {
            /* skip malformed chunks */
          }
        }
      }
    } catch { /* stream ended */ }

    callbacks?.onDone?.(fullContent, lastUsage, lastTelemetry);

    return {
      id: '',
      agent_id: agentId,
      direction: 'agent_to_user',
      content: fullContent,
      mode,
      status: 'delivered',
      created_at: Date.now() / 1000,
    };
  }

  return res.json();
}

/**
 * Ask the agent a question by triggering an ad-hoc run.
 *
 * Posts the question as an `immediate`, non-streamed message — the backend
 * stores it and spawns a real agent tick (`execute_tick`) that consumes it as
 * the run's input (tools, trace, and all), rather than a raw one-shot chat.
 * Returns immediately with the stored user message; progress is observed via
 * the `/v1/agents/events` WebSocket and the resulting trace.
 */
export async function askAgent(agentId: string, content: string): Promise<AgentMessage> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, mode: 'immediate', stream: false }),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function fetchAgentMessages(agentId: string): Promise<AgentMessage[]> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/messages`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.messages || [];
}

export async function fetchErrorAgents(): Promise<ManagedAgent[]> {
  const res = await apiFetch(`/v1/agents/errors`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.agents || [];
}

// ---------------------------------------------------------------------------
// Agent Learning + Traces
// ---------------------------------------------------------------------------

export interface LearningLogEntry {
  id: string;
  agent_id: string;
  event_type: string;
  description: string;
  data: Record<string, unknown>;
  created_at: number;
}

export interface AgentTrace {
  id: string;
  outcome: string;
  duration: number;
  started_at: number;
  steps: number;
  error_message?: string;
  metadata?: Record<string, unknown>;
}

export interface ToolInfo {
  name: string;
  description: string;
  category: string;
  source: 'tool' | 'channel';
  requires_credentials: boolean;
  credential_keys: string[];
  configured: boolean;
}

export async function fetchAvailableTools(): Promise<ToolInfo[]> {
  const res = await apiFetch(`/v1/tools`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.tools || [];
}

export async function saveToolCredentials(
  toolName: string,
  credentials: Record<string, string>,
): Promise<void> {
  const res = await apiFetch(`/v1/tools/${toolName}/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function fetchToolCredentialStatus(
  toolName: string,
): Promise<Record<string, boolean>> {
  const res = await apiFetch(`/v1/tools/${toolName}/credentials/status`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return await res.json();
}

export async function deleteToolCredential(
  toolName: string,
  keyName: string,
): Promise<void> {
  const res = await apiFetch(
    `/v1/tools/${encodeURIComponent(toolName)}/credentials/${encodeURIComponent(keyName)}`,
    { method: 'DELETE' },
  );
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export interface AgentTraceDetail {
  id: string;
  agent: string;
  outcome: string;
  duration: number;
  started_at: number;
  steps: Array<{
    step_type: string;
    input: unknown;
    output: string;
    duration: number;
    metadata: Record<string, unknown>;
  }>;
}

export async function fetchLearningLog(agentId: string): Promise<LearningLogEntry[]> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/learning`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.learning_log || [];
}

export async function triggerLearning(agentId: string): Promise<void> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/learning/run`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function fetchAgentTraces(agentId: string, limit = 20): Promise<AgentTrace[]> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/traces?limit=${limit}`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.traces || [];
}

export async function fetchAgentTrace(agentId: string, traceId: string): Promise<AgentTraceDetail> {
  const res = await apiFetch(`/v1/managed-agents/${agentId}/traces/${traceId}`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Leaderboard savings submission (Supabase)
// ---------------------------------------------------------------------------

export interface SavingsSubmission {
  anon_id: string;
  display_name: string;
  email: string;
  total_calls: number;
  total_tokens: number;
  dollar_savings: number;
  energy_wh_saved: number;
  flops_saved: number;
  token_counting_version?: number;
}

export async function submitSavings(data: SavingsSubmission): Promise<boolean> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return false;
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/savings_entries?on_conflict=anon_id`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          apikey: SUPABASE_ANON_KEY,
          Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
          Prefer: 'resolution=merge-duplicates',
        },
        body: JSON.stringify(data),
      },
    );
    return res.ok || res.status === 201 || res.status === 200;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

export interface MemorySearchResult {
  content: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface MemoryStats {
  entries: number;
  backend: string;
  [key: string]: unknown;
}

export interface MemoryConfig {
  backend: string;
  // Set by the server when the native `openjarvis_rust` extension is missing,
  // so the UI can show the real cause instead of a healthy-looking config.
  available?: boolean;
  detail?: string | null;
  context_from_memory: boolean;
  context_top_k: number;
  context_min_score: number;
  context_max_tokens: number;
}

/**
 * Extract the server's `detail` message from a failed JSON response so the UI
 * surfaces the real cause (e.g. "openjarvis_rust extension is not installed")
 * instead of a blanket fallback string (#502).
 */
async function memoryErrorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    if (data && typeof data.detail === 'string' && data.detail) return data.detail;
  } catch {
    // Non-JSON body — fall through to the generic message below.
  }
  return fallback;
}

export async function getMemoryStats(): Promise<MemoryStats> {
  let res: Response;
  try {
    res = await apiFetch(`/v1/memory/stats`);
  } catch {
    throw new Error('Could not reach the OpenJarvis API. Check the server status and API URL, then retry.');
  }
  if (!res.ok) {
    const detail = await memoryErrorDetail(res, 'Memory status request failed');
    throw new Error(`${detail} (HTTP ${res.status})`);
  }
  return res.json();
}

export async function searchMemory(query: string, topK: number = 5): Promise<MemorySearchResult[]> {
  const res = await apiFetch(`/v1/memory/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k: topK }),
  });
  if (!res.ok) throw new Error('Failed to search memory');
  const data = await res.json();
  return data.results;
}

export async function storeMemory(content: string, metadata?: Record<string, unknown>): Promise<void> {
  const res = await apiFetch(`/v1/memory/store`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, metadata }),
  });
  if (!res.ok) throw new Error(await memoryErrorDetail(res, 'Failed to store memory'));
}

export async function indexMemoryPath(path: string): Promise<{ chunks_indexed: number; note?: string }> {
  const res = await apiFetch(`/v1/memory/index`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(await memoryErrorDetail(res, 'Failed to index path'));
  return res.json();
}

export async function getMemoryConfig(): Promise<MemoryConfig> {
  const res = await apiFetch(`/v1/memory/config`);
  if (!res.ok) throw new Error('Failed to fetch memory config');
  return res.json();
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export interface PendingApproval {
  id: string;
  action_type: string;
  description: string;
  payload: Record<string, unknown>;
  permission_key: string;
  tier: 'trivial' | 'low' | 'medium' | 'high';
  status: string;
  created_at: string;
  expires_at: string;
}

export async function fetchPendingApprovals(): Promise<PendingApproval[]> {
  const res = await apiFetch(`/v1/approvals/pending`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const data = await res.json();
  return data.actions || [];
}

export async function approveAction(actionId: string, remember = false): Promise<void> {
  const res = await apiFetch(`/v1/approvals/${actionId}/approve${remember ? '?remember=true' : ''}`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function getDesktopControl(): Promise<boolean> {
  const res = await apiFetch(`/v1/desktop-control`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return !!(await res.json()).enabled;
}

export async function setDesktopControl(enabled: boolean): Promise<void> {
  const res = await apiFetch(`/v1/desktop-control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function fetchFolderGrants(): Promise<string[]> {
  const res = await apiFetch(`/v1/folder-grants`);
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return (await res.json()).folders || [];
}

export async function addFolderGrant(path: string): Promise<void> {
  const res = await apiFetch(`/v1/folder-grants`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Failed: ${res.status}`);
}

export async function revokeFolderGrant(path: string): Promise<void> {
  const res = await apiFetch(`/v1/folder-grants?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

export async function denyAction(actionId: string): Promise<void> {
  const res = await apiFetch(`/v1/approvals/${actionId}/deny`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
}

// ---------------------------------------------------------------------------
// Inference source (desktop only)
// ---------------------------------------------------------------------------

export type InferenceSource = {
  kind: 'ollama' | 'custom';
  model?: string;
  host?: string;
  engine?: string;
};

export async function getInferenceSource(): Promise<InferenceSource> {
  if (isTauri()) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      return await invoke<InferenceSource>('get_inference_source');
    } catch (e: any) {
      throw new Error(e?.message ?? e ?? 'Failed to read inference source');
    }
  }
  return { kind: 'ollama' };
}

export async function setInferenceSource(
  src: InferenceSource & { apiKey?: string },
  options: { pending?: boolean } = {},
): Promise<void> {
  if (!isTauri()) throw new Error('Inference source is configurable in the desktop app only.');
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke<void>('set_inference_source', {
      kind: src.kind,
      model: src.model ?? null,
      host: src.host ?? null,
      engine: src.engine ?? null,
      apiKey: src.apiKey ?? null,
      pending: options.pending ?? null,
    });
  } catch (e: any) {
    // Surface the backend's actionable error strings (e.g. "A server URL is
    // required…", "Could not store the API key…") as proper Error instances.
    throw new Error(e?.message ?? e ?? 'Failed to save inference source');
  }
}

/** Stage a first-run choice; Rust confirms it only after backend readiness. */
export const stageInferenceSource = (
  src: InferenceSource & { apiKey?: string },
): Promise<void> => setInferenceSource(src, { pending: true });

// ---------------------------------------------------------------------------
// Background Autonomous Tasks & Routines & Capabilities
// ---------------------------------------------------------------------------

export type BackgroundTask = {
  task_id: string;
  goal: string;
  capability_profile: string;
  status: string;
  current_step_index: number;
  total_steps: number;
  progress_pct: number;
  created_at: number;
  updated_at: number;
  completed_at?: number;
  error?: string;
};

export async function fetchBackgroundTasks(status?: string): Promise<BackgroundTask[]> {
  const url = status ? `/v1/tasks?status=${encodeURIComponent(status)}` : '/v1/tasks';
  const res = await apiFetch(url);
  if (!res.ok) return [];
  const data = await res.json();
  return data.tasks ?? [];
}

export async function pauseBackgroundTask(taskId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/tasks/${taskId}/pause`, { method: 'POST' });
  return res.ok;
}

export async function resumeBackgroundTask(taskId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/tasks/${taskId}/resume`, { method: 'POST' });
  return res.ok;
}

export async function cancelBackgroundTask(taskId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/tasks/${taskId}/cancel`, { method: 'POST' });
  return res.ok;
}

export async function steerBackgroundTask(taskId: string, guidance: string): Promise<boolean> {
  const res = await apiFetch(`/v1/tasks/${taskId}/steer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ guidance }),
  });
  return res.ok;
}

export type RoutineItem = {
  id: string;
  name: string;
  description: string;
  routine_type: string;
  interval_seconds?: number;
  cron_expr?: string;
  action_type: string;
  payload: Record<string, any>;
  is_active: boolean;
  quiet_hours_behavior: string;
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;
};

export async function fetchRoutines(): Promise<RoutineItem[]> {
  const res = await apiFetch('/v1/routines');
  if (!res.ok) return [];
  const data = await res.json();
  return data.routines ?? [];
}

export async function pauseRoutine(routineId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/routines/${routineId}/pause`, { method: 'POST' });
  return res.ok;
}

export async function resumeRoutine(routineId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/routines/${routineId}/resume`, { method: 'POST' });
  return res.ok;
}

export async function runRoutineNow(routineId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/routines/${routineId}/run`, { method: 'POST' });
  return res.ok;
}

export async function deleteRoutine(routineId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/routines/${routineId}`, { method: 'DELETE' });
  return res.ok;
}

export type SystemCapabilities = {
  system: string;
  version: string;
  hardware: {
    os: string;
    cpu_name: string;
    cpu_logical_cores: number;
    ram_total_gb: number;
    ram_available_gb: number;
    ram_percent_used?: number;
    gpu_name?: string;
    gpu_vram_total_mb?: number;
    gpu_vram_free_mb?: number;
    gpu_available: boolean;
  };
  tools: {
    count: number;
    list: string[];
  };
  speech: {
    supported: boolean;
    wake_word_supported: boolean;
    wake_phrase: string;
    wake_active: boolean;
    session_state: string;
    stt_engine: string;
    tts_engine: string;
    echo_guard: boolean;
  };
  security: {
    permission_tiers: string[];
    approval_gateway_enforced: boolean;
    fresh_approval_required_for_sensitive: boolean;
    action_journal_enabled: boolean;
    file_compensation_undo: boolean;
  };
};

export async function fetchSystemCapabilities(): Promise<SystemCapabilities | null> {
  const res = await apiFetch('/v1/capabilities');
  if (!res.ok) return null;
  return await res.json();
}

export type MemoryFact = {
  id: string;
  text: string;
  source: string;
  created_at: number;
  trust: string;
  category: string;
  confidence: number;
  pinned: boolean;
};

export async function fetchMemoryFacts(category?: string, query?: string): Promise<MemoryFact[]> {
  const params = new URLSearchParams();
  if (category && category !== 'all') params.set('category', category);
  if (query) params.set('query', query);
  const res = await apiFetch(`/v1/memory/facts?${params.toString()}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.facts ?? [];
}

export async function createMemoryFact(fact: { text: string; category?: string; pinned?: boolean }): Promise<boolean> {
  const res = await apiFetch('/v1/memory/facts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fact),
  });
  return res.ok;
}

export async function deleteMemoryFact(factId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/memory/facts/${factId}`, { method: 'DELETE' });
  return res.ok;
}

export async function pinMemoryFact(factId: string, pinned: boolean): Promise<boolean> {
  const res = await apiFetch(`/v1/memory/facts/${factId}/pin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pinned }),
  });
  return res.ok;
}

// ---------------------------------------------------------------------------
// Agent Workforce & Autonomous Orchestration API
// ---------------------------------------------------------------------------

export type WorkforceAgent = {
  id: string;
  display_name: string;
  role: string;
  description: string;
  avatar: string;
  enabled: boolean;
  status:
    | 'registered'
    | 'initializing'
    | 'online'
    | 'idle'
    | 'busy'
    | 'waiting_approval'
    | 'paused'
    | 'degraded'
    | 'offline'
    | 'failed'
    | 'stopping';
  active_model: string;
  current_task_id?: string | null;
  runtime_health: 'healthy' | 'degraded' | 'offline' | 'failed';
  last_heartbeat?: number | null;
  last_error?: string | null;
  allowed_tools: string[];
  denied_tools: string[];
  version: string;
};

export type TeamSummary = {
  total_registered: number;
  total_online: number;
  total_idle: number;
  total_busy: number;
  total_waiting_approval: number;
  total_degraded: number;
  total_offline: number;
  total_failed: number;
  active_tasks: number;
};

export type WorkforceReport = {
  id: string;
  task_id: string;
  agent_id: string;
  title: string;
  summary: string;
  status: string;
  key_findings: string[];
  actions_taken: string[];
  recommendations: string[];
  confidence_score: number;
  created_at: number;
};

export type TaskTimeline = {
  task_id: string;
  messages: Array<{
    id: string;
    from_agent: string;
    to_agent: string;
    message_type: string;
    content: string;
    timestamp: number;
  }>;
  reports: WorkforceReport[];
};

export async function fetchWorkforceAgents(): Promise<WorkforceAgent[]> {
  const res = await apiFetch('/v1/workforce/agents');
  if (!res.ok) return [];
  const data = await res.json();
  return data.agents ?? [];
}

export async function fetchWorkforceAgent(agentId: string): Promise<any | null> {
  const res = await apiFetch(`/v1/workforce/agents/${agentId}`);
  if (!res.ok) return null;
  return await res.json();
}

export async function enableWorkforceAgent(agentId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/workforce/agents/${agentId}/enable`, { method: 'POST' });
  return res.ok;
}

export async function disableWorkforceAgent(agentId: string): Promise<boolean> {
  const res = await apiFetch(`/v1/workforce/agents/${agentId}/disable`, { method: 'POST' });
  return res.ok;
}

export async function fetchTeamSummary(): Promise<TeamSummary | null> {
  const res = await apiFetch('/v1/workforce/summary');
  if (!res.ok) return null;
  return await res.json();
}

export async function queryWorkforceStatus(query: string): Promise<string> {
  const res = await apiFetch('/v1/workforce/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) return 'Unable to query agent team status.';
  const data = await res.json();
  return data.answer ?? '';
}

export async function delegateTeamTask(goal: string, priority: string = 'normal'): Promise<any> {
  const res = await apiFetch('/v1/workforce/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal, priority }),
  });
  if (!res.ok) throw new Error(`Task delegation failed (${res.status})`);
  return await res.json();
}

export async function fetchTaskTimeline(taskId: string): Promise<TaskTimeline | null> {
  const res = await apiFetch(`/v1/workforce/tasks/${taskId}/timeline`);
  if (!res.ok) return null;
  return await res.json();
}

export async function fetchWorkforceReports(taskId?: string, agentId?: string): Promise<WorkforceReport[]> {
  const params = new URLSearchParams();
  if (taskId) params.set('task_id', taskId);
  if (agentId) params.set('agent_id', agentId);
  const res = await apiFetch(`/v1/workforce/reports?${params.toString()}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.reports ?? [];
}

export async function fetchStartupAnnouncement(): Promise<string> {
  const res = await apiFetch('/v1/workforce/announcement');
  if (!res.ok) return '';
  const data = await res.json();
  return data.announcement ?? '';
}

// ---------------------------------------------------------------------------
// Model Intelligence & Dynamic Allocation API
// ---------------------------------------------------------------------------

export type DiscoveredModel = {
  id: string;
  name: string;
  provider: string;
  is_local: boolean;
  capabilities: {
    tool_calling: boolean;
    structured_output: boolean;
    coding: boolean;
    reasoning: boolean;
    vision: boolean;
    embedding: boolean;
  };
  context_tokens: number;
  parameter_size_b?: number;
  cost_per_m_out: number;
  latency_tier: string;
  loaded_in_vram: boolean;
};

export type ModelProviderStatus = {
  provider: string;
  type: string;
  endpoint?: string;
  online: boolean;
  model_count: number;
  configured?: boolean;
};

export type ModelPolicyResponse = {
  active_policy: string;
  available_policies: string[];
  vram_limit_mb: number;
  max_concurrent_inferences: number;
};

export type ModelSelectionExplanation = {
  selected_model: string;
  provider: string;
  is_local: boolean;
  score: number;
  rationale: string;
  task_type: string;
  policy_used: string;
  vram_allocated_mb: number;
  candidate_scores: Record<string, number>;
};

export type HardwareProfile = {
  vram_limit_mb: number;
  max_concurrent_inferences: number;
  active_inferences: number;
  gpu_detected: string;
  cpu_profile: string;
};

export async function fetchDiscoveredModels(refresh: boolean = false): Promise<{
  total: number;
  local_count: number;
  cloud_count: number;
  models: DiscoveredModel[];
}> {
  const res = await apiFetch(`/v1/models/discovered?refresh=${refresh}`);
  if (!res.ok) return { total: 0, local_count: 0, cloud_count: 0, models: [] };
  return await res.json();
}

export async function fetchModelProviders(): Promise<ModelProviderStatus[]> {
  const res = await apiFetch('/v1/models/providers');
  if (!res.ok) return [];
  const data = await res.json();
  return data.providers ?? [];
}

export async function fetchModelPolicy(): Promise<ModelPolicyResponse | null> {
  const res = await apiFetch('/v1/models/policy');
  if (!res.ok) return null;
  return await res.json();
}

export async function updateModelPolicy(policy: string): Promise<string> {
  const res = await apiFetch('/v1/models/policy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ policy }),
  });
  if (!res.ok) throw new Error(`Policy update failed (${res.status})`);
  const data = await res.json();
  return data.active_policy ?? policy;
}

export async function explainModelSelection(
  taskType: string,
  agentId?: string,
): Promise<ModelSelectionExplanation | null> {
  const res = await apiFetch('/v1/models/select', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_type: taskType, agent_id: agentId }),
  });
  if (!res.ok) return null;
  return await res.json();
}

export async function switchAgentModel(agentId: string, model: string): Promise<boolean> {
  const res = await apiFetch('/v1/models/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId, model }),
  });
  return res.ok;
}

export async function fetchHardwareProfile(): Promise<HardwareProfile | null> {
  const res = await apiFetch('/v1/models/hardware');
  if (!res.ok) return null;
  return await res.json();
}

// ---------------------------------------------------------------------------
// n8n Workflow Automation API
// ---------------------------------------------------------------------------

export type N8nStatus = {
  online: boolean;
  status_code: number;
  base_url: string;
  latency_ms: number;
  auth_mode: string;
  error?: string;
};

export type N8nWorkflowSummary = {
  id: string;
  name: string;
  active: boolean;
  createdAt?: string;
  updatedAt?: string;
  tags?: string[];
};

export async function fetchN8nStatus(): Promise<N8nStatus> {
  const res = await apiFetch('/v1/n8n/status');
  if (!res.ok) {
    return {
      online: false,
      status_code: res.status,
      base_url: '',
      latency_ms: 0,
      auth_mode: 'unknown',
      error: `HTTP ${res.status}`,
    };
  }
  return await res.json();
}

export async function fetchN8nWorkflows(): Promise<N8nWorkflowSummary[]> {
  const res = await apiFetch('/v1/n8n/workflows');
  if (!res.ok) return [];
  const data = await res.json();
  return data.workflows ?? [];
}

export async function executeN8nWorkflow(
  workflowId: string,
  data?: any,
  approvalId?: string,
): Promise<any> {
  const res = await apiFetch(`/v1/n8n/workflows/${workflowId}/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data, approval_id: approvalId }),
  });
  return await res.json();
}

export async function triggerN8nWebhook(path: string, data?: any): Promise<any> {
  const res = await apiFetch(`/v1/n8n/webhooks/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data }),
  });
  return await res.json();
}

// ---------------------------------------------------------------------------
// Desktop Browser Integration
// ---------------------------------------------------------------------------

export interface BrowserTab {
  tab_id: string | number;
  window_id: string | number;
  url: string;
  title: string;
  domain: string;
  is_active: boolean;
  fav_icon_url?: string;
}

export interface BrowserStatus {
  state: 'disconnected' | 'connected' | 'ready';
  mode: 'extension' | 'cdp' | 'windows_gui' | 'unconnected';
  browser_running: boolean;
  running_browsers: string[];
  tab_count: number;
  active_tab: BrowserTab | null;
  extension_connected: boolean;
  pairing_token: string;
  installed_browsers: Record<string, string>;
}

export interface BrowserActionResult {
  success: boolean;
  action: string;
  message: string;
  reused_existing: boolean;
  tab?: BrowserTab;
  metadata?: Record<string, any>;
}

export async function fetchBrowserStatus(): Promise<BrowserStatus> {
  const res = await apiFetch('/v1/browser/status');
  if (!res.ok) throw new Error(`Failed to load browser status (${res.status})`);
  return await res.json();
}

export async function fetchBrowserTabs(): Promise<BrowserTab[]> {
  const res = await apiFetch('/v1/browser/tabs');
  if (!res.ok) return [];
  const data = await res.json();
  return data.tabs ?? [];
}

export async function openOrReuseBrowserTab(
  destination: string,
  explicitNewTab = false,
  taskContext = '',
): Promise<BrowserActionResult> {
  const res = await apiFetch('/v1/browser/tabs/open', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      destination,
      explicit_new_tab: explicitNewTab,
      task_context: taskContext,
    }),
  });
  if (!res.ok) throw new Error(`Failed to open/reuse tab (${res.status})`);
  return await res.json();
}

export async function activateBrowserTab(
  tabId: string | number,
  windowId = 0,
  titleHint = '',
): Promise<{ status: string; activated: boolean; tab_id: string | number }> {
  const res = await apiFetch(`/v1/browser/tabs/${tabId}/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ window_id: windowId, title_hint: titleHint }),
  });
  if (!res.ok) throw new Error(`Failed to activate tab (${res.status})`);
  return await res.json();
}

export async function closeBrowserTab(
  tabId: string | number,
): Promise<{ status: string; closed: boolean; tab_id: string | number }> {
  const res = await apiFetch(`/v1/browser/tabs/${tabId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`Failed to close tab (${res.status})`);
  return await res.json();
}

export async function refreshBrowserConnection(): Promise<BrowserStatus> {
  const res = await apiFetch('/v1/browser/connect', { method: 'POST' });
  if (!res.ok) throw new Error(`Failed to refresh browser connection (${res.status})`);
  return await res.json();
}

export async function fetchBrowserExtensionToken(): Promise<string> {
  const res = await apiFetch('/v1/browser/extension/token');
  if (!res.ok) throw new Error(`Failed to fetch extension pairing token (${res.status})`);
  const data = await res.json();
  return data.token ?? '';
}


