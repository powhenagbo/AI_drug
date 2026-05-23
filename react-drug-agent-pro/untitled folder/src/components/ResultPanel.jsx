function extractAssistantText(agentResult) {
  if (!agentResult) return '';
  return agentResult.assistant || agentResult.message || '';
}

function summarizeWorkflow(workflowResult) {
  if (!workflowResult) return [];
  const steps = workflowResult.steps || [];
  return steps.map((step, index) => ({
    id: `${step.step || 'step'}-${index}`,
    title: step.step || `Step ${index + 1}`,
    ok: step.ok !== false,
    detail: step.result?.message || step.message || step.result?.status || '',
  }));
}

export default function ResultPanel({ workflowResult, agentResult }) {
  const workflowSummary = summarizeWorkflow(workflowResult);
  const assistantText = extractAssistantText(agentResult);

  return (
    <section className="grid result-layout">
      <div className="card stack">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Workflow output</p>
            <h2>Execution summary</h2>
          </div>
        </div>

        {workflowSummary.length === 0 ? <p className="muted">No workflow result yet.</p> : null}
        <div className="timeline">
          {workflowSummary.map((item) => (
            <div key={item.id} className="timeline-item">
              <span className={`status-dot ${item.ok ? 'ok' : 'bad'}`} />
              <div>
                <div className="timeline-title">{item.title}</div>
                {item.detail ? <div className="timeline-detail">{item.detail}</div> : null}
              </div>
            </div>
          ))}
        </div>

        {workflowResult ? <details><summary>Raw JSON</summary><pre>{JSON.stringify(workflowResult, null, 2)}</pre></details> : null}
      </div>

      <div className="card stack">
        <div className="panel-head">
          <div>
            <p className="eyebrow">Agent output</p>
            <h2>Assistant response</h2>
          </div>
        </div>

        {assistantText ? <div className="assistant-box">{assistantText}</div> : <p className="muted">No agent response yet.</p>}
        {agentResult ? <details><summary>Raw JSON</summary><pre>{JSON.stringify(agentResult, null, 2)}</pre></details> : null}
      </div>
    </section>
  );
}
