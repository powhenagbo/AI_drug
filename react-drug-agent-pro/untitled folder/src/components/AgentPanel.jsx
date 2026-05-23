import { useState } from 'react';
import { postJson } from '../api.js';

const suggestions = [
  'Run diphtheria workflow with visuals and summarize the generated files.',
  'Run malaria workflow and include machine learning.',
  'Generate a 3D viewer for the SMILES CCO.',
  'Search targets for diphtheria and summarize the likely next step.',
];

export default function AgentPanel({ onAgentResult, setError }) {
  const [message, setMessage] = useState(suggestions[0]);
  const [loading, setLoading] = useState(false);

  async function handleAsk(event) {
  event.preventDefault();
  setLoading(true);
  setError('');

  try {
    const result = await postJson('/chat', {
     session_id: 'paul-test',
     message
  });

    onAgentResult({
    ...result,
  message: result.message
   });

  } catch (error) {
    setError(error.message);
  } finally {
    setLoading(false);
  }
}

  return (
    <section className="card stack">
      <div className="panel-head">
        <div>
          <p className="eyebrow">AI agent</p>
          <h2>Control the backend with prompts</h2>
        </div>
        <span className="badge">LLM</span>
      </div>

      <div className="chip-row">
        {suggestions.map((s) => (
          <button key={s} type="button" className="chip chip-soft" onClick={() => setMessage(s)}>
            {s}
          </button>
        ))}
      </div>

      <form onSubmit={handleAsk} className="stack">
        <label>
          Agent prompt
          <textarea rows="6" value={message} onChange={(e) => setMessage(e.target.value)} />
        </label>
        <button disabled={loading} className="primary-btn">
          {loading ? 'Thinking…' : 'Ask agent'}
        </button>
      </form>
    </section>
  );
}
