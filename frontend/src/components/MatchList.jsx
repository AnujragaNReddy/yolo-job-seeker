import { useState } from 'react';
import {
  AlertTriangle, Building2, Check, ChevronDown, Clipboard, ExternalLink,
  FileSignature, Loader2, MapPin, SkipForward, Sparkles,
} from 'lucide-react';

const STATE_LABEL = {
  matched: 'New',
  prepared: 'Letter ready',
  opened: 'Opened',
  applied: 'Applied',
  skipped: 'Skipped',
  closed: 'Closed',
};

function scoreClass(score) {
  if (score >= 80) return 'strong';
  if (score >= 60) return 'good';
  if (score >= 45) return 'weak';
  return 'poor';
}

function MatchCard({ item, onPrepare, onState, preparing, letter }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const match = item.match || {};
  const state = item.state || 'matched';

  async function copyLetter() {
    try {
      await navigator.clipboard.writeText(letter.cover_letter);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access is blocked in some contexts; the textarea below is
      // selectable either way, so this is not worth an error message.
    }
  }

  // Opening the posting is also the moment we know it was opened, so the two
  // happen together rather than needing a second click.
  function openPosting() {
    window.open(item.url, '_blank', 'noopener,noreferrer');
    if (state === 'matched' || state === 'prepared') onState(item.id, 'opened');
  }

  return (
    <article className={`match-card match-${state}`}>
      <div className="match-top">
        <div className={`score score-${scoreClass(match.score)}`}>
          <strong>{match.score}</strong>
          <span>match</span>
        </div>

        <div className="match-head">
          <h3>{item.title}</h3>
          <div className="match-meta">
            <span><Building2 size={12} /> {item.company || 'Unknown'}</span>
            {item.location && <span><MapPin size={12} /> {item.location}</span>}
            {item.remote && <span className="tag-remote">Remote</span>}
            <span className="tag-source">{item.source}</span>
          </div>
        </div>

        <span className={`state-pill state-${state}`}>
          {STATE_LABEL[state] || state}
        </span>
      </div>

      {match.matched_skills?.length > 0 && (
        <div className="chip-row tight">
          {match.matched_skills.slice(0, 10).map((s) => (
            <span key={s} className="chip chip-have">{s}</span>
          ))}
          {match.missing_skills?.slice(0, 5).map((s) => (
            <span key={s} className="chip chip-missing" title="Asked for, not on your resume">
              {s}
            </span>
          ))}
        </div>
      )}

      {match.penalties?.length > 0 && (
        <p className="match-penalty">
          <AlertTriangle size={12} /> {match.penalties.join(' · ')}
        </p>
      )}

      <button className="match-why" onClick={() => setOpen(!open)}>
        <ChevronDown size={13} className={open ? 'flip' : ''} />
        Why {match.score}%
      </button>

      {open && (
        <div className="match-detail">
          <ul>
            {(match.reasons || []).map((reason, i) => <li key={i}>{reason}</li>)}
          </ul>
          <div className="components">
            {Object.entries(match.components || {}).map(([key, value]) => (
              <div key={key}>
                <span>{key}</span>
                <div className="bar"><div style={{ width: `${value}%` }} /></div>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          {item.salary && <p className="muted small">Salary: {item.salary}</p>}
        </div>
      )}

      {letter?.cover_letter && (
        <div className="letter">
          <div className="letter-head">
            <strong><FileSignature size={13} /> Draft cover letter</strong>
            <span className="muted small">
              {letter.letter_generated_by === 'template'
                ? 'Template'
                : letter.letter_generated_by}
            </span>
            <button className="btn btn-ghost btn-small" onClick={copyLetter}>
              {copied ? <Check size={12} /> : <Clipboard size={12} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          {/* Labelled a draft on purpose: it is written from a parsed resume
              and a scraped description, and either can be wrong. */}
          <p className="letter-warn">
            Read this before you send it. It was drafted from your parsed
            resume, so check every claim is one you can stand behind.
          </p>
          <textarea readOnly rows={9} value={letter.cover_letter} />
          {letter.letter_note && <p className="muted small">{letter.letter_note}</p>}
        </div>
      )}

      <footer className="match-actions">
        <button className="btn btn-primary" onClick={openPosting}>
          <ExternalLink size={14} /> Open & apply
        </button>

        {!letter?.cover_letter && (
          <button
            className="btn btn-ghost"
            onClick={() => onPrepare(item.id)}
            disabled={preparing}
          >
            {preparing ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}
            {preparing ? 'Drafting…' : 'Draft letter'}
          </button>
        )}

        {state !== 'applied' && (
          <button className="btn btn-ghost" onClick={() => onState(item.id, 'applied')}>
            <Check size={14} /> I applied
          </button>
        )}

        {state !== 'skipped' && (
          <button className="btn btn-ghost" onClick={() => onState(item.id, 'skipped')}>
            <SkipForward size={14} /> Skip
          </button>
        )}
      </footer>
    </article>
  );
}

export default function MatchList({
  items, total, scannedAt, query, sources, scanCounts, hasMore, loadingMore,
  onLoadMore, onPrepare, onState, preparingId, letters, filter, onFilter,
}) {
  const failed = (sources || []).filter((s) => s.ok === false);

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Matches</h2>
        <div className="filter-row">
          {['all', 'matched', 'prepared', 'opened'].map((value) => (
            <button
              key={value}
              className={`pill ${filter === value ? 'pill-on' : ''}`}
              onClick={() => onFilter(value)}
            >
              {value === 'all' ? 'All' : STATE_LABEL[value]}
            </button>
          ))}
        </div>
      </div>

      {scannedAt ? (
        <p className="muted small">
          {total} shown · searched <strong>{query}</strong> ·{' '}
          {scanCounts?.unique ?? 0} postings found,{' '}
          {scanCounts?.matched ?? 0} cleared the bar · last scan{' '}
          {new Date(scannedAt).toLocaleString()}
        </p>
      ) : (
        <p className="muted small">No scan yet.</p>
      )}

      {/* Fewer results than usual with no explanation is the kind of silence
          that wastes an afternoon, so a dead board says so. */}
      {failed.length > 0 && (
        <div className="banner banner-warn">
          <AlertTriangle size={14} />
          <div>
            <strong>
              {failed.length} {failed.length === 1 ? 'board' : 'boards'} did not
              answer this scan.
            </strong>{' '}
            {failed.map((s) => `${s.source}: ${s.reason}`).join(' · ')}
          </div>
        </div>
      )}

      {items.length === 0 ? (
        <p className="empty">
          Nothing here yet. Hit <strong>Scan now</strong> above.
        </p>
      ) : (
        <>
          <div className="match-grid">
            {items.map((item) => (
              <MatchCard
                key={item.id}
                item={item}
                letter={letters[item.id]}
                preparing={preparingId === item.id}
                onPrepare={onPrepare}
                onState={onState}
              />
            ))}
          </div>

          {hasMore && (
            <button className="btn btn-wide" onClick={onLoadMore} disabled={loadingMore}>
              {loadingMore ? <Loader2 size={14} className="spin" /> : <ChevronDown size={14} />}
              {loadingMore ? 'Loading…' : 'Load more'}
            </button>
          )}
        </>
      )}
    </section>
  );
}
