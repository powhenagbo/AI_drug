import { fileUrl } from '../api.js';

function kindFor(relativePath = '') {
  const lower = relativePath.toLowerCase();
  if (lower.endsWith('.html')) return 'html';
  if (lower.endsWith('.png') || lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image';
  if (lower.endsWith('.pdf')) return 'pdf';
  return 'other';
}

export default function PreviewPanel({ selectedRun, selectedFile }) {
  if (!selectedRun || !selectedFile) {
    return (
      <section className="card preview-empty">
        <p className="eyebrow">Preview</p>
        <h2>No file selected</h2>
        <p className="muted">Choose an HTML viewer, plot, or PDF to preview it here.</p>
      </section>
    );
  }

  const url = fileUrl(selectedRun, selectedFile.relative_path);
  const kind = kindFor(selectedFile.relative_path);

  return (
    <section className="card stack preview-card">
      <div className="panel-head">
        <div>
          <p className="eyebrow">Preview</p>
          <h2>{selectedFile.name}</h2>
        </div>
        <a href={url} target="_blank" rel="noreferrer" className="badge link-badge">Open directly</a>
      </div>

      <div className="file-path">{selectedFile.relative_path}</div>

      {kind === 'image' ? <img className="preview-image" src={url} alt={selectedFile.name} /> : null}
      {kind === 'html' || kind === 'pdf' ? <iframe className="preview-frame" src={url} title={selectedFile.name} /> : null}
      {kind === 'other' ? (
        <div className="muted">
          This file type is best opened in a new tab.
        </div>
      ) : null}
    </section>
  );
}
