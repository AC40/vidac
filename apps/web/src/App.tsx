import { isAIProvenance } from './api';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronRight,
  Clapperboard,
  Clock3,
  Film,
  FolderOpen,
  Grid2X2,
  HardDrive,
  Layers3,
  LoaderCircle,
  Plus,
  Search,
  Settings2,
  Sparkles,
  Star,
  Upload,
  X,
} from 'lucide-react';
import {
  api,
  errorMessage,
  seconds,
  time,
  type Asset,
  type Clip,
  type Edit,
  type Health,
  type Job,
  type Notify,
  type Settings,
  type Stats,
} from './api';
import { ClipInspector, CreateEditDialog, ImportDialog } from './components';
import { EditWorkspace } from './EditWorkspace';
import { SettingsPanel } from './SettingsPanel';
import { RelinkDialog } from './RelinkDialog';

type Page = 'library' | 'edits' | 'queue' | 'settings';
const titles = {
  library: 'Your footage. Ready for the story.',
  edits: 'Find the rhythm. Shape the story.',
  queue: 'Preparation, in progress.',
  settings: 'Made to work your way.',
};
const labels = {
  library: 'Clip library',
  edits: 'Rough cuts',
  queue: 'Activity queue',
  settings: 'Settings',
};

export function App() {
  const [page, setPage] = useState<Page>('library');
  const [health, setHealth] = useState<Health | null>(null);
  const [stats, setStats] = useState<Stats>({
    assets: 0,
    clips: 0,
    unclassified: 0,
    favorites: 0,
    duration: 0,
    edits: 0,
    active_jobs: 0,
  });
  const [clips, setClips] = useState<Clip[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [edits, setEdits] = useState<Edit[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [connectionError, setConnectionError] = useState('');
  const [ready, setReady] = useState(false);
  const [query, setQuery] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [source, setSource] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [inspected, setInspected] = useState<Clip | null>(null);
  const [dialog, setDialog] = useState<'import' | 'edit' | 'relink' | null>(null);
  const [relinkAsset, setRelinkAsset] = useState<Asset | null>(null);
  const [selectedEdit, setSelectedEdit] = useState('');
  const [pendingEdit, setPendingEdit] = useState('');
  const [toast, setToast] = useState<{ message: string; error: boolean } | null>(null);
  const [searchResults, setSearchResults] = useState<Clip[] | null>(null);
  const [draftDirty, setDraftDirty] = useState(false);
  const searching = useRef(0);
  const fetching = useRef(false);
  const notify: Notify = useCallback((message, error = false) => setToast({ message, error }), []);

  const refresh = useCallback(async () => {
    if (fetching.current) return;
    fetching.current = true;
    try {
      const [h, s, c, a, j, e, config] = await Promise.all([
        api<Health>('/health'),
        api<Stats>('/stats'),
        api<Clip[]>('/clips'),
        api<Asset[]>('/assets'),
        api<Job[]>('/jobs'),
        api<Edit[]>('/edits'),
        api<Settings>('/settings'),
      ]);
      setHealth(h);
      setStats(s);
      setClips(c);
      setAssets(a);
      setJobs(j);
      setEdits(e);
      setSettings(config);
      setConnectionError('');
      setReady(true);
    } catch (error) {
      setConnectionError(errorMessage(error));
    } finally {
      fetching.current = false;
    }
  }, []);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), toast.error ? 12000 : 5500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const serial = ++searching.current;
    if (!query.trim() && !source && !favorite) {
      setSearchResults(null);
      return;
    }
    const timer = setTimeout(() => {
      void api<Clip[]>(
        `/clips?q=${encodeURIComponent(query)}&asset_id=${encodeURIComponent(source)}&favorite=${favorite}`,
      )
        .then((result) => {
          if (serial === searching.current) setSearchResults(result);
        })
        .catch((error) => notify(errorMessage(error), true));
    }, 200);
    return () => clearTimeout(timer);
  }, [query, source, favorite, clips, notify]);
  useEffect(() => {
    if (!pendingEdit) return;
    const job = jobs.find((j) => j.id === pendingEdit);
    if (job?.status === 'succeeded' && job.result?.edit_id) {
      setPendingEdit('');
      if (!draftDirty) {
        setSelectedEdit(job.result.edit_id);
        setPage('edits');
      }
      notify('Your first pass is ready to shape.');
    } else if (job?.status === 'failed' || job?.status === 'cancelled') {
      setPendingEdit('');
      notify(job.error || 'Edit cancelled.', true);
    }
  }, [jobs, pendingEdit, notify, draftDirty]);

  const filtered = (searchResults || clips).filter(
    (c) => (!favorite || c.favorite) && (!source || c.asset_id === source),
  );
  const active = jobs.filter((j) => j.status === 'running' || j.status === 'queued');
  const sourceAsset = assets.find((a) => a.id === source);
  const canLeaveDraft = () =>
    !draftDirty || window.confirm('Discard unsaved edit changes? Save a version to keep them.');
  const navigate = (next: Page) => {
    if (next === page || canLeaveDraft()) setPage(next);
  };
  async function run(path: string, message: string) {
    try {
      await api(path, 'POST');
      notify(message);
      void refresh();
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }
  async function analyzeLibrary() {
    if (!settings?.vision_model) {
      notify('Choose a classification provider and model in Settings first.', true);
      return;
    }
    const scope = sourceAsset ? sourceAsset.name : `${stats.unclassified} unclassified selects`;
    const provider = `${settings.vision_provider}:${settings.vision_model}`;
    if (
      !window.confirm(
        `Analyze ${scope} with ${provider}? Sampled frames will be sent through the configured CLI and may incur provider charges.`,
      )
    )
      return;
    try {
      await api<Job>('/analyze', 'POST', { asset_ids: sourceAsset ? [sourceAsset.id] : [] });
      notify('Library analysis queued. Track progress and reported cost in Activity.');
      navigate('queue');
      void refresh();
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }
  async function toggleFavorite(clip: Clip) {
    try {
      await api(`/clips/${clip.id}`, 'PATCH', { favorite: !clip.favorite });
      void refresh();
    } catch (error) {
      notify(errorMessage(error), true);
    }
  }
  function toggleSelected(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]));
  }
  function relink(asset: Asset | null) {
    setRelinkAsset(asset);
    setDialog('relink');
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate('library');
          }}
          aria-label="Vidac home"
        >
          <span className="brand-mark">
            <span />
            <span />
            <span />
          </span>
          vidac<span className="brand-period">.</span>
        </a>
        <span className="workspace-label">CREATIVE WORKSPACE</span>
        <nav aria-label="Main navigation">
          <button
            className={page === 'library' ? 'nav-item active' : 'nav-item'}
            onClick={() => navigate('library')}
          >
            <Grid2X2 size={18} /> Clip library<span>{stats.clips || '—'}</span>
          </button>
          <button
            className={page === 'edits' ? 'nav-item active' : 'nav-item'}
            onClick={() => navigate('edits')}
          >
            <Clapperboard size={18} /> Rough cuts<span>{stats.edits || '—'}</span>
          </button>
          <button
            className={page === 'queue' ? 'nav-item active' : 'nav-item'}
            onClick={() => navigate('queue')}
          >
            <Layers3 size={18} /> Activity queue
            {active.length > 0 && <span className="count-badge">{active.length}</span>}
          </button>
        </nav>
        <div className="sidebar-section">
          <span className="eyebrow">COLLECTIONS</span>
          <button
            className={`collection ${favorite && page === 'library' ? 'selected' : ''}`}
            onClick={() => {
              setFavorite(!favorite);
              setSource('');
              navigate('library');
            }}
          >
            <Star size={15} /> Favorites <span>{stats.favorites}</span>
          </button>
        </div>
        <div className="sidebar-section sources">
          <div className="section-heading">
            <span>SOURCE FOOTAGE</span>
            <button className="icon-button" onClick={() => setDialog('import')} aria-label="Import footage">
              <Plus size={15} />
            </button>
          </div>
          {assets.length > 0 && (
            <button onClick={() => relink(null)}>
              Relink media
              {assets.some((asset) => !asset.online)
                ? ` · ${assets.filter((asset) => !asset.online).length} missing`
                : ''}
            </button>
          )}
          {!assets.length && <p className="hint">A home for every angle.</p>}
          {assets.map((asset) => (
            <button
              title={asset.path}
              key={asset.id}
              className={`source-link ${source === asset.id && page === 'library' ? 'selected' : ''}`}
              onClick={() => {
                setSource(source === asset.id ? '' : asset.id);
                setFavorite(false);
                navigate('library');
              }}
            >
              <Film size={15} />
              <span>{asset.name}</span>
              {!asset.online && <span className="offline-dot" title="Original offline" />}
            </button>
          ))}
        </div>
        <div className="sidebar-bottom">
          <div className="local-card">
            <HardDrive size={17} />
            <div>
              Local by design<small>Your footage stays yours.</small>
            </div>
            <span className="status-dot" />
          </div>
          <button
            className={page === 'settings' ? 'nav-item active' : 'nav-item'}
            onClick={() => navigate('settings')}
          >
            <Settings2 size={18} /> Settings
          </button>
          <div className="version">
            VIDAC / {health?.version || '0.1.0'} <span>EARLY ACCESS</span>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <strong>{labels[page]}</strong>
          </div>
          <div className="topbar-status">
            <span className={`status-dot ${connectionError ? 'warning' : ''}`} />
            {connectionError
              ? 'Reconnecting'
              : active.length
                ? `Preparing ${active.length} job${active.length === 1 ? '' : 's'}`
                : draftDirty
                  ? 'Unsaved edit changes'
                  : 'Local workspace'}
          </div>
        </header>
        <div className="page-content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {page === 'library' ? 'LESS SORTING. MORE CREATING.' : 'YOUR CREATIVE WORKSPACE'}
              </span>
              <h1>{titles[page]}</h1>
              <p>
                {page === 'library'
                  ? 'Turn the moments you captured into the clips you’ll reach for.'
                  : page === 'edits'
                    ? 'An editable first pass, with room for your point of view.'
                    : page === 'queue'
                      ? 'Every import, analysis and export — with a clear trail.'
                      : 'A few good defaults. Complete control over the process.'}
              </p>
            </div>
            {page === 'library' ? (
              <div className="heading-actions">
                <button
                  onClick={() => void analyzeLibrary()}
                  disabled={!stats.unclassified || active.some((job) => job.kind === 'analyze-library')}
                  title={
                    settings?.vision_model
                      ? 'Describe unclassified selects in 30-second source windows'
                      : 'Configure a classification model in Settings first'
                  }
                >
                  <Sparkles size={16} /> Analyze unclassified
                </button>
                <button className="primary" onClick={() => setDialog('import')}>
                  <Plus size={17} /> Import footage
                </button>
              </div>
            ) : page === 'edits' ? (
              <button className="primary" disabled={!clips.length} onClick={() => setDialog('edit')}>
                <Plus size={17} /> New rough cut
              </button>
            ) : null}
          </div>
          {connectionError && (
            <div className="error-banner" role="alert">
              Could not reach the local API: {connectionError}. Check that Vidac is running on port 8741.
            </div>
          )}
          {health && (!health.ffmpeg || !health.ffprobe) && (
            <div className="error-banner">
              FFmpeg or ffprobe is missing. Install both on PATH before importing footage.
            </div>
          )}
          {page === 'library' && (
            <>
              <div className="library-summary">
                <div>
                  <span className="summary-number">{stats.assets.toString().padStart(2, '0')}</span>
                  <span>source files</span>
                </div>
                <div>
                  <span className="summary-number">{stats.clips.toString().padStart(2, '0')}</span>
                  <span>ready-to-use selects</span>
                </div>
                <div>
                  <span className="summary-number">{time(stats.duration)}</span>
                  <span>footage organized</span>
                </div>
                <div className="summary-note">
                  <Sparkles size={18} />
                  <span>
                    {settings?.vision_model ? 'Vision analysis configured' : 'Shot detection, ready to go'}
                    <small>
                      {settings?.vision_model
                        ? `${stats.unclassified} unclassified · ${settings.vision_model}`
                        : 'Choose a classification provider and model for descriptions'}
                    </small>
                  </span>
                </div>
              </div>
              <div className="library-toolbar">
                <div className="tabs">
                  <button className={!favorite ? 'active' : ''} onClick={() => setFavorite(false)}>
                    All selects <span>{stats.clips}</span>
                  </button>
                  <button className={favorite ? 'active' : ''} onClick={() => setFavorite(true)}>
                    <Star size={14} /> Favorites
                  </button>
                </div>
                <div className="search-field">
                  <Search size={16} />
                  <input
                    aria-label="Search clips"
                    placeholder="Search scenes, tags, moments…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  {query && (
                    <button className="icon-button" onClick={() => setQuery('')} aria-label="Clear search">
                      <X size={14} />
                    </button>
                  )}
                </div>
              </div>
              {sourceAsset && (
                <div className="source-banner">
                  <FolderOpen size={16} />
                  <div>
                    <strong>{sourceAsset.name}</strong>
                    <small>{sourceAsset.path}</small>
                  </div>
                  {!sourceAsset.online && (
                    <button onClick={() => void relink(sourceAsset)}>Relink original</button>
                  )}
                  <button
                    className="icon-button"
                    aria-label="Clear source filter"
                    onClick={() => setSource('')}
                  >
                    <X size={16} />
                  </button>
                </div>
              )}
              {sourceAsset?.metadata.warnings?.map((warning, i) => (
                <p key={i} className="notice">
                  {warning}
                </p>
              ))}
              {!ready && !connectionError ? (
                <div className="loading">
                  <LoaderCircle className="spin" /> Opening your workspace…
                </div>
              ) : !clips.length ? (
                <div className="empty-state">
                  <div className="empty-art">
                    <div className="film-tile tile-back">
                      <Film size={26} />
                    </div>
                    <div className="film-tile tile-front">
                      <Clapperboard size={34} />
                      <span>YOUR NEXT STORY</span>
                    </div>
                    <div className="art-spark">
                      <Sparkles size={21} />
                    </div>
                  </div>
                  <span className="eyebrow">EVERY STORY STARTS WITH FOOTAGE</span>
                  <h2>A little raw. A lot of possibility.</h2>
                  <p>
                    Bring in a recording. We’ll find the cuts, prepare selects,
                    <br />
                    and give every moment a place in your library.
                  </p>
                  <div className="button-group">
                    <button className="primary" onClick={() => setDialog('import')}>
                      <Upload size={16} /> Bring in footage
                    </button>
                    <button
                      onClick={() =>
                        void run(
                          '/demo',
                          'Synthetic sample queued. This tests the pipeline without downloading media.',
                        )
                      }
                      disabled={active.some((j) => j.kind === 'demo')}
                    >
                      <PlayIcon /> Try a sample
                    </button>
                  </div>
                  <span className="empty-footnote">Originals untouched · Virtual clips · Local storage</span>
                  <div className="workflow">
                    <div>
                      <span>01</span>
                      <strong>Drop it in</strong>
                      <small>One file or a whole folder</small>
                    </div>
                    <ArrowRight size={18} />
                    <div>
                      <span>02</span>
                      <strong>Let it prepare</strong>
                      <small>Shots, selects and previews</small>
                    </div>
                    <ArrowRight size={18} />
                    <div>
                      <span>03</span>
                      <strong>Make it yours</strong>
                      <small>Shape a cut. Open in your editor.</small>
                    </div>
                  </div>
                </div>
              ) : !filtered.length ? (
                <div className="empty-state compact">
                  <Search size={30} />
                  <h2>No selects found.</h2>
                  <p>Try a broader search or clear the source filter.</p>
                  <button
                    onClick={() => {
                      setQuery('');
                      setFavorite(false);
                      setSource('');
                    }}
                  >
                    Show all selects
                  </button>
                </div>
              ) : (
                <>
                  <div className="result-heading">
                    <span>
                      {filtered.length} SELECT{filtered.length === 1 ? '' : 'S'}
                    </span>
                    <span>Virtual clips · originals preserved</span>
                  </div>
                  <div className="clip-grid">
                    {filtered.map((clip) => (
                      <article
                        className={`clip-card ${selected.includes(clip.id) ? 'selected' : ''}`}
                        key={clip.id}
                      >
                        <div className="clip-image">
                          <button
                            className="clip-open"
                            aria-label={`Inspect ${clip.title}`}
                            onClick={() => setInspected(clip)}
                          >
                            <img src={`/api/thumbnails/${clip.id}`} alt={clip.title} loading="lazy" />
                            <span className="play-overlay">
                              <PlayIcon />
                            </span>
                          </button>
                          <button
                            className={`select-check ${selected.includes(clip.id) ? 'checked' : ''}`}
                            aria-label={`Select ${clip.title}`}
                            aria-pressed={selected.includes(clip.id)}
                            onClick={() => toggleSelected(clip.id)}
                          >
                            {selected.includes(clip.id) && <Check size={13} />}
                          </button>
                          <span className="clip-duration">{seconds(clip).toFixed(1)}s</span>
                          <button
                            className={`favorite-button ${clip.favorite ? 'is-favorite' : ''}`}
                            aria-label={`${clip.favorite ? 'Unfavorite' : 'Favorite'} ${clip.title}`}
                            onClick={() => void toggleFavorite(clip)}
                          >
                            <Star size={16} fill={clip.favorite ? 'currentColor' : 'none'} />
                          </button>
                        </div>
                        <button className="clip-title" onClick={() => setInspected(clip)}>
                          {clip.title}
                        </button>
                        <p className="clip-source">
                          {clip.asset_name}{' '}
                          <span>· {isAIProvenance(clip.provenance) ? 'AI described' : 'Unclassified'}</span>
                        </p>
                        <div className="clip-tags">
                          {clip.tags.length ? (
                            clip.tags.slice(0, 3).map((tag) => (
                              <button key={tag} onClick={() => setQuery(tag)}>
                                {tag}
                              </button>
                            ))
                          ) : (
                            <span>Ready for your tags</span>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                  <div className="library-end">
                    <span className="line" />
                    <span>
                      {filtered.length >= 1000
                        ? 'Showing the first 1,000 selects · narrow your search'
                        : 'Every frame, a possibility.'}
                    </span>
                    <span className="line" />
                  </div>
                </>
              )}
              {selected.length > 0 && (
                <div className="selection-bar">
                  <span>
                    <Check size={16} /> {selected.length} selected
                  </span>
                  <button className="ghost" onClick={() => setSelected([])}>
                    Clear
                  </button>
                  <button className="primary" onClick={() => setDialog('edit')}>
                    Build a rough cut <ArrowUpRight size={16} />
                  </button>
                </div>
              )}
            </>
          )}
          {page === 'edits' && (
            <EditWorkspace
              edits={edits}
              selectedId={selectedEdit}
              select={(id) => {
                if (id === selectedEdit || canLeaveDraft()) setSelectedEdit(id);
              }}
              onDirtyChange={setDraftDirty}
              clips={clips}
              create={() => setDialog('edit')}
              notify={notify}
              refresh={() => void refresh()}
            />
          )}
          {page === 'queue' && (
            <div className="queue-page">
              <div className="section-heading">
                <span>
                  {active.length} ACTIVE / {jobs.length} RECENT JOBS
                </span>
                <span className="hint">Queue survives restarts</span>
              </div>
              {!jobs.length && (
                <div className="empty-state compact">
                  <Layers3 size={32} />
                  <h2>A clear runway.</h2>
                  <p>Import footage to start preparing your library.</p>
                  <button onClick={() => setDialog('import')}>
                    Import footage <Plus size={16} />
                  </button>
                </div>
              )}
              {jobs.map((job) => (
                <article className="job-card" key={job.id}>
                  <div className={`job-icon ${job.status}`}>
                    {job.status === 'running' ? (
                      <LoaderCircle className="spin" size={21} />
                    ) : job.status === 'succeeded' ? (
                      <Check size={21} />
                    ) : job.status === 'failed' ? (
                      <X size={21} />
                    ) : (
                      <Clock3 size={21} />
                    )}
                  </div>
                  <div className="job-body">
                    <div className="job-top">
                      <h3>
                        {job.kind === 'demo'
                          ? 'Prepare sample footage'
                          : job.kind === 'ingest'
                            ? 'Prepare footage'
                            : job.kind === 'edit'
                              ? 'Build rough cut'
                              : job.kind === 'relink'
                                ? 'Relink original media'
                                : job.kind === 'annotate'
                                  ? 'Analyze clip'
                                  : job.kind === 'analyze-library'
                                    ? 'Analyze unclassified library'
                                    : `Export ${job.payload.format || 'edit'}`}
                      </h3>
                      <span className={`job-state ${job.status}`}>{job.status}</span>
                    </div>
                    <p>{job.error || job.stage}</p>
                    {job.payload.path !== undefined && <code>{String(job.payload.path)}</code>}
                    {job.status === 'running' && (
                      <div className="progress">
                        <span style={{ width: `${job.progress * 100}%` }} />
                      </div>
                    )}
                    {job.result?.relinked && (
                      <div>
                        <p>
                          {job.result.relinked.length} relinked · {job.result.missing?.length || 0} still
                          missing
                        </p>
                        <details>
                          <summary>Relink results</summary>
                          {job.result.relinked.map((asset) => (
                            <p key={asset.asset_id}>
                              Relinked {asset.name}
                              <br />
                              <code>{asset.path}</code>
                            </p>
                          ))}
                          {job.result.missing?.map((asset) => (
                            <p key={asset.asset_id}>
                              Still missing {asset.name}
                              <br />
                              <code>{asset.path}</code>
                            </p>
                          ))}
                        </details>
                      </div>
                    )}
                    {job.result?.warnings?.map((warning, i) => (
                      <p className="job-warning" key={i}>
                        {warning}
                      </p>
                    ))}
                    {job.kind === 'analyze-library' && job.result?.clips_updated !== undefined && (
                      <div className="analysis-result">
                        <p>
                          {job.result.clips_updated} clips described across {job.result.windows || 0} windows
                          {job.result.reviewed_windows
                            ? ` · ${job.result.reviewed_windows} reviewed by fallback`
                            : ''}
                          {job.result.failed_windows ? ` · ${job.result.failed_windows} windows failed` : ''}
                        </p>
                        <small>
                          {job.result.usage?.requests || 0} model requests
                          {job.result.usage?.cost_usd !== undefined
                            ? ` · $${job.result.usage.cost_usd.toFixed(4)} reported cost`
                            : ' · provider did not report cost'}
                        </small>
                      </div>
                    )}
                    <small>{new Date(job.created_at).toLocaleString()}</small>
                  </div>
                  <div className="job-actions">
                    {['queued', 'running'].includes(job.status) && (
                      <button
                        onClick={() =>
                          void run(
                            `/jobs/${job.id}/cancel`,
                            'Cancellation requested. Running model calls may take a moment to return.',
                          )
                        }
                      >
                        Cancel
                      </button>
                    )}
                    {['failed', 'cancelled'].includes(job.status) && (
                      <button onClick={() => void run(`/jobs/${job.id}/retry`, 'Job queued again.')}>
                        Retry
                      </button>
                    )}
                    {job.result?.edit_id && (
                      <button
                        onClick={() => {
                          setSelectedEdit(job.result!.edit_id!);
                          navigate('edits');
                        }}
                      >
                        Open edit <ArrowUpRight size={14} />
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          {page === 'settings' && settings && (
            <SettingsPanel
              settings={settings}
              health={health}
              notify={notify}
              refresh={() => void refresh()}
            />
          )}
        </div>
        {active.length > 0 && page !== 'queue' && (
          <button className="activity-peek" onClick={() => navigate('queue')}>
            <LoaderCircle className="spin" size={15} />
            <span>{active.find((j) => j.status === 'running')?.stage || 'Waiting to prepare…'}</span>
            <span>{active.length} active</span>
            <ChevronRight size={15} />
          </button>
        )}
      </main>
      {dialog === 'relink' && (
        <RelinkDialog
          assets={assets}
          initialAsset={relinkAsset}
          close={() => setDialog(null)}
          queued={() => navigate('queue')}
          refresh={() => void refresh()}
          notify={notify}
        />
      )}
      {dialog === 'import' && (
        <ImportDialog close={() => setDialog(null)} notify={notify} refresh={() => void refresh()} />
      )}
      {dialog === 'edit' && (
        <CreateEditDialog
          selected={selected}
          settings={settings}
          close={() => setDialog(null)}
          notify={notify}
          created={(job) => {
            setPendingEdit(job.id);
            notify('Building your first pass…');
            void refresh();
          }}
        />
      )}
      {inspected && (
        <ClipInspector
          key={inspected.id}
          clip={inspected}
          close={() => setInspected(null)}
          notify={notify}
          refresh={() => void refresh()}
        />
      )}
      {toast && (
        <div className={`toast ${toast.error ? 'error' : ''}`} role={toast.error ? 'alert' : 'status'}>
          {toast.error ? <X size={18} /> : <Check size={18} />}
          <span>{toast.message}</span>
          <button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast(null)}>
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

function PlayIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M5 3L12 8L5 13V3Z" fill="currentColor" />
    </svg>
  );
}
