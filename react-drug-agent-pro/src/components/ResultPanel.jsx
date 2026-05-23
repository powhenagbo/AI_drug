import { useState } from 'react';

function extractAssistantText(agentResult) {
  if (!agentResult) return '';
  return agentResult.assistant || agentResult.message || '';
}

function summarizeWorkflow(workflowResult) {
  if (!workflowResult) return [];
  return (workflowResult.steps || []).map((step, i) => ({
    id: `${step.step || 'step'}-${i}`,
    title: step.step || `Step ${i + 1}`,
    ok: step.ok !== false,
    detail: step.result?.message || step.message || step.result?.status || '',
  }));
}

function CopyButton({ data }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }
  return (
    <button type="button" className="secondary-btn" onClick={copy} style={{ fontSize: '.78rem' }}>
      {copied ? 'Copied ✓' : 'Copy JSON'}
    </button>
  );
}

export default function ResultPanel({ workflowResult, agentResult }) {
  const steps     = summarizeWorkflow(workflowResult);
  const assistant = extractAssistantText(agentResult);

  return (
    <section className="grid result-layout">

      {/* workflow timeline */}
      <div className="card stack">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Workflow output</p>
            <h2>Execution summary</h2>
          </div>
          {workflowResult && <CopyButton data={workflowResult} />}
        </div>

        {steps.length === 0 ? (
          <p className="muted">No workflow result yet. Run a disease analysis to see steps here.</p>
        ) : (
          <div className="timeline">
            {steps.map((item) => (
              <div key={item.id} className="timeline-item">
                <span className={`tl-dot ${item.ok ? 'ok' : 'bad'}`} />
                <div>
                  <div className="tl-step">{item.title}</div>
                  {item.detail && <div className="tl-detail">{item.detail}</div>}
                </div>
              </div>
            ))}
          </div>
        )}

        {workflowResult && (
          <details>
            <summary>Raw JSON</summary>
            <pre>{JSON.stringify(workflowResult, null, 2)}</pre>
          </details>
        )}
      </div>

      
    </section>
  );
}
