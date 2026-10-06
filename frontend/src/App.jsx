import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Briefcase, Info, Loader2, RefreshCw, Search, ServerCrash,
} from 'lucide-react';
import ResumePanel from './components/ResumePanel';
import MatchList from './components/MatchList';
import ApplicationTracker from './components/ApplicationTracker';
import SettingsPanel from './components/SettingsPanel';
import {
  deleteResume,
  getApplications,
  getHealth,
  getMatches,
  getProfile,
  getSettings,
  getSources,
  prepareApplication,
  runScan,
  setApplicationState,
  updateProfile,
  updateSettings,
  uploadResume,
} from './api';

const PAGE_SIZE = 24;

// Waking a sleeping free instance. Backs off and then stops: a fixed-interval
// retry never ends, and a page left open overnight would hammer the service
// for nothing.
const WAKE_BACKOFF_MS = [4000, 8000, 16000, 32000, 60000, 60000];

export default function App() {
  const [health, setHealth] = useState(null);
  const [sources, setSources] = useState([]);
  const [profile, setProfile] = useState(null);
  const [hasResume, setHasResume] = useState(false);
  const [query, setQuery] = useState('');

  const [matches, setMatches] = useState([]);
  const [matchMeta, setMatchMeta] = useState({});
  const [nextOffset, setNextOffset] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [filter, setFilter] = useState('all');

  const [applications, setApplications] = useState([]);
  const [counts, setCounts] = useState({});
  const [letters, setLetters] = useState({});

  const [settings, setSettings] = useState(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);

  const [uploading, setUploading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [preparingId, setPreparingId] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [backendDown, setBackendDown] = useState(false);
  const [wakeAttempt, setWakeAttempt] = useState(0);
  const [stoppedWaking, setStoppedWaking] = useState(false);

  const loadProfile = useCallback(async () => {
    try {
      const data = await getProfile();
      setProfile(data.profile);
      setHasResume(data.has_resume);
      setQuery(data.query || '');
      setBackendDown(false);
    } catch (err) {
      if (err.backendDown) setBackendDown(true);
    }
  }, []);

  const loadMatches = useCallback(async (state) => {
    try {
      const data = await getMatches({
        limit: PAGE_SIZE,
        offset: 0,
        state: (state ?? 'all') === 'all' ? undefined : (state ?? undefined),
      });
      setMatches(data.items || []);
      setNextOffset(data.next_offset ?? null);
      setMatchMeta(data);

      // The letter text lives on the application record, not the match, so
      // seed what is already known from has_letter and fill in on demand.
      setBackendDown(false);
    } catch (err) {
      if (err.backendDown) setBackendDown(true);
    }
  }, []);

  const loadApplications = useCallback(async () => {
    try {
      const data = await getApplications();
      setApplications(data.applications || []);
      setCounts(data.counts || {});

      // Letters already drafted, so a reload does not look like they vanished.
      const known = {};
      for (const row of data.applications || []) {
        if (row.cover_letter) known[row.job_id] = row;
      }
      setLetters((previous) => ({ ...known, ...previous }));
    } catch (err) {
      if (err.backendDown) setBackendDown(true);
    }
  }, []);

  const loadAll = useCallback(() => {
    loadProfile();
    loadMatches(filter);
    loadApplications();
    getSettings().then((d) => setSettings(d.settings)).catch(() => {});
    getSources().then((d) => setSources(d.sources || [])).catch(() => {});
    getHealth().then(setHealth).catch(() => {});
  }, [loadProfile, loadMatches, loadApplications, filter]);

  useEffect(() => {
    loadAll();
    // Deliberately once on mount: nothing here changes on its own, and the
    // scan button reloads what it affects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!backendDown || stoppedWaking) return undefined;

    const delay = WAKE_BACKOFF_MS[Math.min(wakeAttempt, WAKE_BACKOFF_MS.length - 1)];

    const id = setTimeout(() => {
      getHealth()
        .then(() => {
          setBackendDown(false);
          setWakeAttempt(0);
          loadAll();
        })
        .catch(() => {
          if (wakeAttempt + 1 >= WAKE_BACKOFF_MS.length) setStoppedWaking(true);
          else setWakeAttempt((n) => n + 1);
        });
    }, delay);

    return () => clearTimeout(id);
  }, [backendDown, stoppedWaking, wakeAttempt, loadAll]);

  async function handleUpload(file) {
    setUploading(true);
    setError('');
    setNotice('');
    try {
      const data = await uploadResume(file);
      setProfile(data.profile);
      setHasResume(true);
      setNotice(
        `Found ${data.profile.skills?.length || 0} skills. Check they look right, then scan.`
      );
      loadProfile();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleSaveProfile(values) {
    setSavingProfile(true);
    try {
      const data = await updateProfile(values);
      setProfile(data.profile);
      loadProfile();
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingProfile(false);
    }
  }

  async function handleDeleteResume() {
    if (!window.confirm(
      'Remove the resume and all matches? Your application history is kept.'
    )) return;

    try {
      await deleteResume();
      setProfile(null);
      setHasResume(false);
      setMatches([]);
      setMatchMeta({});
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleScan() {
    setScanning(true);
    setError('');
    setNotice('');
    try {
      const result = await runScan(true);
      setNotice(
        `Searched "${result.query}" — ${result.counts.unique} postings, `
        + `${result.counts.matched} matched, ${result.counts.prepared} letters drafted.`
      );
      await Promise.all([loadMatches(filter), loadApplications()]);
    } catch (err) {
      if (err.backendDown) setBackendDown(true);
      else setError(err.message);
    } finally {
      setScanning(false);
    }
  }

  async function handleLoadMore() {
    if (nextOffset == null || loadingMore) return;
    setLoadingMore(true);
    try {
      const data = await getMatches({
        limit: PAGE_SIZE,
        offset: nextOffset,
        state: filter === 'all' ? undefined : filter,
      });
      setMatches((previous) => {
        const seen = new Set(previous.map((m) => m.id));
        return [...previous, ...(data.items || []).filter((m) => !seen.has(m.id))];
      });
      setNextOffset(data.next_offset ?? null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingMore(false);
    }
  }

  async function handlePrepare(jobId) {
    setPreparingId(jobId);
    setError('');
    try {
      const data = await prepareApplication(jobId);
      setLetters((previous) => ({ ...previous, [jobId]: data.application }));
      loadApplications();
    } catch (err) {
      setError(err.message);
    } finally {
      setPreparingId(null);
    }
  }

  async function handleState(jobId, state) {
    // Optimistic: the click should feel instant, and a failure reloads the
    // truth from the server anyway.
    setMatches((previous) =>
      previous.map((m) => (m.id === jobId ? { ...m, state } : m))
    );
    try {
      await setApplicationState(jobId, state);
      loadApplications();
    } catch (err) {
      setError(err.message);
      loadMatches(filter);
    }
  }

  async function handleSaveSettings(values) {
    setSavingSettings(true);
    try {
      const data = await updateSettings(values);
      setSettings(data.settings);
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingSettings(false);
    }
  }

  function handleFilter(value) {
    setFilter(value);
    loadMatches(value);
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-brand">
          <Briefcase size={20} />
          <h1>yolo-job-seeker</h1>
        </div>
        <div className="app-header-right">
          {health?.storage?.durable === false && (
            <span className="chip chip-warn" title="Set GOOGLE_* env vars">
              Drive off
            </span>
          )}
          <button className="btn btn-ghost" onClick={loadAll} title="Refresh">
            <RefreshCw size={15} />
          </button>
        </div>
      </header>

      <main className="app-main">
        {backendDown && (
          <div className="banner banner-error">
            <ServerCrash size={16} />
            <div>
              {stoppedWaking ? (
                <>
                  <strong>The backend isn&apos;t responding.</strong> Tried for a
                  few minutes and stopped.{' '}
                  <button className="link" onClick={() => {
                    setWakeAttempt(0);
                    setStoppedWaking(false);
                  }}>Try again</button>
                </>
              ) : (
                <>
                  <strong>Waking the backend…</strong> Free instances sleep after
                  a few minutes idle and take ~30–50s to start.
                </>
              )}
            </div>
          </div>
        )}

        {error && (
          <div className="banner banner-error">
            <AlertTriangle size={16} /> <div>{error}</div>
          </div>
        )}

        {notice && (
          <div className="banner banner-info">
            <Info size={16} /> <div>{notice}</div>
          </div>
        )}

        {/* Said once, plainly, where it cannot be missed. Automated submission
            is forbidden by every major board and gets accounts banned, so the
            app stops at the submit button on purpose. */}
        <div className="banner banner-info">
          <Info size={16} />
          <div>
            This finds and ranks jobs and drafts your letters. It does not
            submit applications — no major board allows that, and automated
            submissions get accounts banned. You open each posting and send it
            yourself, and the tracker remembers which.
          </div>
        </div>

        <ResumePanel
          profile={profile}
          hasResume={hasResume}
          query={query}
          uploading={uploading}
          saving={savingProfile}
          onUpload={handleUpload}
          onSave={handleSaveProfile}
          onDelete={handleDeleteResume}
        />

        {hasResume && (
          <>
            <section className="scan-bar">
              <div>
                <h2>Find matching jobs</h2>
                <p>
                  Searches every active board, scores each posting against your
                  resume, and drafts letters for the best ones.
                </p>
              </div>
              <button className="btn btn-primary btn-big" onClick={handleScan}
                      disabled={scanning}>
                {scanning ? <Loader2 size={17} className="spin" /> : <Search size={17} />}
                {scanning ? 'Searching…' : 'Scan now'}
              </button>
            </section>

            <SettingsPanel
              settings={settings}
              sources={sources}
              onSave={handleSaveSettings}
              saving={savingSettings}
            />

            <MatchList
              items={matches}
              total={matchMeta.total || 0}
              scannedAt={matchMeta.scanned_at}
              query={matchMeta.query}
              sources={matchMeta.sources}
              scanCounts={matchMeta.scan_counts}
              hasMore={nextOffset != null}
              loadingMore={loadingMore}
              onLoadMore={handleLoadMore}
              onPrepare={handlePrepare}
              onState={handleState}
              preparingId={preparingId}
              letters={letters}
              filter={filter}
              onFilter={handleFilter}
            />

            <ApplicationTracker
              applications={applications}
              counts={counts}
              onState={handleState}
            />
          </>
        )}
      </main>
    </div>
  );
}
