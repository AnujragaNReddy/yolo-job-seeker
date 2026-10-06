import { useEffect, useState } from 'react';
import { Check, Loader2, Settings2 } from 'lucide-react';

export default function SettingsPanel({ settings, sources, onSave, saving }) {
  const [draft, setDraft] = useState(null);

  useEffect(() => {
    if (settings && !draft) {
      setDraft({ ...settings, locations: (settings.locations || []).join(', ') });
    }
  }, [settings, draft]);

  if (!draft) return null;

  function submit(event) {
    event.preventDefault();
    onSave({
      ...draft,
      locations: draft.locations.split(',').map((s) => s.trim()).filter(Boolean),
    });
  }

  const unconfigured = (sources || []).filter((s) => !s.enabled && s.requires_key);

  return (
    <details className="panel">
      <summary>
        <h2><Settings2 size={16} /> Search settings</h2>
        <span className="muted small">
          {(sources || []).filter((s) => s.enabled).length} of {(sources || []).length} boards active
        </span>
      </summary>

      <form className="profile-form" onSubmit={submit}>
        <div className="form-row">
          <label>
            Search term <span className="hint">blank uses your top job title</span>
            <input
              value={draft.query || ''}
              placeholder="e.g. backend developer"
              onChange={(e) => setDraft({ ...draft, query: e.target.value })}
            />
          </label>

          <label>
            Locations <span className="hint">comma separated</span>
            <input
              value={draft.locations}
              placeholder="Hyderabad, Bengaluru"
              onChange={(e) => setDraft({ ...draft, locations: e.target.value })}
            />
          </label>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={Boolean(draft.remote_only)}
            onChange={(e) => setDraft({ ...draft, remote_only: e.target.checked })}
          />
          Remote only
        </label>

        <div className="form-row">
          <label>
            Minimum match score
            <input
              type="number" min="0" max="100"
              value={draft.min_match_score}
              onChange={(e) => setDraft({ ...draft, min_match_score: +e.target.value })}
            />
            <span className="hint">below this, a job is not stored at all</span>
          </label>

          <label>
            Draft letters above
            <input
              type="number" min="0" max="100"
              value={draft.auto_prepare_above}
              onChange={(e) => setDraft({ ...draft, auto_prepare_above: +e.target.value })}
            />
            <span className="hint">scans pre-write letters for these</span>
          </label>
        </div>

        <div className="form-row">
          <label>
            Letters per scan
            <input
              type="number" min="0" max="25"
              value={draft.max_prepare_per_scan}
              onChange={(e) => setDraft({ ...draft, max_prepare_per_scan: +e.target.value })}
            />
            <span className="hint">each one is a model call</span>
          </label>

          <label>
            Max posting age (days)
            <input
              type="number" min="1" max="365"
              value={draft.max_job_age_days}
              onChange={(e) => setDraft({ ...draft, max_job_age_days: +e.target.value })}
            />
            <span className="hint">older postings are dropped</span>
          </label>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={Boolean(draft.use_llm_letters)}
            onChange={(e) => setDraft({ ...draft, use_llm_letters: e.target.checked })}
          />
          Write letters with the language model
          <span className="hint">off uses a plain template, which always works</span>
        </label>

        <div className="form-actions">
          <button className="btn btn-primary" type="submit" disabled={saving}>
            {saving ? <Loader2 size={15} className="spin" /> : <Check size={15} />}
            Save settings
          </button>
        </div>
      </form>

      <div className="source-list">
        <h3>Job boards</h3>
        {(sources || []).map((source) => (
          <div key={source.name} className={`source ${source.enabled ? 'on' : 'off'}`}>
            <span className="dot" />
            <strong>{source.label}</strong>
            {source.enabled ? (
              <span className="muted small">
                active{source.companies?.length ? ` · ${source.companies.length} companies` : ''}
              </span>
            ) : (
              <span className="muted small">
                {source.missing?.length
                  ? `needs ${source.missing.join(' + ')}`
                  : 'needs a company list'}
              </span>
            )}
          </div>
        ))}
        {unconfigured.length > 0 && (
          <p className="muted small">
            The active boards work without any credentials. The rest are
            optional and only widen the search — see the README for where each
            free key comes from.
          </p>
        )}
      </div>
    </details>
  );
}
