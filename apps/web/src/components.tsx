import { isAIProvenance, providerLabels } from './api';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, Check, Film, LoaderCircle, Sparkles, Upload, X } from 'lucide-react';
import { api, errorMessage, looks, rate, type Clip, type Job, type Notify, type Settings } from './api';

export function Modal({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={wide ? 'modal wide' : 'modal'}
      onCancel={close}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="modal-head">
        <h2>{title}</h2>
        <button className="icon-button" onClick={close} aria-label="Close dialog">
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function Busy({ children }: { children: ReactNode }) {
  return (
    <>
      <LoaderCircle className="spin" size={16} />
      {children}
    </>
  );
}

export function ImportDialog({
  close,
  notify,
  refresh,
}: {
  close: () => void;
  notify: Notify;
  refresh: () => void;
}) {
  const [paths, setPaths] = useState('');
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const submit = async (files?: FileList | File[]) => {
    setBusy(true);
    try {
      let result: { jobs: Job[] };
      if (files?.length) {
        const data = new FormData();
        Array.from(files).forEach((file) => data.append('files', file));
        result = await api('/uploads', 'POST', data);
      } else {
        result = await api('/ingest', 'POST', {
          paths: paths
            .split('\n')
            .map((x) => x.trim())
            .filter(Boolean),
        });
      }
      notify(`${result.jobs.length} file${result.jobs.length === 1 ? '' : 's'} queued for preparation.`);
      refresh();
      close();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Bring in your footage" close={close}>
      <p className="muted">
        Originals stay intact. Vidac creates lightweight proxies and reusable selects in the background.
      </p>
      <div
        className={`dropzone ${busy ? 'disabled' : ''}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (!busy) void submit(e.dataTransfer.files);
        }}
      >
        <Upload size={28} />
        <h3>Drop your videos here</h3>
        <p>or choose files to copy into the workspace</p>
        <button disabled={busy} onClick={() => input.current?.click()}>
          Choose files <ArrowUpRight size={15} />
        </button>
        <input
          ref={input}
          type="file"
          multiple
          accept="video/*,.mkv,.mts,.m2ts,.mxf"
          hidden
          onChange={(e) => {
            if (e.target.files?.length) void submit(e.target.files);
          }}
        />
      </div>
      <div className="divider-label">OR REFERENCE LOCAL FILES</div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label>
          File or folder paths
          <textarea
            rows={3}
            placeholder={'/Volumes/Footage/Morning routine\n/home/you/Videos/desk.mp4'}
            value={paths}
            onChange={(e) => setPaths(e.target.value)}
          />
        </label>
        <p className="hint">
          One absolute path per line, on the machine running Vidac. Folders are scanned recursively. No
          original files are copied.
        </p>
        <div className="modal-footer">
          <button type="button" className="ghost" onClick={close}>
            Close
          </button>
          <button className="primary" disabled={busy || !paths.trim()}>
            {busy ? (
              <Busy>Importing…</Busy>
            ) : (
              <>
                Prepare footage <ArrowUpRight size={16} />
              </>
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ClipInspector({
  clip,
  close,
  notify,
  refresh,
}: {
  clip: Clip;
  close: () => void;
  notify: Notify;
  refresh: () => void;
}) {
  const [draft, setDraft] = useState(clip);
  const [tags, setTags] = useState(clip.tags.join(', '));
  const [busy, setBusy] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const fps = rate(clip.fps);
  const seek = () => {
    if (video.current) video.current.currentTime = draft.start_frame / fps;
  };
  async function save() {
    setBusy(true);
    try {
      await api(`/clips/${clip.id}`, 'PATCH', {
        title: draft.title,
        description: draft.description,
        tags: tags
          .split(',')
          .map((x) => x.trim())
          .filter(Boolean),
        start_frame: draft.start_frame,
        end_frame: draft.end_frame,
      });
      notify('Select saved. Existing edit revisions are unchanged.');
      refresh();
      close();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  }
  async function analyze() {
    setBusy(true);
    try {
      await api(`/clips/${clip.id}/analyze`, 'POST');
      notify('Visual analysis queued.');
      refresh();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Clip inspector" close={close} wide>
      <div className="inspector-grid">
        <div>
          <video
            className="clip-player"
            ref={video}
            src={`/api/media/${clip.asset_id}/proxy`}
            controls
            preload="metadata"
            onLoadedMetadata={seek}
            onTimeUpdate={() => {
              const v = video.current;
              if (v && !v.paused && v.currentTime >= draft.end_frame / fps) {
                v.pause();
                seek();
              }
            }}
          />
          <div className="player-meta">
            <span>{clip.asset_name}</span>
            <span>
              {clip.width} × {clip.height} · {fps.toFixed(2)} fps
            </span>
          </div>
          <img
            className="waveform"
            src={`/api/media/${clip.asset_id}/waveform`}
            alt="Source audio waveform"
            onError={(e) => {
              e.currentTarget.style.display = 'none';
            }}
          />
          <div className="split-fields">
            <label>
              In · source frame
              <input
                type="number"
                min={clip.shot_start}
                max={draft.end_frame - 1}
                value={draft.start_frame}
                onChange={(e) => setDraft({ ...draft, start_frame: Number(e.target.value) })}
                onBlur={seek}
              />
            </label>
            <label>
              Out · exclusive frame
              <input
                type="number"
                min={draft.start_frame + 1}
                max={clip.shot_end}
                value={draft.end_frame}
                onChange={(e) => setDraft({ ...draft, end_frame: Number(e.target.value) })}
              />
            </label>
          </div>
          <p className="hint">
            {(draft.start_frame / fps).toFixed(2)}s → {(draft.end_frame / fps).toFixed(2)}s ·{' '}
            {((draft.end_frame - draft.start_frame) / fps).toFixed(2)}s select. Shot handles:{' '}
            {clip.shot_start}–{clip.shot_end}.
          </p>
        </div>
        <div className="field-stack">
          <span className="eyebrow">
            {isAIProvenance(clip.provenance)
              ? 'AI-described · review suggested'
              : 'Shot detection · unclassified'}
          </span>
          <label>
            Title
            <input
              maxLength={200}
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </label>
          <label>
            Description
            <textarea
              rows={4}
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            />
          </label>
          <label>
            Tags
            <input
              placeholder="desk, morning, close-up"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
            />
          </label>
          <div className="annotation">
            {Object.entries(clip.annotation)
              .filter(
                ([key, value]) =>
                  ['activity', 'setting', 'shot_size', 'camera_motion', 'quality_notes'].includes(key) &&
                  value,
              )
              .map(([key, value]) => (
                <p key={key}>
                  <span>{key.replaceAll('_', ' ')}</span>
                  {String(value)}
                </p>
              ))}
          </div>
          <button onClick={() => void analyze()} disabled={busy}>
            <Sparkles size={15} /> Analyze with AI
          </button>
        </div>
      </div>
      <div className="modal-footer">
        <span className="hint">Virtual select — the original is never modified.</span>
        <button className="primary" onClick={() => void save()} disabled={busy || !draft.title.trim()}>
          {busy ? (
            <Busy>Working…</Busy>
          ) : (
            <>
              <Check size={16} /> Save select
            </>
          )}
        </button>
      </div>
    </Modal>
  );
}

export function CreateEditDialog({
  selected,
  settings,
  close,
  notify,
  created,
}: {
  selected: string[];
  settings: Settings | null;
  close: () => void;
  notify: Notify;
  created: (job: Job) => void;
}) {
  const planningEnabled = Boolean(
    settings?.planning_model && (settings.planning_provider === 'ollama' || settings.allow_remote_ai),
  );
  const [draft, setDraft] = useState({
    name: 'Morning, in motion',
    brief: '',
    duration: 30,
    pace: 'balanced',
    look: 'neutral',
    aspect: '16:9',
    use_ai: planningEnabled,
  });
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    try {
      const job = await api<Job>('/edits', 'POST', { ...draft, clip_ids: selected });
      created(job);
      close();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Start a rough cut" close={close}>
      <p className="muted">
        {selected.length
          ? `Working with ${selected.length} selected clips.`
          : 'Working with your library. Keywords in your brief help find relevant clips.'}{' '}
        Every cut stays editable.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="field-stack"
      >
        <label>
          Edit name
          <input
            required
            maxLength={200}
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>
        <label>
          Creative direction
          <textarea
            rows={4}
            placeholder="A quiet morning. Open on a wide desk shot, move into details of typing, then transition into brushing teeth. Leave room to breathe."
            value={draft.brief}
            onChange={(e) => setDraft({ ...draft, brief: e.target.value })}
          />
        </label>
        <div className="split-fields">
          <label>
            Target length · seconds
            <input
              type="number"
              min={1}
              max={1800}
              required
              value={draft.duration}
              onChange={(e) => setDraft({ ...draft, duration: Number(e.target.value) })}
            />
          </label>
          <label>
            Pacing
            <select value={draft.pace} onChange={(e) => setDraft({ ...draft, pace: e.target.value })}>
              <option value="calm">Calm / longer takes</option>
              <option value="balanced">Balanced</option>
              <option value="energetic">Energetic / quick cuts</option>
            </select>
          </label>
        </div>
        <div className="split-fields">
          <label>
            Starting look
            <select value={draft.look} onChange={(e) => setDraft({ ...draft, look: e.target.value })}>
              {looks.map((look) => (
                <option key={look}>{look}</option>
              ))}
            </select>
          </label>
          <label>
            Canvas
            <select value={draft.aspect} onChange={(e) => setDraft({ ...draft, aspect: e.target.value })}>
              <option>16:9</option>
              <option>9:16</option>
              <option>1:1</option>
            </select>
          </label>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={draft.use_ai}
            disabled={!planningEnabled}
            onChange={(e) => setDraft({ ...draft, use_ai: e.target.checked })}
          />
          <span>
            Use AI for creative planning{' '}
            <small>
              {settings?.planning_model
                ? `${providerLabels[settings.planning_provider]} · ${settings.planning_model}${planningEnabled ? '' : ' · Enable CLI data sharing in Settings'}`
                : 'Choose a planning model in Settings to enable.'}
            </small>
          </span>
        </label>
        {!draft.use_ai && (
          <p className="notice">
            Offline mode assembles by keywords, selection order and pacing. It does not interpret visual
            storytelling or complex direction.
          </p>
        )}
        <div className="modal-footer">
          <span className="hint">
            {draft.use_ai && settings?.planning_provider !== 'ollama'
              ? 'The selected CLI receives your brief and catalogue descriptions.'
              : 'This edit is planned on your machine.'}
          </span>
          <button className="primary" disabled={busy}>
            {busy ? (
              <Busy>Starting…</Busy>
            ) : (
              <>
                <Film size={16} /> Build rough cut
              </>
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
}
