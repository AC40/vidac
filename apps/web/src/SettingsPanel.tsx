import { useEffect, useState } from 'react';
import { Check, Cpu, Folder, RefreshCw, ScanLine, Settings2 } from 'lucide-react';
import { api, errorMessage, type Health, type Notify, type Settings } from './api';
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
  const [models, setModels] = useState<{ connected: boolean; models: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const check = () => {
    void api<{ connected: boolean; models: string[] }>('/models')
      .then(setModels)
      .catch((e) => notify(errorMessage(e), true));
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
          <p>Media stays local. Configure the preparation engine and opt into on-device intelligence.</p>
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
            <h3>Local intelligence</h3>
            <p>
              Ollama provides optional visual descriptions and creative planning. No model is downloaded by
              Vidac.
            </p>
            <span className={`connection ${models?.connected ? 'connected' : ''}`}>
              <span className="status-dot" />
              {models === null
                ? 'Checking Ollama…'
                : models.connected
                  ? 'Ollama connected'
                  : 'Ollama not connected'}
            </span>
          </div>
          <div className="field-stack">
            <label>
              Ollama endpoint
              <input
                type="url"
                required
                value={draft.ollama_url}
                onChange={(e) => setDraft({ ...draft, ollama_url: e.target.value })}
              />
            </label>
            <div className="split-fields">
              <label>
                Vision model
                <input
                  list="installed-models"
                  placeholder="None — shot detection only"
                  value={draft.vision_model}
                  onChange={(e) => setDraft({ ...draft, vision_model: e.target.value })}
                />
              </label>
              <label>
                Planning model
                <input
                  list="installed-models"
                  placeholder="None — offline assembly"
                  value={draft.planning_model}
                  onChange={(e) => setDraft({ ...draft, planning_model: e.target.value })}
                />
              </label>
            </div>
            <datalist id="installed-models">
              {models?.models.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
            <p className="hint">
              Vision requires an image-capable model, for example qwen3-vl:8b. Install with Ollama first.
              Leave fields empty to disable AI. Save before checking a changed endpoint.
            </p>
            <button type="button" className="fit" onClick={check}>
              <RefreshCw size={14} /> Refresh installed models
            </button>
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
