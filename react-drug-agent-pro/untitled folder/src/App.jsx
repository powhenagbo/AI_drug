import { useEffect, useMemo, useState } from 'react';
import AgentPanel from './components/AgentPanel.jsx';
import PreviewPanel from './components/PreviewPanel.jsx';
import ResultPanel from './components/ResultPanel.jsx';
import RunsPanel from './components/RunsPanel.jsx';
import StatCard from './components/StatCard.jsx';
import WorkflowPanel from './components/WorkflowPanel.jsx';
import { API_BASE, getJson } from './api.js';

export default function App() {
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [selectedRun, setSelectedRun] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [workflowResult, setWorkflowResult] = useState(null);
  const [agentResult, setAgentResult] = useState(null);
  const [health, setHealth] = useState(null);
  const [runCount, setRunCount] = useState(0);
  const [fileCount, setFileCount] = useState(0);

  const healthUrl = useMemo(() => `${API_BASE}/health`, []);

  useEffect(() => {
    async function loadOverview() {
      try {
        const healthResult = await getJson('/health');
        setHealth(healthResult);

        try {
          const runsResult = await getJson('/runs');
          const runs = runsResult.runs || [];
          setRunCount(runs.length);
          setFileCount(runs.reduce((sum, run) => sum + (run.file_count || 0), 0));
       } catch {
         setRunCount(0);
         setFileCount(0);
       }

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
    const runName = normalizeRunName(diseaseName);
    setSelectedRun(runName);
    setSelectedFile(null);
    setRefreshKey((n) => n + 1);
  }

  function handleAgentResult(result) {
    setAgentResult(result);
    const candidate = result?.tool_results?.find?.((entry) => entry?.result?.disease_name)?.result?.disease_name;
    if (candidate) {
      setSelectedRun(normalizeRunName(candidate));
      setSelectedFile(null);
    }
    setRefreshKey((n) => n + 1);
  }

  function handlePreviewFile(runName, file) {
    setSelectedRun(runName);
    setSelectedFile(file);
  }

  return (
    <div className="app-shell">
      <header className="hero card">
        <div className="hero-copy">
          <p className="eyebrow">Drug Discovery Agent Dashboard</p>
          <h1>React frontend with embedded artifact preview and AI-agent control</h1>
          <p className="muted">
            Run workflows, browse run artifacts, preview 3D HTML viewers, and trigger the backend with natural-language prompts.
          </p>
        </div>
        <div className="hero-actions">
          <a className="pill" href={healthUrl} target="_blank" rel="noreferrer">API health</a>
          <span className="badge">API: {API_BASE}</span>
        </div>
      </header>

      <section className="stats-grid">
        <StatCard label="Backend status" value={health?.ok ? 'Online' : 'Unknown'} hint={health?.service || 'FastAPI service'} />
        <StatCard label="Saved runs" value={runCount} hint="Detected in /runs" />
        <StatCard label="Artifacts" value={fileCount} hint="Files across all runs" />
        <StatCard label="Selected run" value={selectedRun || 'None'} hint="Preview updates here" />
      </section>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="grid control-layout">
        <WorkflowPanel onComplete={handleWorkflowComplete} setError={setError} />
        <AgentPanel onAgentResult={handleAgentResult} setError={setError} />
      </section>

      <RunsPanel
        refreshKey={refreshKey}
        selectedRun={selectedRun}
        onSelectRun={setSelectedRun}
        onPreviewFile={handlePreviewFile}
        setError={setError}
      />

      <PreviewPanel selectedRun={selectedRun} selectedFile={selectedFile} />

      <ResultPanel workflowResult={workflowResult} agentResult={agentResult} />
    </div>
  );
}
