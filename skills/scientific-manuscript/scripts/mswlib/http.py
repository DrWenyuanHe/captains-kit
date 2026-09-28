"""A polite HTTP client for PubMed E-utilities and Crossref (standard library only).

- Talks only to the hosts in ALLOWED_HOSTS, over HTTPS. The transport never
  follows a redirect itself: the client follows one only to an https URL on an
  allowed host, at most MAX_REDIRECTS times, and refuses any other as a FetchError.
- Sends a User-Agent that names the tool and, when MSW_CONTACT_EMAIL is set, a
  contact address. No address is built in. The contact address and the NCBI API
  key go only to the host the request was made for: a redirect to another
  allowed host drops them from the User-Agent, the query and the form body.
- Spaces requests per host: NCBI allows 3 requests/s without an API key and 10/s
  with NCBI_API_KEY; Crossref is asked politely (at most 5/s).
- Retries 429, 5xx, timeouts and connection errors with exponential backoff and
  honours Retry-After. Other errors raise FetchError at once, so a failed fetch is
  never mistaken for an empty result.
- Caches successful responses in a content-addressed folder. Entries are written
  once with exclusive create and checked against their SHA-256 when read.
- The transport is pluggable, so tests run without a network.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

TOOL_NAME = "captains-kit-msw"
USER_AGENT = f"{TOOL_NAME}/{__version__} (scientific-manuscript skill; Python urllib)"
CONTACT_ENV = "MSW_CONTACT_EMAIL"
NCBI_KEY_ENV = "NCBI_API_KEY"
CACHE_ENV = "MSW_HTTP_CACHE"

NCBI_HOST = "eutils.ncbi.nlm.nih.gov"
CROSSREF_HOST = "api.crossref.org"
ALLOWED_HOSTS = frozenset({NCBI_HOST, CROSSREF_HOST})

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
# Request parameters that identify the caller rather than the resource: they are
# left out of cache keys and redacted from archived request records.
SECRET_PARAMS = frozenset({"api_key", "email"})
VOLATILE_PARAMS = SECRET_PARAMS | {"tool"}
CACHE_SCHEMA = "msw-http-cache/1"


class FetchError(RuntimeError):
    """A request that failed after the allowed retries, or with a non-retryable status."""

    def __init__(self, message: str, *, url: str = "", status: int | None = None, attempts: int = 0):
        super().__init__(message)
        self.url = url
        self.status = status
        self.attempts = attempts


class HostNotAllowed(ValueError):
    """Raised for any URL outside ALLOWED_HOSTS or not using HTTPS."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def contact_email() -> str:
    return os.environ.get(CONTACT_ENV, "").strip()


def ncbi_api_key() -> str:
    return os.environ.get(NCBI_KEY_ENV, "").strip()


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    url: str                      # redacted URL (no api_key or email)
    method: str = "GET"
    from_cache: bool = False
    attempts: int = 0
    fetched_utc: str = ""
    cache_key: str = ""
    params: dict = field(default_factory=dict)   # redacted

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def record(self) -> dict:
        """A request record for an evidence archive. Secrets are redacted."""
        return {"method": self.method, "url": self.url, "params": self.params, "status": self.status,
                "fetched_utc": self.fetched_utc, "from_cache": self.from_cache, "attempts": self.attempts,
                "bytes": len(self.body), "sha256": self.sha256,
                "content_type": self.headers.get("content-type", ""), "cache_key": self.cache_key}


# -- transport ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the 30x comes back to the Client, which checks the target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    """Default transport: returns (status, headers, body); raises OSError on network failure.

    Redirects are returned as they are (status 30x with its Location header), never followed.
    """
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as reply:  # noqa: S310 (host checked by Client)
            return reply.status, {k.lower(): v for k, v in reply.headers.items()}, reply.read()
    except urllib.error.HTTPError as error:
        data = error.read() if error.fp is not None else b""
        return error.code, {k.lower(): v for k, v in (error.headers or {}).items()}, data


def retry_after_seconds(value: str | None, now: float | None = None) -> float | None:
    """Parse a Retry-After header (delta seconds or an HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now if now is not None else time.time()
    return max(0.0, when.timestamp() - current)


def _redact_params(params: dict) -> dict:
    return {k: ("REDACTED" if k in SECRET_PARAMS else v) for k, v in params.items()}


def _encode(params: dict) -> str:
    return urllib.parse.urlencode(list(params.items()), doseq=True)


# -- client ------------------------------------------------------------------------------

class Client:
    """Sequential, polite client. Construct once per command and reuse it."""

    def __init__(self, *, cache_dir=None, transport=None, contact: str | None = None,
                 api_key: str | None = None, sleep=None, clock=None, rng=None, max_retries: int = 4,
                 timeout: float = 30.0, max_wait: float = 120.0, allowed_hosts=ALLOWED_HOSTS):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.transport = transport or urllib_transport
        self.contact = contact_email() if contact is None else contact.strip()
        self.api_key = ncbi_api_key() if api_key is None else api_key.strip()
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.rng = rng or random.random
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_wait = max_wait
        self.allowed_hosts = frozenset(allowed_hosts)
        self.last_request: dict = {}
        self.warnings: list[str] = []
        self.network_requests = 0

    # -- identity ---------------------------------------------------------------------
    @property
    def user_agent(self) -> str:
        return f"{USER_AGENT[:-1]}; mailto:{self.contact})" if self.contact else USER_AGENT

    def ncbi_params(self) -> dict:
        """tool, email and api_key parameters for E-utilities."""
        params = {"tool": TOOL_NAME}
        if self.contact:
            params["email"] = self.contact
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def min_interval(self, host: str) -> float:
        if host == NCBI_HOST:
            return 0.1 if self.api_key else 0.34
        if host == CROSSREF_HOST:
            return 0.2
        return 1.0

    # -- helpers ----------------------------------------------------------------------
    def _check_url(self, url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise HostNotAllowed(f"only https URLs are allowed: {url}")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise HostNotAllowed(f"host not allowed: {host} (allowed: {', '.join(sorted(self.allowed_hosts))})")
        return host

    def _redact(self, text: str) -> str:
        for secret in (self.api_key, self.contact, urllib.parse.quote(self.contact) if self.contact else ""):
            if secret:
                text = text.replace(secret, "REDACTED")
        return text

    @staticmethod
    def cache_key(method: str, url: str, params: dict | None, data: dict | None) -> str:
        canonical = {
            "method": method.upper(), "url": url,
            "params": sorted((k, str(v)) for k, v in (params or {}).items() if k not in VOLATILE_PARAMS),
            "data": sorted((k, str(v)) for k, v in (data or {}).items() if k not in VOLATILE_PARAMS),
        }
        return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / key[:2] / f"{key}.bin" if self.cache_dir else None

    def _cache_read(self, key: str) -> Response | None:
        path = self._cache_path(key)
        if path is None or not path.is_file():
            return None
        blob = path.read_bytes()
        head, sep, body = blob.partition(b"\n")
        try:
            meta = json.loads(head.decode("utf-8")) if sep else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            meta = None
        intact = bool(meta) and meta.get("schema") == CACHE_SCHEMA and \
            hashlib.sha256(body).hexdigest() == meta.get("body_sha256")
        if not intact:
            self.warnings.append(f"cache entry {path.name} is incomplete or corrupt; fetching again (entry kept)")
            return None
        return Response(status=meta["status"], headers=meta.get("headers", {}), body=body, url=meta.get("url", ""),
                        method=meta.get("method", "GET"), from_cache=True, attempts=0,
                        fetched_utc=meta.get("stored_utc", ""), cache_key=key, params=meta.get("params", {}))

    def _cache_write(self, response: Response):
        path = self._cache_path(response.cache_key)
        if path is None:
            return
        meta = {"schema": CACHE_SCHEMA, "key": response.cache_key, "method": response.method, "url": response.url,
                "params": response.params, "status": response.status,
                "headers": {k: v for k, v in response.headers.items() if k in ("content-type", "last-modified")},
                "body_sha256": response.sha256, "body_bytes": len(response.body), "stored_utc": response.fetched_utc}
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as handle:   # write-once: an existing entry is never replaced
                handle.write(json.dumps(meta, sort_keys=True).encode("utf-8") + b"\n" + response.body)
        except FileExistsError:
            pass

    def _throttle(self, host: str):
        last = self.last_request.get(host)
        if last is not None:
            wait = self.min_interval(host) - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self.last_request[host] = self.clock()

    def _backoff(self, attempt: int) -> float:
        return min(self.max_wait, 2.0 ** (attempt - 1) + self.rng() * 0.5)

    def _send_headers(self, identify: bool, has_body: bool, extra: dict) -> dict:
        """Request headers; the contact address is sent only when identify is true."""
        send = {"User-Agent": self.user_agent if identify else USER_AGENT, "Accept-Encoding": "identity"}
        if has_body:
            send["Content-Type"] = "application/x-www-form-urlencoded"
        send.update(extra)
        return send

    def _redirect_target(self, method: str, shown_url: str, status: int, location, current: str,
                         origin: str, redirects: int, attempt: int) -> tuple[str, str]:
        """Resolve and check a redirect; returns (url, host) or raises FetchError.

        Only https URLs on an allowed host are followed. A target on a host other than the one the
        request was made for loses the secret parameters (email, api_key) from its query, and is
        refused if the contact address or API key still appears anywhere in it.
        """
        where = f"{method} {shown_url} returned HTTP {status}"
        if not location:
            raise FetchError(f"{where} without a Location header", url=shown_url, status=status, attempts=attempt)
        if redirects > MAX_REDIRECTS:
            raise FetchError(f"{where}: more than {MAX_REDIRECTS} redirects; not followed", url=shown_url,
                             status=status, attempts=attempt)
        target = urllib.parse.urljoin(current, str(location).strip())
        parts = urllib.parse.urlsplit(target)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in self.allowed_hosts:
            raise FetchError(f"{where}: redirect to {self._redact(target)} refused (only https URLs on "
                             f"{', '.join(sorted(self.allowed_hosts))} are followed)", url=shown_url,
                             status=status, attempts=attempt)
        if host != origin:
            query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                     if k not in SECRET_PARAMS]
            target = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query), fragment=""))
            if self._redact(target) != target:
                raise FetchError(f"{where}: redirect to another host ({host}) would carry the contact address "
                                 f"or API key; refused", url=shown_url, status=status, attempts=attempt)
        return target, host

    # -- requests ---------------------------------------------------------------------
    def request(self, method: str, url: str, *, params: dict | None = None, data: dict | None = None,
                headers: dict | None = None, use_cache: bool = True) -> Response:
        method = method.upper()
        origin = self._check_url(url)
        params = dict(params or {})
        data = dict(data) if data is not None else None
        key = self.cache_key(method, url, params, data)
        if use_cache:
            cached = self._cache_read(key)
            if cached is not None:
                return cached
        target, host = url + ("?" + _encode(params) if params else ""), origin
        body = _encode(data).encode("ascii") if data is not None else None
        extra_headers = dict(headers or {})
        shown_params = _redact_params({**params, **(data or {})})
        attempt = redirects = 0
        while True:
            shown_url = self._redact(target)
            send_headers = self._send_headers(host == origin, body is not None, extra_headers)
            attempt += 1
            self._throttle(host)
            self.network_requests += 1
            try:
                status, reply_headers, reply = self.transport(method, target, send_headers, body, self.timeout)
            except (TimeoutError, socket.timeout, OSError) as error:
                if attempt > self.max_retries:
                    raise FetchError(f"{method} {shown_url} failed after {attempt} attempt(s): "
                                     f"{self._redact(str(error)) or type(error).__name__}",
                                     url=shown_url, attempts=attempt) from error
                self.sleep(self._backoff(attempt))
                continue
            reply_headers = {str(k).lower(): v for k, v in (reply_headers or {}).items()}
            if 200 <= status < 300:
                response = Response(status=status, headers=reply_headers, body=reply or b"", url=shown_url,
                                    method=method, attempts=attempt, fetched_utc=utc_now(), cache_key=key,
                                    params=shown_params)
                if use_cache and status == 200:
                    self._cache_write(response)
                return response
            if status in REDIRECT_STATUSES:
                redirects += 1
                target, host = self._redirect_target(method, shown_url, status, reply_headers.get("location"),
                                                     target, origin, redirects, attempt)
                if (status == 303 and method != "HEAD") or (status in (301, 302) and method == "POST"):
                    method, body = "GET", None         # as browsers and urllib do
                elif body is not None and host != origin:
                    body = _encode({k: v for k, v in data.items() if k not in SECRET_PARAMS}).encode("ascii")
                attempt = 0
                continue
            if status in RETRY_STATUSES and attempt <= self.max_retries:
                wait = retry_after_seconds(reply_headers.get("retry-after"))
                self.sleep(min(self.max_wait, wait if wait is not None else self._backoff(attempt)))
                continue
            snippet = self._redact((reply or b"")[:200].decode("utf-8", errors="replace")).strip()
            raise FetchError(f"{method} {shown_url} returned HTTP {status} after {attempt} attempt(s)"
                             + (f": {snippet}" if snippet else ""), url=shown_url, status=status, attempts=attempt)

    def get(self, url: str, params: dict | None = None, **options) -> Response:
        return self.request("GET", url, params=params, **options)

    def post(self, url: str, data: dict, **options) -> Response:
        return self.request("POST", url, data=data, **options)
