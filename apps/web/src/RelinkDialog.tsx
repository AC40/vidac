import { useState } from 'react';
import { api, errorMessage, type Asset, type Notify } from './api';
import { Busy, Modal } from './components';

export function RelinkDialog({
  assets,
  initialAsset,
  close,
  queued,
  refresh,
  notify,
}: {
  assets: Asset[];
  initialAsset: Asset | null;
  close: () => void;
  queued: () => void;
  refresh: () => void;
  notify: Notify;
}) {
  const [assetId, setAssetId] = useState(initialAsset?.id || '');
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const missing = assets.filter((asset) => !asset.online);
  async function submit() {
    setBusy(true);
    setError('');
    try {
      await api(assetId ? `/assets/${assetId}/relink` : '/relink', 'POST', { path: path.trim() });
      refresh();
      notify(
        assetId
          ? 'Original media relinked.'
          : 'Searching for missing originals. Results appear in the activity queue.',
      );
      close();
      if (!assetId) queued();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Relink original media" close={close}>
      <p className="muted">
        {missing.length} original{missing.length === 1 ? '' : 's'} missing. Your clips and saved edits are
        preserved.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="field-stack">
          <label>
            Relink method
            <select
              value={assetId}
              disabled={busy}
              onChange={(event) => {
                setAssetId(event.target.value);
                setPath('');
                setError('');
              }}
            >
              <option value="">Search a folder for all missing originals</option>
              {assets.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  Choose file for {asset.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {assetId ? 'Original file path' : 'Folder to search'}
            <input
              required
              value={path}
              disabled={busy}
              placeholder={assetId ? '/Volumes/Footage/video.mov' : '/Volumes/New drive/Footage'}
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
          <p className="hint">
            {assetId
              ? 'Choose the exact original, even if its name has changed.'
              : 'Includes all subfolders and renamed files. Only exact file contents are matched. Existing online sources stay linked.'}{' '}
            Use a path on the machine running Vidac. For automatic recovery, add the new location to Watched
            folders in Settings.
          </p>
          {missing.length > 0 && (
            <details>
              <summary>Missing originals</summary>
              {missing.map((asset) => (
                <p key={asset.id}>
                  {asset.name}
                  <br />
                  <code style={{ overflowWrap: 'anywhere' }}>{asset.path}</code>
                </p>
              ))}
            </details>
          )}
          {error && <p role="alert">{error}</p>}
        </div>
        <div className="modal-footer">
          <button type="button" onClick={close}>
            Close
          </button>
          <button className="primary" disabled={busy || !path.trim()}>
            {busy ? <Busy>Checking media…</Busy> : assetId ? 'Relink file' : 'Search and relink'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
