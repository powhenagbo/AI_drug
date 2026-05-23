import { useEffect, useMemo, useState } from 'react';
import AgentPanel    from './components/AgentPanel.jsx';
import AlphaFoldPanel from './components/AlphaFoldPanel.jsx';
import MolViewer     from './components/MolViewer.jsx';
import PreviewPanel  from './components/PreviewPanel.jsx';
import ResultPanel   from './components/ResultPanel.jsx';
import RunsPanel     from './components/RunsPanel.jsx';
import StatCard      from './components/StatCard.jsx';
import WorkflowPanel from './components/WorkflowPanel.jsx';
import { API_BASE, getJson } from './api.js';

export default function App() {
  const [error, setError]               = useState('');
  const [refreshKey, setRefreshKey]     = useState(0);
  const [selectedRun, setSelectedRun]   = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [workflowResult, setWorkflowResult] = useState(null);
  const [agentResult, setAgentResult]   = useState(null);
  const [health, setHealth]             = useState(null);
  const [runCount, setRunCount]         = useState(0);
  const [fileCount, setFileCount]       = useState(0);

  const healthUrl = useMemo(() => `${API_BASE}/health`, []);

  useEffect(() => {
    async function loadOverview() {
      try {
        const h = await getJson('/health');
        setHealth(h);
        try {
          const r = await getJson('/runs');
          const runs = r.runs || [];
          setRunCount(runs.length);
          setFileCount(runs.reduce((s, run) => s + (run.file_count || 0), 0));
        } catch { setRunCount(0); setFileCount(0); }
      } catch (err) {
        setError(err.message);
      }
    }
    loadOverview();
  }, [refreshKey]);

  function normalizeRunName(name = '') {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  }

  function handleWorkflowComplete(result, diseaseName) {
    setWorkflowResult(result);
    setSelectedRun(normalizeRunName(diseaseName));
    setSelectedFile(null);
    setRefreshKey((n) => n + 1);
  }

  function handleAgentResult(result) {
    setAgentResult(result);
    const candidate = result?.tool_results?.find?.((e) => e?.result?.disease_name)?.result?.disease_name;
    if (candidate) { setSelectedRun(normalizeRunName(candidate)); setSelectedFile(null); }
    setRefreshKey((n) => n + 1);
  }

  return (
    <div className="app-shell">

      {/* ── hero ── */}
      <header className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Drug Discovery Agent · UALR Bioinformatics</p>
          <h1>AI-powered <em>drug discovery</em> dashboard</h1>
          <p>Run ChEMBL workflows, browse artifacts, preview 3D molecular viewers, and control the backend with natural-language prompts.</p>
        </div>
        <div className="hero-actions">
          <a className="pill" href={healthUrl} target="_blank" rel="noreferrer">API health ↗</a>
          <span className="badge">{API_BASE}</span>
        </div>
      </header>

      {/* ── stats ── */}
      <section className="stats-grid">
        <StatCard
          label="Backend status"
          value={health?.ok ? 'Online' : 'Offline'}
          hint={health?.service || 'FastAPI service'}
          status={health?.ok ? 'online' : 'offline'}
        />
        <StatCard label="Saved runs"  value={runCount}  hint="Stored in /runs" />
        <StatCard label="Artifacts"   value={fileCount}  hint="Files across all runs" />
        <StatCard label="Active run"  value={selectedRun || 'None'} hint="Preview auto-updates" />
      </section>

      {error && <div className="error-banner">{error}</div>}

      {/* ── workflow + agent ── */}
      <section className="grid control-layout">
        <WorkflowPanel onComplete={handleWorkflowComplete} setError={setError} />
        <AgentPanel    onAgentResult={handleAgentResult}   setError={setError} />
      </section>

      {/* ── structure tools: AlphaFold + SMILES viewer side by side, both collapsible ── */}
      <section className="grid structure-layout">
        <AlphaFoldPanel setError={setError} />
        <MolViewer />
      </section>

      {/* ── runs browser ── */}
      <RunsPanel
        refreshKey={refreshKey}
        selectedRun={selectedRun}
        onSelectRun={setSelectedRun}
        onPreviewFile={(runName, file) => { setSelectedRun(runName); setSelectedFile(file); }}
        setError={setError}
      />

      {/* ── preview ── */}
      <PreviewPanel selectedRun={selectedRun} selectedFile={selectedFile} />

      {/* ── results ── */}
      <ResultPanel workflowResult={workflowResult} agentResult={agentResult} />

    </div>
  );
}
