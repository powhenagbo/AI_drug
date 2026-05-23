import { useState } from 'react';
import { postJson } from '../api.js';

const SUGGESTIONS = [
  'Run diphtheria workflow with visuals and summarize the generated files.',
  'Run malaria workflow and include machine learning.',
  'Generate a 3D viewer for the SMILES CCO.',
  'Search targets for tuberculosis and summarize the likely next step.',
];

export default function AgentPanel({ onAgentResult, setError }) {
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState([]);

  async function handleAsk(e) {
    e.preventDefault();
    if (!message.trim()) return;
    const userMsg = message.trim();
    setHistory((h) => [...h, { role: 'user', text: userMsg }]);
    setMessage('');
    setLoading(true);
    setError('');
    try {
      const result = await postJson('/chat', {
        session_id: 'dashboard-session',
        message: userMsg,
      });
      const reply = result.message || '(no response)';
      setHistory((h) => [...h, { role: 'assistant', text: reply }]);
      onAgentResult({ ...result, message: reply });
    } catch (err) {
      setHistory((h) => [...h, { role: 'error', text: err.message }]);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function useSuggestion(s) {
    setMessage(s);
  }

  return (
    <section className="card stack">
      <div className="panel-head">
        <div>
          <p className="eyebrow">AI agent</p>
          <h2>Natural-language control</h2>
        </div>
        <span className="badge teal">LLM</span>
      </div>

      {history.length === 0 ? (
        <div className="suggestion-list">
          {SUGGESTIONS.map((s, i) => (
            <button key={s} type="button" className="suggestion-item" onClick={() => useSuggestion(s)}>
              <span className="suggestion-num">0{i + 1}</span>
              {s}
            </button>
          ))}
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gap: 10,
            maxHeight: 280,
            overflowY: 'auto',
            paddingRight: 4,
          }}
        >
          {history.map((msg, i) => (
            <div
              key={i}
              style={{
                padding: '10px 13px',
                borderRadius: 12,
                fontSize: '.85rem',
                lineHeight: 1.65,
                background:
                  msg.role === 'user'
                    ? 'rgba(77,132,232,.12)'
                    : msg.role === 'error'
                    ? 'rgba(242,107,107,.1)'
                    : 'rgba(0,212,160,.06)',
                border: `1px solid ${
                  msg.role === 'user'
                    ? 'rgba(77,132,232,.25)'
                    : msg.role === 'error'
                    ? 'rgba(242,107,107,.25)'
                    : 'rgba(0,212,160,.2)'
                }`,
                color:
                  msg.role === 'error' ? 'var(--red)' : 'var(--text-primary)',
              }}
            >
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '.68rem',
                  textTransform: 'uppercase',
                  letterSpacing: '.08em',
                  color: 'var(--text-muted)',
                  display: 'block',
                  marginBottom: 5,
                }}
              >
                {msg.role === 'user' ? 'You' : msg.role === 'error' ? 'Error' : 'Agent'}
              </span>
              {msg.text}
            </div>
          ))}
          {loading && (
            <div style={{ padding: '10px 13px', color: 'var(--text-muted)', fontSize: '.84rem', fontStyle: 'italic' }}>
              <span className="stat-dot run" style={{ marginRight: 8 }} />
              Thinking…
            </div>
          )}
        </div>
      )}

      <form onSubmit={handleAsk} style={{ display: 'grid', gap: 10 }}>
        <div className="field">
          <label htmlFor="agent-input">Your prompt</label>
          <textarea
            id="agent-input"
            rows={3}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Ask anything about your runs or trigger a workflow…"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleAsk(e);
            }}
          />
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button disabled={loading || !message.trim()} className="primary-btn" style={{ flex: 1 }}>
            {loading ? 'Thinking…' : 'Ask agent →'}
          </button>
          {history.length > 0 && (
            <button
              type="button"
              className="secondary-btn"
              onClick={() => setHistory([])}
            >
              Clear
            </button>
          )}
        </div>
        <p className="muted" style={{ fontSize: '.75rem' }}>Tip: ⌘↵ or Ctrl↵ to send</p>
      </form>
    </section>
  );
}
