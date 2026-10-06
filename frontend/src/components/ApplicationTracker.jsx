import { Check, ExternalLink, RotateCcw } from 'lucide-react';

const ORDER = ['applied', 'opened', 'prepared', 'matched', 'skipped', 'closed'];

const LABEL = {
  matched: 'Found',
  prepared: 'Letter ready',
  opened: 'Opened',
  applied: 'Applied',
  skipped: 'Skipped',
  closed: 'Closed',
};

export default function ApplicationTracker({ applications, counts, onState }) {
  // Applied first: this panel exists to answer "where have I actually
  // applied?", which is the question the rest of the app cannot answer.
  const sorted = [...(applications || [])].sort(
    (a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state)
  );

  const interesting = sorted.filter(
    (a) => a.state !== 'matched' && a.state !== 'closed'
  );

  return (
    <details className="panel">
      <summary>
        <h2>Application tracker</h2>
        <span className="muted small">
          {counts?.applied || 0} applied · {counts?.opened || 0} opened ·{' '}
          {counts?.prepared || 0} drafted · {counts?.skipped || 0} skipped
        </span>
      </summary>

      {interesting.length === 0 ? (
        <p className="empty">
          Nothing yet. Opening a posting or marking one applied records it here.
        </p>
      ) : (
        <table className="tracker">
          <thead>
            <tr>
              <th>Role</th>
              <th>Company</th>
              <th>Score</th>
              <th>State</th>
              <th>Updated</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {interesting.map((row) => (
              <tr key={row.job_id}>
                <td>
                  {row.url ? (
                    <a href={row.url} target="_blank" rel="noreferrer">
                      {row.title} <ExternalLink size={11} />
                    </a>
                  ) : row.title}
                </td>
                <td>{row.company}</td>
                <td>{row.score != null ? `${row.score}%` : '—'}</td>
                <td><span className={`state-pill state-${row.state}`}>
                  {LABEL[row.state] || row.state}
                </span></td>
                <td className="muted small">
                  {row.updated_at ? new Date(row.updated_at).toLocaleDateString() : ''}
                </td>
                <td className="tracker-actions">
                  {row.state !== 'applied' && (
                    <button
                      className="btn btn-ghost btn-small"
                      onClick={() => onState(row.job_id, 'applied')}
                      title="Mark as applied"
                    >
                      <Check size={12} />
                    </button>
                  )}
                  {row.state !== 'matched' && (
                    <button
                      className="btn btn-ghost btn-small"
                      onClick={() => onState(row.job_id, 'matched')}
                      title="Put it back"
                    >
                      <RotateCcw size={12} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}
