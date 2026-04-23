"""
iracing_auth.py
---------------
Minimal client for iRacing's "members-ng" Data API (the one the new UI uses).

Usage:
    from iracing_auth import IRacingClient

    client = IRacingClient(email, password, cache_dir=Path("trackmaps/cache"))
    assets = client.get_track_assets()
    client.download_track_map(track_id=123, dest_dir=Path("trackmaps/cache/123"))

Credentials are passed in; this module does NOT read them from disk. See
iracing_trackmap.py for the config-file loading layer.

The session cookie is persisted across runs in <cache_dir>/session.json
so we don't hammer the login endpoint. If iRacing returns 401 on any
subsequent call, we transparently re-authenticate once and retry.
"""

from __future__ import annotations
import base64
import hashlib
import json
import time
from pathlib import Path

import requests


BASE_URL = "https://members-ng.iracing.com"
USER_AGENT = "iRacing-Overlay/1.0 (Thomas' personal OBS tooling)"

# HTTP timeout for all iRacing calls. Track-asset S3 downloads use a longer one.
HTTP_TIMEOUT = 20
ASSET_TIMEOUT = 60


class IRacingAuthError(RuntimeError):
    """Raised when login fails or verification is required."""


class IRacingClient:
    def __init__(self, email: str, password: str, cache_dir: Path):
        if not email or not password:
            raise IRacingAuthError("email and password are required")
        self.email = email.lower().strip()
        self._password_enc = self._encode_password(email, password)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cookie_file = self.cache_dir / "session.json"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._authed = False
        self._load_cookies()

    # ----- auth -----------------------------------------------------------
    @staticmethod
    def _encode_password(email: str, password: str) -> str:
        """iRacing expects base64(sha256(password + lowercased_email))."""
        h = hashlib.sha256((password + email.lower()).encode("utf-8")).digest()
        return base64.b64encode(h).decode("utf-8")

    def _load_cookies(self) -> None:
        if not self._cookie_file.is_file():
            return
        try:
            data = json.loads(self._cookie_file.read_text())
            # Don't trust cookies older than 12h — iRacing sessions expire.
            if time.time() - data.get("saved_at", 0) > 12 * 3600:
                return
            for name, value in (data.get("cookies") or {}).items():
                self.session.cookies.set(name, value)
            self._authed = True
        except Exception:
            # Ignore — we'll just log in again.
            pass

    def _save_cookies(self) -> None:
        try:
            payload = {
                "saved_at": time.time(),
                "email": self.email,
                "cookies": {c.name: c.value for c in self.session.cookies},
            }
            self._cookie_file.write_text(json.dumps(payload))
        except Exception as e:
            print(f"[iracing_auth] WARNING: could not save session: {e}")

    def authenticate(self) -> None:
        """Perform a fresh login. Raises IRacingAuthError on failure."""
        try:
            r = self.session.post(
                f"{BASE_URL}/auth",
                json={"email": self.email, "password": self._password_enc},
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as e:
            raise IRacingAuthError(f"network error during login: {e}")

        if r.status_code != 200:
            raise IRacingAuthError(
                f"login HTTP {r.status_code}: {r.text[:200]}"
            )

        try:
            resp = r.json()
        except ValueError:
            raise IRacingAuthError("login response was not JSON")

        if resp.get("authcode") == 0:
            reason = resp.get("message") or "unknown"
            raise IRacingAuthError(f"login rejected: {reason}")
        if resp.get("verificationRequired"):
            raise IRacingAuthError(
                "iRacing is asking for extra verification (likely 2FA or a new "
                "device confirmation email). Resolve it in your browser, then retry."
            )

        self._authed = True
        self._save_cookies()
        print("[iracing_auth] Authenticated")

    # ----- low-level request with auto re-auth ---------------------------
    def _request_json(self, path: str) -> dict | list:
        if not self._authed:
            self.authenticate()
        url = f"{BASE_URL}{path}" if path.startswith("/") else path
        r = self.session.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 401:
            self._authed = False
            self.authenticate()
            r = self.session.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # Many iRacing data endpoints return {"link": "<signed s3 url>"} — the
        # actual payload sits behind that link.
        if isinstance(data, dict) and set(data.keys()) >= {"link"} and "expires" in data:
            r2 = requests.get(data["link"], timeout=HTTP_TIMEOUT)
            r2.raise_for_status()
            return r2.json()
        return data

    def get_binary(self, url: str) -> bytes:
        """Download a static asset (e.g. SVG from the CDN). No auth needed for those."""
        r = requests.get(url, timeout=ASSET_TIMEOUT, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.content

    # ----- tracks --------------------------------------------------------
    def get_track_assets(self) -> dict:
        """
        Returns the full track-assets dict keyed by track_id (as string).
        Each entry has 'track_map' (CDN base URL) and 'track_map_layers'
        (a dict of layer_name -> filename).
        """
        return self._request_json("/data/track/assets")

    def download_track_map(self, track_id: int, dest_dir: Path) -> dict:
        """
        Download every SVG layer for the given track_id into dest_dir, and
        write a manifest.json describing what was saved.

        Returns the manifest.
        """
        assets = self.get_track_assets()
        entry = assets.get(str(track_id))
        if not entry:
            raise IRacingAuthError(f"track {track_id} not in /data/track/assets")

        base_url = entry.get("track_map") or ""
        layers = entry.get("track_map_layers") or {}
        if not base_url or not layers:
            raise IRacingAuthError(f"track {track_id} has no map layers")

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        saved: dict[str, str] = {}
        for layer_name, filename in layers.items():
            url = base_url + filename
            try:
                data = self.get_binary(url)
            except requests.RequestException as e:
                print(f"[iracing_auth] failed to fetch {layer_name}: {e}")
                continue
            (dest_dir / filename).write_bytes(data)
            saved[layer_name] = filename

        manifest = {
            "track_id":   track_id,
            "track_name": entry.get("track_name", ""),
            "base_url":   base_url,
            "layers":     saved,
            "saved_at":   time.time(),
        }
        (dest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest
