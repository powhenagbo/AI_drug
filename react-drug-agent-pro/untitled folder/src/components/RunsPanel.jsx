import { useEffect, useMemo, useState } from 'react';
import { API_BASE, getJson } from '../api.js';

function formatFileKind(path) {
  const lower = path.toLowerCase();
  if (lower.endsWith('.html')) return 'html';
  if (lower.endsWith('.png') || lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image';
  if (lower.endsWith('.csv')) return 'csv';
  if (lower.endsWith('.zip')) return 'zip';
  if (lower.endsWith('.pdf')) return 'pdf';
  return 'file';
}

export default function RunsPanel({ refreshKey, selectedRun, onSelectRun, onPreviewFile, setError }) {
  const [runs, setRuns] = useState([]);
  const [files, setFiles] = useState([]);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    async function loadRuns() {
      try {
        const result = await getJson('/runs');
        setRuns(result.runs || []);
      } catch (error) {
        setError(error.message);
      }
    }
    loadRuns();
  }, [refreshKey, setError]);

  useEffect(() => {
    if (!selectedRun) {
      setFiles([]);
      return;
    }
    async function loadFiles() {
      try {
        const result = await getJson(`/runs/${encodeURIComponent(selectedRun)}/files`);
        setFiles(result.files || []);
      } catch (error) {
        setError(error.message);
      }
    }
    loadFiles();
  }, [selectedRun, refreshKey, setError]);

  const visibleFiles = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return files;
    return files.filter((file) => file.relative_path.toLowerCase().includes(q));
  }, [files, filter]);

  return (
    <section className="grid runs-layout">
      <div className="card stack">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Runs</p>
            <h2>Previous analyses</h2>
          </div>
          <span className="badge">{runs.length}</span>
        </div>

        <div className="run-list">
          {runs.length === 0 ? <p className="muted">No runs yet.</p> : null}
          {runs.map((run) => (
            <button
              key={run.run_name}
              className={`run-item ${selectedRun === run.run_name ? 'selected' : ''}`}
              onClick={() => onSelectRun(run.run_name)}
            >
              <span>{run.run_name}</span>
              <small>{run.file_count} files</small>
            </button>
          ))}
        </div>
      </div>

      <div className="card stack">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Artifacts</p>
            <h2>{selectedRun ? `${selectedRun} files` : 'Choose a run'}</h2>
          </div>
          {selectedRun ? <a className="badge link-badge" href={`${API_BASE}/runs/${selectedRun}/files`} target="_blank" rel="noreferrer">JSON</a> : null}
        </div>

        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter files" />

        <div className="file-table">
          {visibleFiles.map((file) => {
            const kind = formatFileKind(file.relative_path);
            return (
              <div key={file.relative_path} className="file-row-pro">
                <div>
                  <div className="file-name">{file.name}</div>
                  <div className="file-path">{file.relative_path}</div>
                </div>
                <div className="file-actions">
                  <span className={`kind-pill kind-${kind}`}>{kind}</span>
                  <button type="button" className="secondary-btn" onClick={() => onPreviewFile(selectedRun, file)}>
                    Preview
                  </button>
                  <a href={`${API_BASE}${file.download_url}`} target="_blank" rel="noreferrer" className="secondary-link">
                    Open
                  </a>
                </div>
              </div>
            );
          })}
          {selectedRun && visibleFiles.length === 0 ? <p className="muted">No matching files.</p> : null}
        </div>
      </div>
    </section>
  );
}
