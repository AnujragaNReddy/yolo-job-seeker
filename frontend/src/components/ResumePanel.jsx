import { useRef, useState } from 'react';
import {
  Check, FileText, Loader2, Pencil, Trash2, Upload, X,
} from 'lucide-react';

const SENIORITY = ['intern', 'junior', 'mid', 'senior', 'lead', 'principal', 'director'];

export default function ResumePanel({
  profile, hasResume, query, uploading, onUpload, onSave, onDelete, saving,
}) {
  const fileInput = useRef(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);

  function startEditing() {
    setDraft({
      name: profile?.name || '',
      skills: (profile?.skills || []).join(', '),
      titles: (profile?.titles || []).join(', '),
      seniority: profile?.seniority || 'mid',
      years_experience: profile?.years_experience ?? '',
    });
    setEditing(true);
  }

  function submit(event) {
    event.preventDefault();
    const years = parseInt(draft.years_experience, 10);

    onSave({
      name: draft.name.trim() || undefined,
      skills: draft.skills.split(',').map((s) => s.trim()).filter(Boolean),
      titles: draft.titles.split(',').map((s) => s.trim()).filter(Boolean),
      seniority: draft.seniority,
      years_experience: Number.isNaN(years) ? undefined : years,
    });
    setEditing(false);
  }

  if (!hasResume) {
    return (
      <section className="panel upload-panel">
        <div className="upload-drop">
          <Upload size={30} />
          <h2>Start with your resume</h2>
          <p>
            PDF, DOCX or TXT. It is parsed for skills, job titles and seniority,
            and those drive every match from here on.
          </p>
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
            hidden
          />
          <button
            className="btn btn-primary"
            onClick={() => fileInput.current?.click()}
            disabled={uploading}
          >
            {uploading ? <Loader2 size={16} className="spin" /> : <Upload size={16} />}
            {uploading ? 'Reading it…' : 'Choose a file'}
          </button>
          <p className="upload-note">
            A scanned or image-only PDF has no text to read. Export a text PDF
            or upload a .docx instead.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2><FileText size={16} /> Your profile</h2>
        <div className="panel-actions">
          {!editing && (
            <button className="btn btn-ghost" onClick={startEditing}>
              <Pencil size={14} /> Correct it
            </button>
          )}
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
            hidden
          />
          <button className="btn btn-ghost" onClick={() => fileInput.current?.click()}>
            <Upload size={14} /> Replace
          </button>
          <button className="btn btn-ghost btn-danger" onClick={onDelete}>
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {editing ? (
        <form className="profile-form" onSubmit={submit}>
          {/* Everything here was guessed from unstructured text. A wrong
              skill list quietly ruins every match, and nobody can fix that
              faster than the person whose resume it is. */}
          <p className="form-note">
            These were read off your resume automatically. Fixing them changes
            what gets matched.
          </p>

          <label>
            Name
            <input
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="Used to sign cover letters"
            />
          </label>

          <label>
            Skills <span className="hint">comma separated; unknown ones are dropped</span>
            <textarea
              rows={3}
              value={draft.skills}
              onChange={(e) => setDraft({ ...draft, skills: e.target.value })}
            />
          </label>

          <label>
            Job titles <span className="hint">the first one becomes the search query</span>
            <input
              value={draft.titles}
              onChange={(e) => setDraft({ ...draft, titles: e.target.value })}
            />
          </label>

          <div className="form-row">
            <label>
              Seniority
              <select
                value={draft.seniority}
                onChange={(e) => setDraft({ ...draft, seniority: e.target.value })}
              >
                {SENIORITY.map((level) => (
                  <option key={level} value={level}>{level}</option>
                ))}
              </select>
            </label>

            <label>
              Years of experience
              <input
                type="number"
                min="0"
                max="60"
                value={draft.years_experience}
                onChange={(e) => setDraft({ ...draft, years_experience: e.target.value })}
              />
            </label>
          </div>

          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? <Loader2 size={15} className="spin" /> : <Check size={15} />}
              Save
            </button>
            <button className="btn btn-ghost" type="button" onClick={() => setEditing(false)}>
              <X size={15} /> Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <div className="profile-grid">
            <div>
              <span className="label">Name</span>
              <strong>{profile?.name || <em>not found</em>}</strong>
            </div>
            <div>
              <span className="label">Seniority</span>
              <strong>{profile?.seniority || '—'}</strong>
            </div>
            <div>
              <span className="label">Experience</span>
              <strong>
                {profile?.years_experience != null
                  ? `~${profile.years_experience} years`
                  : <em>not found</em>}
              </strong>
            </div>
            <div>
              <span className="label">Searching for</span>
              <strong>{query || '—'}</strong>
            </div>
          </div>

          {profile?.titles?.length > 0 && (
            <div className="chip-row">
              <span className="label">Titles</span>
              {profile.titles.map((t) => <span key={t} className="chip">{t}</span>)}
            </div>
          )}

          <div className="chip-row">
            <span className="label">
              Skills ({profile?.skills?.length || 0})
            </span>
            {(profile?.skills || []).map((s) => (
              <span key={s} className="chip chip-skill">{s}</span>
            ))}
            {!profile?.skills?.length && (
              <em className="muted">
                None recognised — correct them above, or matching will not work.
              </em>
            )}
          </div>

          {profile?.drive?.stored === false && (
            <p className="muted small">
              Not saved to Drive ({profile.drive.reason}) — this resume is on an
              ephemeral disk and will be lost when the service restarts.
            </p>
          )}
        </>
      )}
    </section>
  );
}
