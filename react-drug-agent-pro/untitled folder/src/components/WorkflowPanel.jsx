import { useState } from 'react';
import { postJson } from '../api.js';

const presets = [
  'diphtheria',
  'malaria',
  'tuberculosis',
  'influenza',
  
];

export default function WorkflowPanel({ onComplete, setError }) {
  const [diseaseName, setDiseaseName] = useState('diphtheria');
  const [doVisuals, setDoVisuals] = useState(true);
  const [doMl, setDoMl] = useState(false);
  const [usePadel, setUsePadel] = useState(false);
  const [running, setRunning] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setRunning(true);
    setError('');
    try {
      const result = await postJson('/workflow/run', {
        disease_name: diseaseName,
        do_visuals: doVisuals,
        do_ml: doMl,
        use_padel: usePadel,
        do_archive: true,
      });
      onComplete(result, diseaseName);
    } catch (error) {
      setError(error.message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="card stack">
      <div className="panel-head">
        <div>
          <p className="eyebrow">Workflow</p>
          <h2>Run a disease analysis</h2>
        </div>
        <span className="badge">FastAPI</span>
      </div>

      <div className="chip-row">
        {presets.map((preset) => (
          <button key={preset} type="button" className="chip" onClick={() => setDiseaseName(preset)}>
            {preset}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="stack">
        <label>
          Disease name
          <input value={diseaseName} onChange={(e) => setDiseaseName(e.target.value)} placeholder="diphtheria" />
        </label>

        <div className="option-grid">
          <label className="toggle">
            <input type="checkbox" checked={doVisuals} onChange={(e) => setDoVisuals(e.target.checked)} />
            <span>
              <strong>Visuals</strong>
              <small>Plots and 3D viewers</small>
            </span>
          </label>
          <label className="toggle">
            <input type="checkbox" checked={doMl} onChange={(e) => setDoMl(e.target.checked)} />
            <span>
              <strong>Machine learning</strong>
              <small>Train models on processed data</small>
            </span>
          </label>
          <label className="toggle">
            <input type="checkbox" checked={usePadel} onChange={(e) => setUsePadel(e.target.checked)} />
            <span>
              <strong>PaDEL</strong>
              <small>Use PaDEL descriptors when available</small>
            </span>
          </label>
        </div>

        <button disabled={running} className="primary-btn">
          {running ? 'Running analysis…' : 'Run workflow'}
        </button>
      </form>
    </section>
  );
}
