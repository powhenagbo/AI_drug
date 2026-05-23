import { useState } from 'react';
import { postJson } from '../api.js';

const PRESETS = ['diphtheria', 'malaria', 'tuberculosis', 'influenza', 'alzheimer'];

export default function WorkflowPanel({ onComplete, setError }) {
  const [diseaseName, setDiseaseName] = useState('diphtheria');
  const [doVisuals,   setDoVisuals]   = useState(true);
  const [doMl,        setDoMl]        = useState(false);
  const [usePadel,    setUsePadel]    = useState(false);
  const [doAlphaFold, setDoAlphaFold] = useState(false);
  const [afUniprotId, setAfUniprotId] = useState('');
  const [running, setRunning]         = useState(false);
  const [step,    setStep]            = useState('');

  async function handleSubmit(e) {
    e.preventDefault();
    setRunning(true);
    setStep('Connecting to backend…');
    setError('');
    try {
      setStep('Searching ChEMBL targets…');
      const body = {
        disease_name: diseaseName,
        do_visuals:   doVisuals,
        do_ml:        doMl,
        use_padel:    usePadel,
        do_archive:   true,
        do_alphafold: doAlphaFold,
      };
      if (doAlphaFold && afUniprotId.trim()) body.af_uniprot_id = afUniprotId.trim();
      const result = await postJson('/workflow/run', body);
      setStep('');
      onComplete(result, diseaseName);
    } catch (err) {
      setStep('');
      setError(err.message);
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

      {/* preset segmented control */}
      <div className="seg-control">
        {PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            className={`seg-btn${diseaseName === p ? ' active' : ''}`}
            onClick={() => setDiseaseName(p)}
          >
            {p}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="stack">
        <div className="field">
          <label htmlFor="disease-input">Disease / condition name</label>
          <input
            id="disease-input"
            type="text"
            value={diseaseName}
            onChange={(e) => setDiseaseName(e.target.value)}
            placeholder="e.g. diphtheria"
          />
        </div>

        <div className="toggle-row">
          <label className="toggle-item">
            <input type="checkbox" checked={doVisuals} onChange={(e) => setDoVisuals(e.target.checked)} />
            <span className="tgl-text">
              <strong>Visuals</strong>
              <small>2D plots &amp; 3D viewers</small>
            </span>
          </label>
          <label className="toggle-item">
            <input type="checkbox" checked={doMl} onChange={(e) => setDoMl(e.target.checked)} />
            <span className="tgl-text">
              <strong>Machine learning</strong>
              <small>Train classifiers on data</small>
            </span>
          </label>
          <label className="toggle-item">
            <input type="checkbox" checked={usePadel} onChange={(e) => setUsePadel(e.target.checked)} />
            <span className="tgl-text">
              <strong>PaDEL descriptors</strong>
              <small>Extended fingerprinting</small>
            </span>
          </label>
          <label className="toggle-item">
            <input type="checkbox" checked={doAlphaFold} onChange={(e) => setDoAlphaFold(e.target.checked)} />
            <span className="tgl-text">
              <strong>AlphaFold structure</strong>
              <small>Fetch &amp; store PDB from EBI</small>
            </span>
          </label>
        </div>

        {/* AlphaFold UniProt override — only shown when toggled on */}
        {doAlphaFold && (
          <div className="field" style={{ marginTop: -4 }}>
            <label htmlFor="af-uniprot">UniProt ID override (optional)</label>
            <input
              id="af-uniprot"
              type="text"
              value={afUniprotId}
              onChange={(e) => setAfUniprotId(e.target.value)}
              placeholder="e.g. P00533 — leave blank to auto-resolve"
            />
          </div>
        )}

        {running && step ? (
          <div className="muted mono" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="stat-dot run" />
            {step}
          </div>
        ) : null}

        <button disabled={running} className="primary-btn">
          {running ? 'Running analysis…' : 'Run workflow →'}
        </button>
      </form>
    </section>
  );
}
