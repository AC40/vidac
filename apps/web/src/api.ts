export type Look = 'neutral' | 'warm' | 'cool' | 'monochrome';
export interface Clip {
  id: string;
  asset_id: string;
  title: string;
  description: string;
  tags: string[];
  start_frame: number;
  end_frame: number;
  shot_start: number;
  shot_end: number;
  favorite: boolean;
  provenance: string;
  annotation: Record<string, unknown>;
  asset_name: string;
  fps: string;
  width: number;
  height: number;
  has_audio: boolean;
}
export interface Asset {
  id: string;
  name: string;
  path: string;
  online: boolean;
  status: string;
  duration: number;
  width: number;
  height: number;
  fps: string;
  metadata: {
    warnings?: string[];
    analysis?: Record<string, unknown>;
    conformed?: boolean;
    edit_path?: string;
  };
}
export interface Job {
  id: string;
  kind: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  progress: number;
  stage: string;
  error?: string;
  result?: {
    edit_id?: string;
    asset_id?: string;
    warnings?: string[];
    relinked?: { asset_id: string; name: string; path: string }[];
    missing?: { asset_id: string; name: string; path: string }[];
    assets?: number;
    windows?: number;
    clips_updated?: number;
    reviewed_windows?: number;
    failed_windows?: number;
    usage?: {
      requests?: number;
      input_tokens?: number;
      output_tokens?: number;
      reasoning_tokens?: number;
      cache_read_tokens?: number;
      cache_write_tokens?: number;
      cost_usd?: number;
    };
  };
  payload: Record<string, unknown>;
  created_at: string;
}
export interface EditItem {
  clip_id: string;
  asset_id: string;
  source_in: number;
  source_out: number;
  timeline_frames: number;
  title: string;
  reason: string;
  look: Look;
  audio: boolean;
}
export interface Plan {
  name: string;
  brief: string;
  fps: string;
  width: number;
  height: number;
  items: EditItem[];
  notes: string[];
  planner: string;
}
export interface Artifact {
  id: string;
  kind: string;
  revision: number;
  path: string;
}
export interface Edit {
  id: string;
  name: string;
  revision: number;
  plan: Plan;
  artifacts: Artifact[];
  versions: { revision: number; created_at: string }[];
}
export type Provider = 'ollama' | 'codex' | 'claude' | 'opencode';
export interface ModelCatalogue {
  provider: Provider;
  connected: boolean;
  models: string[];
  detail: string;
}
export const providerLabels: Record<Provider, string> = {
  ollama: 'Ollama',
  codex: 'Codex CLI',
  claude: 'Claude Code',
  opencode: 'OpenCode',
};
export function isAIProvenance(value: string) {
  return Object.keys(providerLabels).some((provider) => value.startsWith(`${provider}:`));
}
export interface Settings {
  scene_threshold: number;
  min_shot_seconds: number;
  select_seconds: number;
  proxy_height: number;
  ollama_url: string;
  vision_provider: Provider;
  vision_review_provider: Provider;
  planning_provider: Provider;
  allow_remote_ai: boolean;
  model_timeout_seconds: number;
  vision_model: string;
  vision_review_model: string;
  vision_review_threshold: number;
  planning_model: string;
  transcribe: boolean;
  transcription_model: string;
  watch_folders: string[];
}
export interface Stats {
  assets: number;
  clips: number;
  unclassified: number;
  favorites: number;
  duration: number;
  edits: number;
  active_jobs: number;
}
export interface Health {
  status: string;
  workspace: string;
  ffmpeg: boolean;
  ffprobe: boolean;
  worker: boolean;
  version: string;
}
export type Notify = (message: string, error?: boolean) => void;

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: {
      'X-Vidac-Client': 'workspace',
      ...(body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail =
      typeof data.detail === 'string'
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail
              .map((x: { msg: string; loc: string[] }) => `${x.loc.slice(1).join('.')}: ${x.msg}`)
              .join('; ')
          : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return response.json();
}
export function rate(fps: string): number {
  const [n, d = '1'] = fps.split('/');
  return Number(n) / Number(d);
}
export function time(seconds: number): string {
  const rounded = Math.max(0, Math.round(seconds));
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}
export function seconds(clip: Clip): number {
  return (clip.end_frame - clip.start_frame) / rate(clip.fps);
}
export const looks: Look[] = ['neutral', 'warm', 'cool', 'monochrome'];
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
