import { useEffect, useState } from 'react';
import { Check, Cpu, Folder, RefreshCw, ScanLine, Settings2 } from 'lucide-react';
import {
  api,
  errorMessage,
  type Health,
  type Notify,
  type Settings,
  type Provider,
  type ModelCatalogue,
  providerLabels,
} from './api';
import { Busy } from './components';

export function SettingsPanel({
  settings,
  health,
  notify,
  refresh,
}: {
  settings: Settings;
  health: Health | null;
  notify: Notify;
  refresh: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [folders, setFolders] = useState(settings.watch_folders.join('\n'));
  const [models, setModels] = useState<Partial<Record<Provider, ModelCatalogue>>>({});
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const check = () => {
    setChecking(true);
    void Promise.all(
      (Object.keys(providerLabels) as Provider[]).map(async (provider) => {
        const result = await api<ModelCatalogue>(`/models?provider=${provider}`);
        return [provider, result] as const;
      }),
    )
      .then((entries) => setModels(Object.fromEntries(entries)))
      .catch((e) => notify(errorMessage(e), true))
      .finally(() => setChecking(false));
  };
  useEffect(check, []);
  async function save() {
    setBusy(true);
    try {
      const next = await api<Settings>('/settings', 'PUT', {
        ...draft,
        watch_folders: folders
          .split('\n')
          .map((x) => x.trim())
          .filter(Boolean),
      });
      setDraft(next);
      refresh();
      notify('Workspace settings saved. Analysis changes apply to future imports.');
      check();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="settings-page">
      <div className="settings-intro">
        <Settings2 size={23} />
        <div>
          <h2>Your workspace, your machine.</h2>
          <p>Keep your library on this machine. Choose local models or your installed AI CLIs.</p>
        </div>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <section className="settings-section">
          <div>
            <ScanLine size={22} />
            <h3>Footage preparation</h3>
            <p>These settings apply to new footage. Existing select ranges are preserved.</p>
          </div>
          <div className="field-stack">
            <div className="split-fields">
              <label>
                Cut sensitivity · threshold
                <input
                  type="number"
                  step="0.01"
                  min="0.05"
                  max="0.9"
                  required
                  value={draft.scene_threshold}
                  onChange={(e) => setDraft({ ...draft, scene_threshold: Number(e.target.value) })}
                />
              </label>
              <label>
                Minimum shot · seconds
                <input
                  type="number"
                  step="0.25"
                  min="0.25"
                  max="10"
                  required
                  value={draft.min_shot_seconds}
                  onChange={(e) => setDraft({ ...draft, min_shot_seconds: Number(e.target.value) })}
                />
              </label>
            </div>
            <p className="hint">
              Lower thresholds find more cuts, but can mistake movement or lighting changes for a new angle.
            </p>
            <div className="split-fields">
              <label>
                Maximum select · seconds
                <input
                  type="number"
                  min="2"
                  max="60"
                  required
                  value={draft.select_seconds}
                  onChange={(e) => setDraft({ ...draft, select_seconds: Number(e.target.value) })}
                />
              </label>
              <label>
                Proxy height
                <select
                  value={draft.proxy_height}
                  onChange={(e) => setDraft({ ...draft, proxy_height: Number(e.target.value) })}
                >
                  <option value={360}>360p · light</option>
                  <option value={540}>540p</option>
                  <option value={720}>720p · balanced</option>
                  <option value={1080}>1080p</option>
                </select>
              </label>
            </div>
          </div>
        </section>
        <section className="settings-section">
          <div>
            <Cpu size={22} />
            <h3>Intelligence</h3>
            <p>
              Choose a provider for each task. Ollama runs locally; CLI providers use their own login and
              model service.
            </p>
            <p className="hint">
              Using T3 Code? Select the same underlying Codex, Claude Code or OpenCode provider here. T3 Code
              itself is an agent interface, not an inference model.
            </p>
          </div>
          <div className="field-stack">
            {(['vision', 'planning'] as const).map((role) => {
              const provider = draft[`${role}_provider`];
              const catalogue = models[provider];
              return (
                <div className="field-stack" key={role}>
                  <div className="split-fields">
                    <label>
                      {role === 'vision' ? 'Vision provider' : 'Planning provider'}
                      <select
                        value={provider}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            [`${role}_provider`]: e.target.value as Provider,
                            [`${role}_model`]: '',
                          })
                        }
                      >
                        {(Object.entries(providerLabels) as [Provider, string][]).map(([id, label]) => (
                          <option key={id} value={id}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {role === 'vision' ? 'Vision model' : 'Planning model'}
                      <input
                        list={`${role}-models`}
                        maxLength={200}
                        placeholder={
                          role === 'vision' ? 'None, shot detection only' : 'None, offline assembly'
                        }
                        value={draft[`${role}_model`]}
                        onChange={(e) => setDraft({ ...draft, [`${role}_model`]: e.target.value })}
                      />
                    </label>
                  </div>
                  <datalist id={`${role}-models`}>
                    {catalogue?.models.map((model) => (
                      <option key={model} value={model} />
                    ))}
                  </datalist>
                  <p className="hint" aria-live="polite">
                    {catalogue ? catalogue.detail : `Checking ${providerLabels[provider]}…`}
                    {role === 'vision' && ' Choose a model that accepts images.'}
                  </p>
                </div>
              );
            })}
            {(draft.vision_provider === 'ollama' || draft.planning_provider === 'ollama') && (
              <label>
                Ollama endpoint
                <input
                  type="url"
                  required
                  value={draft.ollama_url}
                  onChange={(e) => setDraft({ ...draft, ollama_url: e.target.value })}
                />
              </label>
            )}
            <p className="hint">
              Enter a model ID or choose a suggestion. Leave a model empty to disable that task. Switching
              providers clears the model. Save before checking a changed Ollama endpoint.
            </p>
            <button type="button" className="fit" onClick={check} disabled={checking}>
              <RefreshCw size={14} /> {checking ? 'Checking providers…' : 'Refresh providers and models'}
            </button>
            <label className="check-row">
              <input
                type="checkbox"
                checked={draft.allow_remote_ai}
                onChange={(e) => setDraft({ ...draft, allow_remote_ai: e.target.checked })}
              />
              <span>
                Allow CLI provider data sharing
                <small>
                  Send creative briefs, catalogue descriptions and sampled frames through the selected CLI.
                  Its account, usage limits and data policy apply. Originals stay on disk.
                </small>
              </span>
            </label>
            <label>
              Model timeout · seconds
              <input
                type="number"
                min={10}
                max={1800}
                required
                value={draft.model_timeout_seconds}
                onChange={(e) => setDraft({ ...draft, model_timeout_seconds: Number(e.target.value) })}
              />
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={draft.transcribe}
                onChange={(e) => setDraft({ ...draft, transcribe: e.target.checked })}
              />
              <span>
                Transcribe speech locally
                <small>Requires uv sync --extra transcription. First use downloads model weights.</small>
              </span>
            </label>
            {draft.transcribe && (
              <label>
                Whisper model
                <select
                  value={draft.transcription_model}
                  onChange={(e) => setDraft({ ...draft, transcription_model: e.target.value })}
                >
                  <option>tiny</option>
                  <option>base</option>
                  <option>small</option>
                  <option>medium</option>
                  <option>large-v3</option>
                </select>
              </label>
            )}
          </div>
        </section>
        <section className="settings-section">
          <div>
            <Folder size={22} />
            <h3>Watched folders</h3>
            <p>
              Drop footage into a watched folder to queue it automatically. Files must stop changing for 15
              seconds.
            </p>
          </div>
          <div>
            <label>
              Absolute paths · one per line
              <textarea
                rows={3}
                placeholder="/Volumes/Footage/Inbox"
                value={folders}
                onChange={(e) => setFolders(e.target.value)}
              />
            </label>
            <p className="hint">
              Use folders outside the Vidac workspace. Network shares must be mounted locally. Failed imports
              can be retried from the queue.
            </p>
          </div>
        </section>
        <div className="settings-bottom">
          <div className="hint">
            Workspace <code>{health?.workspace}</code>
            <br />
            FFmpeg {health?.ffmpeg ? 'available' : 'missing'} · ffprobe{' '}
            {health?.ffprobe ? 'available' : 'missing'} · v{health?.version}
          </div>
          <button className="primary" disabled={busy}>
            {busy ? (
              <Busy>Saving…</Busy>
            ) : (
              <>
                <Check size={16} /> Save settings
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
