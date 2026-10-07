import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import { AuthProvider, RequireAuth, useAuth } from './useAuth';
import { setTokenProvider } from './api';

// Sign-in is off until VITE_AUTH_URL is set, which mirrors the backend
// refusing to enforce it until AUTH_ISSUER is set. Rolling this out across
// several apps means deploying the code before the configuration exists, and
// a half-configured deployment should behave exactly as it did yesterday
// rather than showing a sign-in wall in front of a service that has no auth
// backend to talk to.
const AUTH_URL = import.meta.env.VITE_AUTH_URL;

/** Hands the live access token to api.js, which is not a React module. */
function AuthBridge({ children }) {
  const { getToken } = useAuth();
  setTokenProvider(getToken);
  return children;
}

createRoot(document.getElementById('root')).render(
  AUTH_URL ? (
  <AuthProvider issuer={AUTH_URL} client="yolo-job-seeker">
      <AuthBridge>
        <RequireAuth fallback={<p className="auth-loading">Checking your session…</p>}>
          <App />
        </RequireAuth>
      </AuthBridge>
    </AuthProvider>
  ) : (
  <App />
  )
);
