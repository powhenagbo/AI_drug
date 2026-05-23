export default function StatCard({ label, value, hint, status }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">
        {status === 'online'  && <span className="stat-dot online"  />}
        {status === 'offline' && <span className="stat-dot offline" />}
        {value ?? '—'}
      </div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}
