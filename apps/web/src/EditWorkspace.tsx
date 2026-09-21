import { isAIProvenance } from './api';
import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, Check, Download, Film, History, Play, Plus, Save, Trash2 } from 'lucide-react';
import {
  api,
  errorMessage,
  looks,
  rate,
  time,
  type Clip,
  type Edit,
  type EditItem,
  type Notify,
  type Plan,
} from './api';
import { Busy } from './components';

export function EditWorkspace({
  edits,
  selectedId,
  select,
  clips,
  create,
  notify,
  refresh,
  onDirtyChange,
}: {
  edits: Edit[];
  selectedId: string;
  select: (id: string) => void;
  clips: Clip[];
  create: () => void;
  notify: Notify;
  refresh: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const selected = edits.find((edit) => edit.id === selectedId) || edits[0];
  return (
    <div className="edits-layout">
      <aside className="edit-list">
        <div className="section-heading">
          <span>YOUR EDITS</span>
          <button className="icon-button" onClick={create} aria-label="New rough cut">
            <Plus size={17} />
          </button>
        </div>
        {edits.map((edit) => (
          <button
            key={edit.id}
            onClick={() => select(edit.id)}
            className={`edit-list-item ${selected?.id === edit.id ? 'active' : ''}`}
          >
            <Film size={18} />
            <span>
              {edit.name}
              <small>
                {edit.plan.items.length} shots · v{edit.revision}
              </small>
            </span>
          </button>
        ))}
        {!edits.length && (
          <p className="muted small">
            Your sequences will live here. Start with a direction, then make it yours.
          </p>
        )}
      </aside>
      {selected ? (
        <Timeline
          key={selected.id}
          edit={selected}
          clips={clips}
          notify={notify}
          refresh={refresh}
          onDirtyChange={onDirtyChange}
        />
      ) : (
        <div className="empty-state compact">
          <div className="empty-icon">
            <Film size={32} />
          </div>
          <span className="eyebrow">FROM SELECTS TO A STORY</span>
          <h2>Give your footage a direction.</h2>
          <p>
            Build an editable first pass from your library.
            <br />
            Set the pacing, choose a look, and shape the sequence.
          </p>
          <button className="primary" onClick={create} disabled={!clips.length}>
            <Plus size={17} /> Start a rough cut
          </button>
        </div>
      )}
    </div>
  );
}

function Timeline({
  edit,
  clips,
  notify,
  refresh,
  onDirtyChange,
}: {
  edit: Edit;
  clips: Clip[];
  notify: Notify;
  refresh: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [plan, setPlan] = useState<Plan>(edit.plan);
  const [revision, setRevision] = useState(edit.revision);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(0);
  const [addId, setAddId] = useState('');
  const [format, setFormat] = useState('resolve');
  const [restore, setRestore] = useState('');
  const fps = rate(plan.fps);
  useEffect(() => {
    if (!dirty && edit.revision > revision) {
      setPlan(edit.plan);
      setRevision(edit.revision);
    }
  }, [edit, revision, dirty]);
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const change = (next: Plan) => {
    setPlan(next);
    setDirty(true);
  };
  const durationFrames = (item: EditItem) => {
    const clip = clips.find((c) => c.id === item.clip_id);
    return clip
      ? Math.max(1, Math.round(((item.source_out - item.source_in) / rate(clip.fps)) * fps))
      : item.timeline_frames;
  };
  const total = plan.items.reduce((n, item) => n + durationFrames(item), 0) / fps;
  const item = plan.items[focused];
  const clip = item && clips.find((c) => c.id === item.clip_id);
  const artifact = edit.artifacts.find((a) => a.kind === 'preview' && a.revision === revision);
  const updateItem = (updates: Partial<EditItem>) =>
    change({ ...plan, items: plan.items.map((it, i) => (i === focused ? { ...it, ...updates } : it)) });
  async function save(): Promise<Edit | null> {
    setBusy(true);
    try {
      const saved = await api<Edit>(`/edits/${edit.id}`, 'PUT', { revision, plan });
      setPlan(saved.plan);
      setRevision(saved.revision);
      setDirty(false);
      refresh();
      notify(`Saved version ${saved.revision}.`);
      return saved;
    } catch (error) {
      notify(errorMessage(error), true);
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function exportTo(kind: string) {
    const savedRevision = dirty ? (await save())?.revision : revision;
    if (!savedRevision) return;
    setBusy(true);
    try {
      await api(`/edits/${edit.id}/export`, 'POST', { format: kind, revision: savedRevision });
      notify(
        `${kind === 'preview' ? 'Review render' : 'Editor handoff'} queued. Watch progress in the queue.`,
      );
      refresh();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  }
  function move(delta: number) {
    const index = focused + delta;
    if (index < 0 || index >= plan.items.length) return;
    const next = [...plan.items];
    [next[focused], next[index]] = [next[index], next[focused]];
    change({ ...plan, items: next });
    setFocused(index);
  }
  function addClip() {
    const added = clips.find((c) => c.id === addId);
    if (!added) return;
    change({
      ...plan,
      items: [
        ...plan.items,
        {
          clip_id: added.id,
          asset_id: added.asset_id,
          title: added.title,
          source_in: added.start_frame,
          source_out: added.end_frame,
          timeline_frames: 0,
          look: 'neutral',
          audio: true,
          reason: 'Added manually',
        },
      ],
    });
    setFocused(plan.items.length);
    setAddId('');
  }
  async function loadVersion() {
    if (!restore) return;
    try {
      const old = await api<Edit>(`/edits/${edit.id}?revision=${restore}`);
      change(old.plan);
      setFocused(0);
      notify(`Version ${restore} loaded as a draft. Save to create a new revision.`);
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }
  return (
    <section className="timeline-workspace">
      <div className="timeline-heading">
        <div>
          <span className="eyebrow">
            ROUGH CUT / V{revision}
            {dirty ? ' · UNSAVED CHANGES' : ''}
          </span>
          <input
            aria-label="Edit name"
            className="title-input"
            value={plan.name}
            onChange={(e) => change({ ...plan, name: e.target.value })}
          />
        </div>
        <button onClick={() => void save()} disabled={busy || !dirty}>
          {busy ? (
            <Busy>Working</Busy>
          ) : (
            <>
              <Save size={15} /> Save version
            </>
          )}
        </button>
      </div>
      {edit.revision !== revision && dirty && (
        <p className="notice">
          A newer version exists. Copy your changes before reloading this edit; saving will not overwrite it.
        </p>
      )}
      <div className="edit-preview-grid">
        <div className="review-monitor">
          {artifact && !dirty ? (
            <video key={artifact.id} controls src={`/api/artifacts/${artifact.id}?inline=true`} />
          ) : clip ? (
            <video
              key={`${clip.id}-${focused}`}
              controls
              src={`/api/media/${clip.asset_id}/proxy#t=${item.source_in / rate(clip.fps)},${item.source_out / rate(clip.fps)}`}
            />
          ) : (
            <div className="monitor-placeholder">
              <Film size={35} />
              <span>Add a clip to your sequence</span>
            </div>
          )}
          <div className="monitor-bar">
            <span>
              <span className="status-dot" />
              {artifact && !dirty
                ? `Rendered review · v${revision}`
                : 'Source preview · render to see sequence & look'}
            </span>
            <code>
              {time(total)} / {fps.toFixed(2)} FPS
            </code>
          </div>
        </div>
        <div className="creative-note">
          <span className="eyebrow">THE DIRECTION</span>
          <textarea
            aria-label="Creative direction"
            value={plan.brief}
            onChange={(e) => change({ ...plan, brief: e.target.value })}
            placeholder="Describe the feeling, rhythm and story…"
          />
          <div>
            <span className="pill">
              {plan.width} × {plan.height}
            </span>
            <span className="pill">{plan.items.length} shots</span>
          </div>
          <p className="hint">
            {isAIProvenance(plan.planner)
              ? 'Planned with AI. Review ordering and trims.'
              : 'Offline assembly + your editorial decisions.'}{' '}
            Brief changes are notes for this edit; create a new cut to re-plan.
          </p>
        </div>
      </div>
      <div className="section-heading">
        <span>
          SEQUENCE <em>{time(total)}</em>
        </span>
        <span className="hint">Select a shot to refine</span>
      </div>
      <div className="timeline-strip">
        {plan.items.map((it, index) => (
          <button
            className={`timeline-shot ${index === focused ? 'active' : ''}`}
            key={`${index}-${it.clip_id}`}
            onClick={() => setFocused(index)}
          >
            <img src={`/api/thumbnails/${it.clip_id}`} alt="" />
            <span className="shot-count">{String(index + 1).padStart(2, '0')}</span>
            <div>
              <strong>{it.title}</strong>
              <small>
                {(durationFrames(it) / fps).toFixed(1)}s · {it.look}
              </small>
            </div>
          </button>
        ))}
        {!plan.items.length && <p className="muted">This sequence is empty. Add a clip below.</p>}
      </div>
      <div className="add-to-timeline">
        <select aria-label="Clip to add" value={addId} onChange={(e) => setAddId(e.target.value)}>
          <option value="">Add a clip from the library…</option>
          {clips.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button disabled={!addId || plan.items.length >= 200} onClick={addClip}>
          <Plus size={16} /> Add shot
        </button>
      </div>
      {item && (
        <div className="shot-inspector">
          <div className="shot-inspector-title">
            <div>
              <span className="eyebrow">SHOT {focused + 1}</span>
              <strong>{item.title}</strong>
              {item.reason && <small>{item.reason}</small>}
            </div>
            <div className="button-group">
              <button
                className="icon-button"
                aria-label="Move shot earlier"
                disabled={focused === 0}
                onClick={() => move(-1)}
              >
                <ArrowUp size={16} />
              </button>
              <button
                className="icon-button"
                aria-label="Move shot later"
                disabled={focused === plan.items.length - 1}
                onClick={() => move(1)}
              >
                <ArrowDown size={16} />
              </button>
              <button
                className="icon-button danger"
                aria-label="Remove shot"
                onClick={() => {
                  change({ ...plan, items: plan.items.filter((_, i) => i !== focused) });
                  setFocused(Math.max(0, focused - 1));
                }}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>
          <div className="shot-controls">
            <label>
              In · source frame
              <input
                type="number"
                min={clip?.shot_start || 0}
                max={item.source_out - 1}
                value={item.source_in}
                onChange={(e) => updateItem({ source_in: Number(e.target.value) })}
              />
            </label>
            <label>
              Out · exclusive frame
              <input
                type="number"
                min={item.source_in + 1}
                max={clip?.shot_end}
                value={item.source_out}
                onChange={(e) => updateItem({ source_out: Number(e.target.value) })}
              />
            </label>
            <label>
              Suggested look
              <select
                value={item.look}
                onChange={(e) => updateItem({ look: e.target.value as EditItem['look'] })}
              >
                {looks.map((look) => (
                  <option key={look}>{look}</option>
                ))}
              </select>
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={item.audio}
                onChange={(e) => updateItem({ audio: e.target.checked })}
              />
              Source audio
            </label>
          </div>
        </div>
      )}
      <div className="handoff-panel">
        <div>
          <span className="eyebrow">READY FOR YOUR FINISHING TOUCH</span>
          <h3>Take it into your editor.</h3>
          <p>
            Original-resolution media references. Editable cuts.
            <br />
            Review renders include the selected display-referred look.
          </p>
        </div>
        <div className="handoff-actions">
          <button onClick={() => void exportTo('preview')} disabled={busy || !plan.items.length}>
            <Play size={16} /> Render review
          </button>
          <div className="button-group">
            <select aria-label="Export format" value={format} onChange={(e) => setFormat(e.target.value)}>
              <option value="resolve">DaVinci Resolve bundle</option>
              <option value="fcpxml">Final Cut Pro XML</option>
              <option value="otio">OpenTimelineIO</option>
            </select>
            <button
              className="primary"
              onClick={() => void exportTo(format)}
              disabled={busy || !plan.items.length}
            >
              <Download size={16} /> Export
            </button>
          </div>
        </div>
      </div>
      {edit.artifacts.length > 0 && (
        <div className="artifacts">
          {edit.artifacts.map((a) => (
            <a className="artifact" href={`/api/artifacts/${a.id}`} key={a.id}>
              <Check size={14} />
              <span>
                {a.kind.toUpperCase()} · v{a.revision}
              </span>
              <Download size={14} />
            </a>
          ))}
        </div>
      )}
      <details className="details">
        <summary>Editorial notes & version history</summary>
        <ul>
          {plan.notes.map((note, i) => (
            <li key={i}>{note}</li>
          ))}
        </ul>
        <p className="hint">
          Color suggestions need review on color-managed originals. FCPXML carries look markers, not native
          grading nodes.
        </p>
        <div className="button-group">
          <History size={16} />
          <select aria-label="Version history" value={restore} onChange={(e) => setRestore(e.target.value)}>
            <option value="">Choose a saved revision…</option>
            {edit.versions.map((v) => (
              <option key={v.revision} value={v.revision}>
                Version {v.revision} · {new Date(v.created_at).toLocaleString()}
              </option>
            ))}
          </select>
          <button disabled={!restore || busy} onClick={() => void loadVersion()}>
            Load as draft
          </button>
        </div>
      </details>
    </section>
  );
}
