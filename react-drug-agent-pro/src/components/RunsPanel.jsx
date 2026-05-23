import { useEffect, useMemo, useState } from 'react';
import { API_BASE, getJson } from '../api.js';

const KIND_MAP = {
  '.html': 'html', '.htm': 'html',
  '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
  '.csv': 'csv',
  '.zip': 'zip',
  '.pdf': 'pdf',
};

function kindFor(path = '') {
  const lower = path.toLowerCase();
  for (const [ext, kind] of Object.entries(KIND_MAP)) {
    if (lower.endsWith(ext)) return kind;
  }
  return 'file';
}

const ICON_LABELS = { html: 'HTML', image: 'IMG', csv: 'CSV', pdf: 'PDF', zip: 'ZIP', file: 'FILE' };

export default function RunsPanel({ refreshKey, selectedRun, onSelectRun, onPreviewFile, setError }) {
  const [runs, setRuns]   = useState([]);
  const [files, setFiles] = useState([]);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    getJson('/runs')
      .then((r) => setRuns(r.runs || []))
      .catch((e) => setError(e.message));
  }, [refreshKey]);

  useEffect(() => {
    if (!selectedRun) { setFiles([]); return; }
    getJson(`/runs/${encodeURIComponent(selectedRun)}/files`)
      .then((r) => setFiles(r.files || []))
      .catch((e) => setError(e.message));
  }, [selectedRun, refreshKey]);

  const visibleFiles = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? files.filter((f) => f.relative_path.toLowerCase().includes(q)) : files;
  }, [files, filter]);

  return (
    <section className="grid runs-layout">

      {/* ── run list ── */}
      <div className="card stack">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Runs</p>
            <h2>Previous analyses</h2>
          </div>
          <span className="badge">{runs.length}</span>
        </div>

        {runs.length === 0 ? (
          <p className="muted">No runs yet. Start a workflow above.</p>
        ) : (
          <div className="run-list">
            {runs.map((run) => (
              <button
                key={run.run_name}
                className={`run-item${selectedRun === run.run_name ? ' selected' : ''}`}
                onClick={() => onSelectRun(run.run_name)}
              >
                <span className="run-name">{run.run_name}</span>
                <span className="run-meta">{run.file_count} files</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── file list ── */}
      <div className="card stack">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Artifacts</p>
            <h2>{selectedRun ? `${selectedRun}` : 'Select a run'}</h2>
          </div>
          {selectedRun && (
            <a
              className="badge"
              href={`${API_BASE}/runs/${selectedRun}/files`}
              target="_blank"
              rel="noreferrer"
            >
              JSON ↗
            </a>
          )}
        </div>

        {selectedRun && (
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter files…"
          />
        )}

        <div className="file-table">
          {!selectedRun && <p className="muted">Choose a run to browse its files.</p>}

          {visibleFiles.map((file) => {
            const kind = kindFor(file.relative_path);
            return (
              <div key={file.relative_path} className="file-row">
                <div className={`file-icon ${kind}`}>{ICON_LABELS[kind]}</div>
                <div className="file-info">
                  <div className="file-name">{file.name}</div>
                  <div className="file-path">{file.relative_path}</div>
                </div>
                <div className="file-actions">
                  <span className={`kind-pill kind-${kind}`}>{kind}</span>
                  {(kind === 'html' || kind === 'image' || kind === 'pdf') && (
                    <button
                      type="button"
                      className="secondary-btn"
                      onClick={() => onPreviewFile(selectedRun, file)}
                    >
                      Preview
                    </button>
                  )}
                  <a
                    className="secondary-btn"
                    href={`${API_BASE}${file.download_url}`}
                    target="_blank"
                    rel="noreferrer"
                    style={{ textDecoration: 'none' }}
                  >
                    Open ↗
                  </a>
                </div>
              </div>
            );
          })}

          {selectedRun && visibleFiles.length === 0 && (
            <p className="muted">{filter ? 'No files match your filter.' : 'No files in this run.'}</p>
          )}
        </div>
      </div>
    </section>
  );
}
