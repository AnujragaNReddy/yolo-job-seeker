"""Drop-in Yolo-Auth verification for the FastAPI apps in this folder.

Copy this file into a backend, set two environment variables, and protect a
route with one dependency:

    from fastapi import Depends
    from yolo_auth import require_user, User

    @app.get("/api/private")
    def private(user: User = Depends(require_user)):
        return {"you are": user.email}

    AUTH_ISSUER=https://yolo-auth.onrender.com
    AUTH_AUDIENCE=yolo-automatic        # this app's name, optional

Verification happens here, in the app, against the issuer's published public
key. It does not call Yolo-Auth per request, and that is deliberate: these
services sleep on the free tier, and an app that had to reach the auth
service before answering would inherit its cold starts and its outages. The
key is fetched once and cached; a token is then checked with local maths.

The only thing this needs from the network is the JWKS, and only when its
cache is cold or a token arrives signed by a key it has not seen.
"""

import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

ISSUER = os.environ.get("AUTH_ISSUER", "https://yolo-auth.onrender.com").strip().rstrip("/")

# Which app this is. When set, a token minted for a different app is rejected,
# so one lifted from a low-value service cannot be replayed against this one.
AUDIENCE = os.environ.get("AUTH_AUDIENCE", "").strip()

# A shared secret for callers that are not people: the scheduled jobs that
# drive these apps. They have no browser, cannot complete a Google sign-in,
# and would be locked out the moment a route starts requiring a user.
#
# Deliberately separate from the user path rather than issuing a service
# account a long-lived JWT: this grants no identity, carries no roles, and
# is only ever accepted on the handful of routes that say so.
SERVICE_TOKEN = os.environ.get("AUTH_SERVICE_TOKEN", "").strip()

# Long, because keys change about never. A token signed by an unknown key
# refetches immediately regardless, so a rotation is picked up at once rather
# than after this expires.
JWKS_TTL_SECONDS = 3600
JWKS_TIMEOUT = 15

_lock = threading.Lock()
_keys: dict = {}
_fetched_at = 0.0


class AuthError(Exception):
    """A token was missing, malformed, expired, or not for this app."""


@dataclass
class User:
    id: str
    email: str
    name: str
    picture: str
    roles: list
    email_verified: bool
    claims: dict

    def has_role(self, role: str) -> bool:
        return role in (self.roles or [])


def _jwks_url() -> str:
    return f"{ISSUER}/.well-known/jwks.json"


def _load_keys(force: bool = False) -> dict:
    global _keys, _fetched_at

    with _lock:
        fresh = time.time() - _fetched_at < JWKS_TTL_SECONDS
        if _keys and fresh and not force:
            return _keys

        try:
            response = requests.get(_jwks_url(), timeout=JWKS_TIMEOUT)
            response.raise_for_status()
            document = response.json()
        except (requests.RequestException, ValueError) as e:
            # Keep serving with the cached key if there is one. The auth
            # service being briefly unreachable should not sign everybody out
            # of every app.
            if _keys:
                return _keys
            raise AuthError(f"Could not fetch signing keys from {_jwks_url()}: {e}") from e

        try:
            from jwt import algorithms
        except ImportError as e:
            raise AuthError("Needs PyJWT with crypto: pip install 'pyjwt[crypto]'") from e

        import json

        loaded = {}
        for key in document.get("keys", []):
            if key.get("kid"):
                loaded[key["kid"]] = algorithms.RSAAlgorithm.from_jwk(json.dumps(key))

        if not loaded:
            raise AuthError(f"{_jwks_url()} published no usable keys.")

        _keys, _fetched_at = loaded, time.time()
        return _keys


def verify(token: str) -> User:
    """Verify an access token and return who it belongs to."""
    try:
        import jwt
        from jwt import PyJWTError
    except ImportError as e:
        raise AuthError("Needs PyJWT with crypto: pip install 'pyjwt[crypto]'") from e

    if not token:
        raise AuthError("No token supplied.")

    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except PyJWTError as e:
        raise AuthError(f"Malformed token: {e}") from e

    keys = _load_keys()
    key = keys.get(kid)

    if key is None:
        # Unknown key id usually means a rotation since the cache was filled.
        keys = _load_keys(force=True)
        key = keys.get(kid)

    if key is None:
        raise AuthError(f"Token was signed by an unknown key ({kid}).")

    try:
        claims = jwt.decode(
            token,
            key,
            # Pinned explicitly. Leaving this open is how "alg: none" and
            # HMAC-with-the-public-key attacks get in.
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=AUDIENCE or None,
            options={"verify_aud": bool(AUDIENCE)},
        )
    except PyJWTError as e:
        raise AuthError(f"Token rejected: {e}") from e

    return User(
        id=claims.get("sub", ""),
        email=claims.get("email", ""),
        name=claims.get("name", ""),
        picture=claims.get("picture", ""),
        roles=claims.get("roles") or [],
        email_verified=bool(claims.get("email_verified")),
        claims=claims,
    )


def login_url(redirect_uri: str, client: str = "") -> str:
    """Where to send a browser to sign in."""
    from urllib.parse import urlencode

    params = {"redirect_uri": redirect_uri}
    if client or AUDIENCE:
        params["client"] = client or AUDIENCE

    return f"{ISSUER}/auth/google/start?{urlencode(params)}"


# --- FastAPI glue -----------------------------------------------------------

def require_user(authorization: Optional[str] = None):
    """FastAPI dependency. 401s when the token is missing or bad.

    Imported lazily so this module stays usable in a project that is not
    using FastAPI.
    """
    from fastapi import Header, HTTPException

    def _dependency(authorization: Optional[str] = Header(default=None)) -> Optional[User]:
        # Off until AUTH_ISSUER is set. Rolling this out across several apps
        # means deploying the code before the configuration exists, and a
        # half-configured deployment should behave exactly as it did
        # yesterday rather than locking everyone out of a working service.
        if not auth_enabled():
            return None

        if not authorization:
            raise HTTPException(status_code=401, detail="Sign in to use this.")

        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise HTTPException(
                status_code=401,
                detail="Expected 'Authorization: Bearer <token>'.",
            )

        try:
            return verify(value.strip())
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))

    return _dependency


def require_user_or_service():
    """For routes a scheduled job drives as well as a person.

    Accepts either a signed-in user or the shared service token. The service
    token is compared in constant time — a plain == on a secret leaks, through
    timing, how much of a guess was correct.

    Routes using this are the ones a cron job calls: locking them to a browser
    sign-in would silently stop the automation, and the failure would look
    like the feature being broken rather than like a permissions change.
    """
    import hmac

    from fastapi import Header, HTTPException

    def _dependency(
        authorization: Optional[str] = Header(default=None),
        x_service_token: Optional[str] = Header(default=None),
    ) -> Optional[User]:
        if not auth_enabled():
            return None

        if x_service_token and SERVICE_TOKEN:
            if hmac.compare_digest(x_service_token.strip(), SERVICE_TOKEN):
                return None  # a machine, not a person
            raise HTTPException(status_code=401, detail="Bad service token.")

        if not authorization:
            raise HTTPException(
                status_code=401,
                detail="Sign in, or send the X-Service-Token header.",
            )

        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise HTTPException(
                status_code=401, detail="Expected 'Authorization: Bearer <token>'."
            )

        try:
            return verify(value.strip())
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))

    return _dependency


def auth_enabled() -> bool:
    """Whether this app should enforce sign-in at all.

    With no issuer configured the apps keep working exactly as before. That
    matters while rolling this out across several of them: a half-configured
    deployment should degrade to its previous behaviour rather than locking
    everybody out of a service that was working an hour ago.
    """
    return bool(os.environ.get("AUTH_ISSUER", "").strip())


def optional_user():
    """For routes that behave differently when signed in but do not demand it."""
    from fastapi import Header

    def _dependency(authorization: Optional[str] = Header(default=None)) -> Optional[User]:
        if not authorization:
            return None
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            return None
        try:
            return verify(value.strip())
        except AuthError:
            return None

    return _dependency


def install_auth(app, public=(), service=(), enabled: Optional[bool] = None) -> None:
    """Require a signed-in user for every route except the ones named.

    One middleware with an explicit path policy, rather than a dependency on
    each handler. Retrofitting twenty-odd routes by hand across six apps is
    how a route gets missed, and a missed route is an unprotected one that
    looks protected. Here the whole policy is five lines you can read at once,
    and anything new is private by default.

    `public`  paths nobody needs to sign in for. Prefix matched, because
              /api/media/<file> is one rule rather than one per file.
    `service` paths a scheduled job drives. They take the service token, and
              a signed-in user as well.

    Getting the public list wrong is the dangerous part in both directions:
    too little and the automation breaks silently, too much and a private
    route is wide open. Each app states its own and explains why.

    CALL THIS BEFORE add_middleware(CORSMiddleware). Starlette runs the
    last-added middleware outermost, so adding CORS afterwards puts it
    outside this one and lets it attach headers to the 401s raised here.
    The other way round, a 401 goes back without CORS headers, the browser
    refuses to let the page read it, and a perfectly clear "sign in" arrives
    in the frontend as an unexplained network error instead. Verified rather
    than assumed: with CORS added first, a 401 carries no
    access-control-allow-origin at all.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    public = tuple(public)
    service = tuple(service)

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        active = auth_enabled() if enabled is None else enabled

        if not active:
            return await call_next(request)

        path = request.url.path

        # A CORS preflight never carries credentials - the browser sends it
        # before it will attach any. Rejecting it fails every cross-origin
        # request with what looks like a CORS misconfiguration.
        if request.method == "OPTIONS":
            return await call_next(request)

        if path.startswith(public):
            return await call_next(request)

        if path.startswith(service):
            supplied = (request.headers.get("x-service-token") or "").strip()
            if supplied and SERVICE_TOKEN:
                import hmac
                if hmac.compare_digest(supplied, SERVICE_TOKEN):
                    return await call_next(request)
                return JSONResponse({"detail": "Bad service token."}, status_code=401)

        authorization = request.headers.get("authorization") or ""
        scheme, _, value = authorization.partition(" ")

        if scheme.lower() != "bearer" or not value.strip():
            return JSONResponse(
                {"detail": "Sign in to use this.", "login": f"{ISSUER}/auth/google/start"},
                status_code=401,
            )

        try:
            request.state.user = verify(value.strip())
        except AuthError as e:
            return JSONResponse({"detail": str(e)}, status_code=401)

        return await call_next(request)


def describe() -> dict:
    return {
        "issuer": ISSUER,
        "audience": AUDIENCE or None,
        "jwks_url": _jwks_url(),
        "service_token_set": bool(SERVICE_TOKEN),
        "enabled": auth_enabled(),
        "keys_cached": len(_keys),
    }
