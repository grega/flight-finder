"""Cloudflare Access identity verification for the admin endpoints.

Access sits in front of the admin paths and, once a visitor passes the SSO
policy, stamps every proxied request with a signed JWT (the
`Cf-Access-Jwt-Assertion` header, mirrored in the `CF_Authorization` cookie).
Verifying that JWT here is what makes Access real authentication rather than a
perimeter the origin merely hopes is in place: an unsigned request that reaches
the app another way carries no assertion and is rejected like any other.

Configure with two env vars (both required, else Access auth stays off):
  CF_ACCESS_TEAM_DOMAIN - e.g. yourteam.cloudflareaccess.com
  CF_ACCESS_AUD         - the Access application's Audience (AUD) tag
"""

import os

try:
    import jwt
    from jwt import PyJWKClient
except ImportError:  # PyJWT not installed - Access auth simply stays disabled.
    jwt = None
    PyJWKClient = None

TEAM_DOMAIN = os.getenv("CF_ACCESS_TEAM_DOMAIN", None)
AUD = os.getenv("CF_ACCESS_AUD", None)

# Cloudflare signs with RS256; ES256 appears on some older applications.
ALGORITHMS = ["RS256", "ES256"]
# Signing keys rotate roughly every 6 weeks; re-fetch hourly and on a miss.
JWKS_LIFESPAN = 3600

_jwk_client = None


def enabled():
    """True when the app is configured to accept Access identities."""
    return bool(jwt and TEAM_DOMAIN and AUD)


def _issuer():
    return f"https://{TEAM_DOMAIN}"


def _client(fresh=False):
    """The (cached) JWKS client. `fresh=True` drops the cache, for the case
    where a key rotated between our last fetch and this request."""
    global _jwk_client
    if fresh or _jwk_client is None:
        _jwk_client = PyJWKClient(
            f"{_issuer()}/cdn-cgi/access/certs",
            cache_keys=True,
            lifespan=JWKS_LIFESPAN,
        )
    return _jwk_client


def _decode(token, fresh=False):
    key = _client(fresh).get_signing_key_from_jwt(token).key
    return jwt.decode(
        token, key, algorithms=ALGORITHMS, audience=AUD, issuer=_issuer()
    )


def verify(token):
    """Verify an Access JWT, returning its claims, or None if it fails any
    check (signature, audience, issuer, expiry)."""
    if not enabled() or not token:
        return None
    try:
        return _decode(token)
    except Exception:
        pass
    # An unknown `kid` usually means a rotation we haven't picked up yet; one
    # forced re-fetch distinguishes that from a genuinely bad token.
    try:
        return _decode(token, fresh=True)
    except Exception:
        return None


def identity(request):
    """Claims for the Access-authenticated caller behind `request`, or None.
    Human logins carry `email`; service tokens carry `common_name`."""
    token = (request.headers.get("Cf-Access-Jwt-Assertion")
             or request.cookies.get("CF_Authorization"))
    return verify(token)
