# -*- coding: utf-8 -*-
"""
oauth.py
========

Google OAuth2 "installed app" loopback-redirect flow, cloned from Public
GDrive Library's ``gdrive_oauth.py`` almost verbatim (PKCE + a temporary
``127.0.0.1`` HTTP server that catches the redirect, pure standard-library
implementation so no extra pip package is required inside Calibre's
bundled Python).

The one functional change from the source plugin: Public GDrive Library
only ever *read* public folders, so it requested the read-only
``drive.readonly`` scope. This plugin uploads, updates, deletes and
manages sharing permissions for files it creates, so it requests the
non-readonly ``drive.file`` scope instead -- this is intentionally
*narrower* than full ``drive`` access: it only grants access to files and
folders the app itself creates (or that the user explicitly opens with
the picker), which is exactly what "Calibre Library" folder management
needs and keeps the OAuth consent screen minimal.
"""

__copyright__ = '2026, Google Drive Sync Plugin Authors'
__license__ = 'GPL v3'

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from audiobookgdrive.jsonconfig import JSONConfig

AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
USERINFO_ENDPOINT = 'https://openidconnect.googleapis.com/v1/userinfo'

# drive.file: read/write access limited to files created by this app, plus
# openid/email so we can show "signed in as ...".
SCOPES = 'openid email https://www.googleapis.com/auth/drive.file'

# Scope for the "Device Library Sync" feature (see device_sync/ package):
# it must be able to see/read the folder + files the Android app "Calibre
# Sync" already created before the desktop plugin ever ran (its Device
# Library Sync uses full Drive access -- OAuthProviderSpec.java,
# "https://www.googleapis.com/auth/drive"), which drive.file cannot do
# (drive.file only sees files/folders this app itself created). Kept as a
# SEPARATE scope/flow from SCOPES above so users of the existing "Calibre
# Library" sync are never forced to re-consent to broader access they
# don't need.
DEVICE_SYNC_SCOPES = 'openid email https://www.googleapis.com/auth/drive'

# Token storage is keyed by "flow" so the two features never share (or
# clobber) each other's tokens, even though both may be signed in with the
# same Google account: 'library' is the existing drive.file flow (default,
# preserves on-disk key names for backward compatibility with existing
# users' saved tokens); 'device_sync' is the new full-Drive-access flow.
_FLOW_KEYS = {
    'library': {
        'access_token': 'access_token',
        'refresh_token': 'refresh_token',
        'expires_at': 'expires_at',
        'email': 'email',
    },
    'device_sync': {
        'access_token': 'device_sync_access_token',
        'refresh_token': 'device_sync_refresh_token',
        'expires_at': 'device_sync_expires_at',
        'email': 'device_sync_email',
    },
}

# ---------------------------------------------------------------------------
# Shared OAuth client (zero-setup sign-in for end users)
# ---------------------------------------------------------------------------
# Google's "Desktop app" / "installed application" OAuth client type does
# NOT treat client_secret as a real secret -- PKCE (already implemented
# above) is what actually protects this flow; the "secret" is just a
# legacy request parameter Google still asks for. So it's safe (and it's
# exactly what tools like rclone, gdrive-cli, gphotos-sync etc. do) to bake
# a single OAuth client -- owned by the plugin author -- into the plugin
# itself. End users then never see "Client ID / Client Secret" at all: they
# click "Sign in with Google", authorize with *their own* Google account,
# and the resulting tokens are stored only on their own machine and can
# only reach *their own* Drive (scope is drive.file, not full drive).
#
# ONE-TIME SETUP FOR THE PLUGIN AUTHOR (not the end user):
#   1. https://console.cloud.google.com/ -> create/select a project.
#   2. "APIs & Services" -> "Library" -> enable "Google Drive API".
#   3. "APIs & Services" -> "OAuth consent screen":
#        - User type: External
#        - Scopes: add .../auth/drive.file, openid, email (all
#          "non-sensitive" scopes -> the light verification track).
#        - Publish status: "In production" (NOT "Testing" -- Testing caps
#          you at 100 hand-whitelisted users). While unverified, users see
#          a "Google hasn't verified this app" screen with a "Continue"
#          link; that's fine to ship with, submit for verification later
#          to remove the warning.
#   4. "APIs & Services" -> "Credentials" -> "Create Credentials" ->
#      "OAuth client ID" -> Application type: "Desktop app".
#   5. Paste the resulting values below and rebuild the plugin zip.
#
# Left blank in the public source tree; fill these in locally before
# building the distributed .zip.
DEFAULT_CLIENT_ID = '987737400403-ictnn7lvbd3s13gupmcrev54fve9ohvl.apps.googleusercontent.com'
DEFAULT_CLIENT_SECRET = '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'

_token_store = JSONConfig('plugins/gdrive_sync_tokens')
_token_store.defaults['access_token'] = ''
_token_store.defaults['refresh_token'] = ''
_token_store.defaults['expires_at'] = 0
_token_store.defaults['email'] = ''
_token_store.defaults['device_sync_access_token'] = ''
_token_store.defaults['device_sync_refresh_token'] = ''
_token_store.defaults['device_sync_expires_at'] = 0
_token_store.defaults['device_sync_email'] = ''


class OAuthError(Exception):
    pass


def _generate_pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b'=').decode('ascii')
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.oauth_result = params

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        if 'code' in params:
            body = (
                '<html><head><meta charset="utf-8"></head><body '
                'style="font-family:sans-serif;text-align:center;margin-top:80px">'
                '<h2>Google sign-in successful</h2>'
                '<p>You can close this tab and return to Calibre.</p>'
                '</body></html>'
            )
        else:
            body = (
                '<html><head><meta charset="utf-8"></head><body '
                'style="font-family:sans-serif;text-align:center;margin-top:80px">'
                '<h2>Sign-in failed or was canceled.</h2>'
                '<p>Please return to Calibre and try again.</p>'
                '</body></html>'
            )
        self.wfile.write(body.encode('utf-8'))

    def log_message(self, format, *args):
        pass


def _post_form(url, fields, timeout=30):
    data = urllib.parse.urlencode(fields).encode('ascii')
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        raise OAuthError('Google returned HTTP error %d: %s' % (e.code, body[:400]))
    except urllib.error.URLError as e:
        raise OAuthError('Could not connect to Google: %s' % e)


def _fetch_userinfo_email(access_token, timeout=15):
    req = urllib.request.Request(
        USERINFO_ENDPOINT,
        headers={'Authorization': 'Bearer %s' % access_token},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data.get('email', '')


def _exchange_code(client_id, client_secret, code, redirect_uri, verifier, timeout=30):
    payload = _post_form(TOKEN_ENDPOINT, {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'code_verifier': verifier,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
    }, timeout=timeout)

    if 'access_token' not in payload:
        raise OAuthError('Google response did not contain an access_token: %s' % payload)

    tokens = {
        'access_token': payload['access_token'],
        'refresh_token': payload.get('refresh_token', ''),
        'expires_at': time.time() + float(payload.get('expires_in', 3600)),
    }
    try:
        tokens['email'] = _fetch_userinfo_email(tokens['access_token'], timeout=timeout)
    except Exception:
        tokens['email'] = ''
    return tokens


def _refresh_access_token(client_id, client_secret, refresh_token, timeout=30):
    payload = _post_form(TOKEN_ENDPOINT, {
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }, timeout=timeout)

    if 'access_token' not in payload:
        raise OAuthError('Could not refresh token: %s' % payload)

    return {
        'access_token': payload['access_token'],
        'refresh_token': refresh_token,
        'expires_at': time.time() + float(payload.get('expires_in', 3600)),
    }


def interactive_login(client_id, client_secret, timeout=180, log=None, scopes=None):
    """Blocking: run the full sign-in flow. Must be called from a worker
    thread (see :class:`audiobookgdrive.login_dialog.LoginWorker`),
    never directly from the GUI thread.

    ``scopes`` defaults to :data:`SCOPES` (the existing "Calibre Library"
    drive.file flow); pass :data:`DEVICE_SYNC_SCOPES` for the Device
    Library Sync flow instead."""
    client_id = (client_id or '').strip()
    client_secret = (client_secret or '').strip()
    if not client_id or not client_secret:
        raise OAuthError(
            'Client ID / Client Secret not set (Google Cloud Console). '
            'See README.md for instructions on creating an OAuth Client.'
        )

    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    server = HTTPServer(('127.0.0.1', 0), _CallbackHandler)
    server.oauth_result = None
    port = server.server_address[1]
    redirect_uri = 'http://127.0.0.1:%d/' % port

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': scopes or SCOPES,
        'access_type': 'offline',
        'prompt': 'consent',
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'state': state,
    }
    auth_url = AUTH_ENDPOINT + '?' + urllib.parse.urlencode(params)

    if log:
        log('Opening browser to sign in to Google (local port %d)...' % port)
    if not webbrowser.open(auth_url):
        server.server_close()
        raise OAuthError('Could not open a browser automatically. Open this URL manually:\n%s' % auth_url)

    server.timeout = 1
    deadline = time.time() + timeout
    try:
        while server.oauth_result is None and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    result = server.oauth_result
    if not result:
        raise OAuthError('Sign-in timed out (%d seconds). Please try again.' % timeout)
    if 'error' in result:
        raise OAuthError('Google denied the sign-in request: %s' % result['error'][0])
    if result.get('state', [None])[0] != state:
        raise OAuthError('The "state" parameter did not match (possible CSRF) - sign-in aborted.')
    code = (result.get('code') or [None])[0]
    if not code:
        raise OAuthError('Did not receive an authorization code from Google.')

    if log:
        log('Authorization code received, exchanging for access token...')
    return _exchange_code(client_id, client_secret, code, redirect_uri, verifier, timeout=30)


def save_tokens(tokens, flow='library'):
    keys = _FLOW_KEYS[flow]
    _token_store[keys['access_token']] = tokens.get('access_token', '')
    new_refresh = tokens.get('refresh_token')
    _token_store[keys['refresh_token']] = (
        new_refresh if new_refresh else (_token_store.get(keys['refresh_token']) or ''))
    _token_store[keys['expires_at']] = tokens.get('expires_at', 0)
    _token_store[keys['email']] = tokens.get('email', _token_store.get(keys['email']) or '')


def logout(flow='library'):
    keys = _FLOW_KEYS[flow]
    _token_store[keys['access_token']] = ''
    _token_store[keys['refresh_token']] = ''
    _token_store[keys['expires_at']] = 0
    _token_store[keys['email']] = ''


def is_logged_in(flow='library'):
    return bool(_token_store.get(_FLOW_KEYS[flow]['refresh_token']))


def get_account_email(flow='library'):
    return _token_store.get(_FLOW_KEYS[flow]['email']) or ''


def get_effective_credentials():
    """Return (client_id, client_secret) to actually use.

    An explicit override in Settings -> Advanced (power users who want to
    use their own Google Cloud project/quota) always wins; otherwise fall
    back to the plugin's built-in shared OAuth client, so ordinary users
    never have to create anything on Google Cloud Console themselves.
    """
    from audiobookgdrive.config import prefs
    client_id = (prefs.get('client_id') or '').strip() or DEFAULT_CLIENT_ID
    client_secret = (prefs.get('client_secret') or '').strip() or DEFAULT_CLIENT_SECRET
    return client_id, client_secret


def has_usable_client():
    client_id, client_secret = get_effective_credentials()
    return bool(client_id and client_secret)


def get_valid_access_token(client_id, client_secret, flow='library'):
    """Return a valid access token, refreshing it if needed. Raises
    OAuthError if not signed in or if the refresh fails.

    ``flow`` selects which set of stored tokens to use -- 'library' (the
    existing drive.file flow, default) or 'device_sync' (the full-Drive
    Device Library Sync flow, a fully independent sign-in even when both
    use the same Google account)."""
    keys = _FLOW_KEYS[flow]
    refresh_token = _token_store.get(keys['refresh_token']) or ''
    if not refresh_token:
        raise OAuthError('Not signed in to Google Drive. Use "Sign in with Google" in Settings first.')

    access_token = _token_store.get(keys['access_token']) or ''
    expires_at = _token_store.get(keys['expires_at']) or 0
    if access_token and time.time() < (expires_at - 120):
        return access_token

    client_id = (client_id or '').strip()
    client_secret = (client_secret or '').strip()
    if not client_id or not client_secret:
        raise OAuthError('Client ID / Client Secret missing from Settings.')

    tokens = _refresh_access_token(client_id, client_secret, refresh_token)
    save_tokens(tokens, flow=flow)
    return tokens['access_token']
