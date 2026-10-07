/**
 * Drop-in Yolo-Auth for the React frontends in this folder.
 *
 * Copy this file in, wrap the app, and use the hook:
 *
 *   import { AuthProvider, useAuth } from './useAuth';
 *
 *   createRoot(el).render(
 *     <AuthProvider issuer={import.meta.env.VITE_AUTH_URL} client="yolo-automatic">
 *       <App />
 *     </AuthProvider>
 *   );
 *
 *   const { user, loading, signIn, signOut, authFetch } = useAuth();
 *
 * Where the tokens live, and why:
 *
 * The access token is kept in memory only. It never goes into localStorage,
 * so a cross-site scripting bug on any page cannot read it out and walk away
 * with a live session. It dies with the tab, which is the point.
 *
 * The refresh token does go in localStorage, because something has to survive
 * a reload or you are signing in on every page load. That is a real trade
 * rather than a free one: localStorage is readable by script on the origin.
 * What makes it acceptable is that the refresh token is single-use and
 * rotates on every exchange, so a stolen one stops working the moment the
 * real owner refreshes, and the theft becomes visible as an unexpected
 * sign-out rather than a silent indefinite session.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';

const AuthContext = createContext(null);

const REFRESH_KEY = 'yolo-auth.refresh';

// Refresh this long before the access token actually expires, so a request
// never goes out holding a token that dies in transit.
const REFRESH_MARGIN_MS = 60 * 1000;

function readStored() {
  try {
    return localStorage.getItem(REFRESH_KEY) || '';
  } catch {
    // Private windows and blocked site data both throw here. Sign-in still
    // works for the life of the tab.
    return '';
  }
}

function writeStored(token) {
  try {
    if (token) localStorage.setItem(REFRESH_KEY, token);
    else localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* see readStored */
  }
}

export function AuthProvider({ children, issuer, client = '', redirectUri }) {
  const base = (issuer || '').replace(/\/$/, '');
  const landing = redirectUri || `${window.location.origin}/auth/done`;

  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // In memory on purpose. See the note at the top of this file.
  const accessToken = useRef('');
  const expiresAt = useRef(0);
  const refreshing = useRef(null);

  const applySession = useCallback((data) => {
    accessToken.current = data.access_token || '';
    expiresAt.current = Date.now() + (data.expires_in || 0) * 1000;
    if (data.refresh_token) writeStored(data.refresh_token);
    setUser(data.user || null);
  }, []);

  const clearSession = useCallback(() => {
    accessToken.current = '';
    expiresAt.current = 0;
    writeStored('');
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    const stored = readStored();
    if (!stored) return null;

    // One refresh at a time. Several requests noticing an expired token at
    // once would otherwise each spend the same single-use refresh token, and
    // all but the first would fail and sign the user out.
    if (refreshing.current) return refreshing.current;

    refreshing.current = (async () => {
      try {
        const response = await fetch(`${base}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: stored, client }),
        });

        if (!response.ok) {
          clearSession();
          return null;
        }

        const data = await response.json();
        applySession(data);
        return data;
      } catch {
        // Network failure, not a rejected token. Keep what we have; the next
        // attempt may work, and signing out on a flaky connection is worse.
        return null;
      } finally {
        refreshing.current = null;
      }
    })();

    return refreshing.current;
  }, [base, client, applySession, clearSession]);

  // On load: finish a sign-in if we just came back from one, otherwise try to
  // restore a session from the stored refresh token.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const hash = new URLSearchParams(window.location.hash.slice(1));
      const code = hash.get('code');
      const failed = hash.get('error');

      if (failed) {
        setError(decodeURIComponent(failed));
        history.replaceState(null, '', window.location.pathname);
        setLoading(false);
        return;
      }

      if (code) {
        // Clear the fragment before anything else can read it, and so a
        // reload does not try to spend an already-used code.
        history.replaceState(null, '', window.location.pathname);

        try {
          const response = await fetch(`${base}/auth/exchange`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
          });

          if (!response.ok) {
            const body = await response.json().catch(() => null);
            throw new Error(body?.detail || 'Sign-in could not be completed.');
          }

          if (!cancelled) applySession(await response.json());
        } catch (err) {
          if (!cancelled) setError(err.message);
        }
      } else {
        await refresh();
      }

      if (!cancelled) setLoading(false);
    })();

    return () => { cancelled = true; };
  }, [base, applySession, refresh]);

  const signIn = useCallback(() => {
    const params = new URLSearchParams({ redirect_uri: landing });
    if (client) params.set('client', client);
    window.location.href = `${base}/auth/google/start?${params}`;
  }, [base, client, landing]);

  const signOut = useCallback(async () => {
    const stored = readStored();
    clearSession();

    if (stored) {
      try {
        await fetch(`${base}/auth/logout`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: stored }),
        });
      } catch {
        // Locally signed out regardless; the token expires on its own.
      }
    }
  }, [base, clearSession]);

  /** fetch() with the access token attached, refreshed first if it is due. */
  const authFetch = useCallback(async (url, options = {}) => {
    if (accessToken.current && Date.now() > expiresAt.current - REFRESH_MARGIN_MS) {
      await refresh();
    }

    const headers = { ...(options.headers || {}) };
    if (accessToken.current) headers.Authorization = `Bearer ${accessToken.current}`;

    let response = await fetch(url, { ...options, headers });

    // One retry on a 401: the token may have been revoked or the clock may
    // have drifted. If the refresh also fails, the 401 is genuine.
    if (response.status === 401 && readStored()) {
      const renewed = await refresh();
      if (renewed?.access_token) {
        response = await fetch(url, {
          ...options,
          headers: { ...headers, Authorization: `Bearer ${renewed.access_token}` },
        });
      }
    }

    return response;
  }, [refresh]);

  const value = useMemo(() => ({
    user,
    loading,
    error,
    signedIn: Boolean(user),
    signIn,
    signOut,
    authFetch,
    getToken: () => accessToken.current,
  }), [user, loading, error, signIn, signOut, authFetch]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);

  if (!value) {
    throw new Error('useAuth must be used inside an <AuthProvider>.');
  }

  return value;
}

/** Renders children only when signed in, and a sign-in prompt when not. */
export function RequireAuth({ children, fallback = null }) {
  const { signedIn, loading, signIn, error } = useAuth();

  if (loading) return fallback;

  if (!signedIn) {
    return (
      <div className="auth-gate">
        <h2>Sign in to continue</h2>
        {error && <p className="auth-error">{error}</p>}
        <button onClick={signIn}>Continue with Google</button>
      </div>
    );
  }

  return children;
}
