import { fileUrl } from '../api.js';

function kindFor(path = '') {
  const lower = path.toLowerCase();
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'html';
  if (['.png','.jpg','.jpeg','.webp'].some((e) => lower.endsWith(e))) return 'image';
  if (lower.endsWith('.pdf')) return 'pdf';
  return 'other';
}

export default function PreviewPanel({ selectedRun, selectedFile }) {
  if (!selectedRun || !selectedFile) {
    return (
      <section className="card preview-empty">
        <div className="preview-empty-icon">🔬</div>
        <p className="eyebrow">Preview</p>
        <h2>No file selected</h2>
        <p className="muted">Choose an HTML viewer, plot, or PDF from the artifacts panel to preview it here.</p>
      </section>
    );
  }

  const url  = fileUrl(selectedRun, selectedFile.relative_path);
  const kind = kindFor(selectedFile.relative_path);

  return (
    <section className="card stack preview-card">
      <div className="panel-head">
        <div>
          <p className="eyebrow">Preview · {selectedRun}</p>
          <h2>{selectedFile.name}</h2>
        </div>
        <a href={url} target="_blank" rel="noreferrer" className="pill">
          Open in new tab ↗
        </a>
      </div>

      <p className="muted mono">{selectedFile.relative_path}</p>

      {kind === 'image' && <img className="preview-image" src={url} alt={selectedFile.name} />}
      {(kind === 'html' || kind === 'pdf') && (
        <iframe className="preview-frame" src={url} title={selectedFile.name} />
      )}
      {kind === 'other' && (
        <div className="muted" style={{ padding: '20px 0' }}>
          This file type cannot be previewed inline. Use the "Open in new tab" link above.
        </div>
      )}
    </section>
  );
}
