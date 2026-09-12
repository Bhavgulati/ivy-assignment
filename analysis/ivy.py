"""
Ivy Homes assignment - discovery harness, single file.

    python3 ivy.py discover --write     # probe the API, write out/discovery.json
    python3 ivy.py fetch --details 25   # pull the full dataset into out/*.jsonl
    python3 ivy.py report               # distributions over the snapshot
    python3 ivy.py all --write --details 25

Standard library only. Reads credentials from .env in the working directory:

    IVY_BASE_URL=https://solve.ivy.homes
    IVY_API_KEY=...
    IVY_PASSWORD=...
    IVY_LOCALITY=guindy

This is four modules concatenated into one file so there is exactly one thing to
place on disk. Section order: transport, discovery, fetch, report.
"""
from __future__ import annotations

import sys

import base64
import json
import math
import os
import re
import statistics as st
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env reader. Real env vars win over the file."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


C = sys.modules[__name__]  # so the sections below can keep using C.get(...)

_load_dotenv()

BASE_URL = os.environ.get("IVY_BASE_URL", "https://solve.ivy.homes").rstrip("/")
API_KEY = os.environ.get("IVY_API_KEY", "").strip()
PASSWORD = os.environ.get("IVY_PASSWORD", "")

OUT = Path(os.environ.get("IVY_OUT", "out"))
OUT.mkdir(parents=True, exist_ok=True)
REQUEST_LOG = OUT / "requests.jsonl"

MIN_INTERVAL_S = float(os.environ.get("IVY_MIN_INTERVAL", "0.10"))  # ~600/min
MAX_ATTEMPTS = 3

_last_request_at = 0.0
_request_count = 0


def request_count() -> int:
    return _request_count


# --------------------------------------------------------------------------
# response
# --------------------------------------------------------------------------

@dataclass
class Response:
    status: int
    url: str
    headers: dict
    text: str
    json: Any
    elapsed_ms: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def detail(self) -> str | None:
        """The API documents error bodies as {"detail": ...} and says to read
        them. This pulls it out so callers actually do."""
        if isinstance(self.json, dict):
            d = self.json.get("detail")
            if d is not None:
                return d if isinstance(d, str) else json.dumps(d)[:500]
        return None

    def short(self, n: int = 220) -> str:
        body = self.detail or self.text
        body = " ".join(body.split())
        return f"{self.status} {body[:n]}"


def _redact(s: str) -> str:
    if API_KEY:
        s = s.replace(API_KEY, "$IVY_API_KEY")
    if PASSWORD:
        s = s.replace(PASSWORD, "$IVY_PASSWORD")
    return s


def request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    body: Any = None,
    token: str | None = None,
    api_key: bool = True,
    api_key_mode: str = "header",  # "query" | "header" | "both"
    headers: dict | None = None,
    timeout: int = 25,
    tag: str = "",
) -> Response:
    global _last_request_at, _request_count

    params = dict(params or {})
    hdrs = {
        "Accept": "application/json",
        "User-Agent": "ivy-assignment-harness/1.0",
    }

    if api_key and API_KEY:
        if api_key_mode in ("query", "both"):
            params.setdefault("api_key", API_KEY)
        if api_key_mode in ("header", "both"):
            hdrs["X-API-Key"] = API_KEY
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if headers:
        hdrs.update(headers)

    clean = {k: v for k, v in params.items() if v is not None}
    qs = urllib.parse.urlencode(clean, doseq=True)
    url = f"{BASE_URL}{path}" + (f"?{qs}" if qs else "")

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    last: Response | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)

        started = time.monotonic()
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8", "replace")
                rhdrs = dict(resp.headers.items())
                err = None
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read().decode("utf-8", "replace")
            rhdrs = dict(e.headers.items()) if e.headers else {}
            err = None
        except Exception as e:  # network/DNS/TLS/timeout
            status = 0
            raw = ""
            rhdrs = {}
            err = f"{type(e).__name__}: {e}"

        _last_request_at = time.monotonic()
        _request_count += 1
        elapsed = int((time.monotonic() - started) * 1000)

        try:
            parsed = json.loads(raw) if raw else None
        except Exception:
            parsed = None

        r = Response(status, url, rhdrs, raw, parsed, elapsed, err)
        _log(method, r, tag, attempt)
        last = r

        # Retry only on things that are worth retrying.
        if status == 429:
            time.sleep(min(2.0 * attempt, 6.0))
            continue
        if status == 0 or 500 <= status < 600:
            time.sleep(0.5 * attempt)
            continue
        return r

    return last  # type: ignore[return-value]


def _log(method: str, r: Response, tag: str, attempt: int) -> None:
    rec = {
        "t": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n": _request_count,
        "tag": tag,
        "attempt": attempt,
        "method": method,
        "url": _redact(r.url),
        "status": r.status,
        "ms": r.elapsed_ms,
        "error": r.error,
        "body_head": _redact(" ".join(r.text.split()))[:2000],
    }
    with REQUEST_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def get(path: str, **kw) -> Response:
    return request("GET", path, **kw)


def post(path: str, **kw) -> Response:
    return request("POST", path, **kw)


def delete(path: str, **kw) -> Response:
    return request("DELETE", path, **kw)


# --------------------------------------------------------------------------
# small helpers used by discover.py / fetch.py
# --------------------------------------------------------------------------

def login(email: str, password: str) -> Response:
    return post("/auth/login", body={"email": email, "password": password},
                tag=f"login:{email}")


def decode_jwt_payload(token: str) -> dict | None:
    """Read a JWT's payload without verifying it.

    This is introspection of a token the server just handed us so that we can
    find its real TTL. It is not a security control and does not pretend to be
    one; nothing in this project trusts the decoded output.
    """
    for seg in token.split("."):
        try:
            padded = seg + "=" * (-len(seg) % 4)
            obj = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except Exception:
            continue
        if isinstance(obj, dict) and ("exp" in obj or "sub" in obj):
            return obj
    return None


def find_results_key(obj: Any) -> str | None:
    """Which key holds the records? The reference claims `results`, but that is
    exactly the kind of thing this reference gets wrong, so ask the payload."""
    if isinstance(obj, list):
        return None  # bare array, no envelope
    if not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return k
    for k, v in obj.items():  # empty list still counts
        if isinstance(v, list):
            return k
    return None


def records_of(obj: Any) -> list:
    if isinstance(obj, list):
        return obj
    k = find_results_key(obj)
    return obj.get(k, []) if (k and isinstance(obj, dict)) else []


def envelope_meta(obj: Any) -> dict:
    """Everything in the envelope that is not the records list."""
    if not isinstance(obj, dict):
        return {"_shape": type(obj).__name__}
    rk = find_results_key(obj)
    return {k: v for k, v in obj.items() if k != rk and not isinstance(v, (list, dict))}


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")


def preflight() -> None:
    missing = []
    if not API_KEY:
        missing.append("IVY_API_KEY")
    if not PASSWORD:
        missing.append("IVY_PASSWORD")
    if missing:
        raise SystemExit(
            "Missing " + ", ".join(missing) + ".\n"
            "Copy .env.example to .env and fill it in, or export them:\n"
            "  export IVY_API_KEY=IVY26-XXXXXXXXXXXX\n"
            "  export IVY_PASSWORD=...\n"
        )


# ==========================================================================
# DISCOVERY
# ==========================================================================

RESULT: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "base_url": C.BASE_URL}

DEMO_USERS = ["demo1@ivy.homes", "demo2@ivy.homes", "demo3@ivy.homes"]
COLLECTIONS = ["/v1/listings", "/v1/rentals", "/v1/projects"]


def hdr(n, title: str) -> None:
    print(f"\n{'=' * 72}\n{n}. {title}\n{'=' * 72}")


# --------------------------------------------------------------------------
# 1. health
# --------------------------------------------------------------------------

def step_health() -> None:
    hdr(1, "HEALTH + SERVICE ROOT")
    r = C.get("/health", api_key=False, tag="health")
    RESULT["health"] = {"status": r.status, "json": r.json, "text_head": r.text[:400],
                        "date_header": r.headers.get("Date"),
                        "server_header": r.headers.get("Server")}
    print(f"  GET /health -> {r.short(300)}")
    print(f"  Server header: {r.headers.get('Server')}")

    # `GET /` is not in the reference at all, and its body points at /llms.txt,
    # which is also not in the reference. Both are unauthenticated. Save them
    # verbatim: a service that publishes a machine-readable description of
    # itself is describing the contract that actually shipped.
    root = C.get("/", api_key=False, tag="root")
    RESULT["root"] = {"status": root.status, "json": root.json,
                      "text_head": root.text[:1000]}
    print(f"  GET /      -> {root.short(300)}")

    llms = C.get("/llms.txt", api_key=False, tag="llms.txt")
    RESULT["llms_txt"] = {"status": llms.status, "bytes": len(llms.text)}
    if llms.ok and llms.text.strip():
        (C.OUT / "llms.txt").write_text(llms.text, encoding="utf-8")
        print(f"  GET /llms.txt -> {llms.status}, {len(llms.text)} bytes "
              f"saved to out/llms.txt")
        print("  >> READ out/llms.txt BEFORE DOING ANYTHING ELSE.")
    else:
        print(f"  GET /llms.txt -> {llms.short(200)}")


# --------------------------------------------------------------------------
# 2. password resolution + login
# --------------------------------------------------------------------------


def step_agent_files() -> None:
    """Pull the large agent-facing files to disk and summarise them.

    /llms-full.txt advertises itself as roughly a hundred thousand tokens. That
    is a context-flooding hazard, not a document: it goes on disk and gets
    grepped. /sitemap.xml claims to list "several [properties] that never were",
    which is worth cross-referencing against the listing ids we actually fetch.
    Neither file is treated as authoritative - both came from the same
    unreviewed generator as the reference.
    """
    hdr("1b", "AGENT-FACING FILES")
    summary = {}
    for path, fname in [("/llms-full.txt", "llms-full.txt"),
                        ("/sitemap.xml", "sitemap.xml"),
                        ("/humans.txt", "humans.txt"),
                        ("/robots.txt", "robots.txt")]:
        r = C.get(path, api_key=False, tag=f"agentfile:{path}")
        if not r.ok:
            r = C.get(path, tag=f"agentfile:{path}:keyed")
        if r.ok and r.text.strip():
            (C.OUT / fname).write_text(r.text, encoding="utf-8")
            heads = [l.strip() for l in r.text.splitlines()
                     if l.startswith("#")][:60]
            locs = r.text.count("<loc>")
            summary[path] = {"status": r.status, "bytes": len(r.text),
                             "headings": heads, "loc_entries": locs}
            print(f"  {path:18s} {r.status}  {len(r.text):>8,d} bytes -> out/{fname}"
                  + (f"  ({locs:,d} <loc> entries)" if locs else ""))
            for h in heads[:25]:
                print(f"       {h[:100]}")
        else:
            summary[path] = {"status": r.status, "detail": r.detail}
            print(f"  {path:18s} {r.short(120)}")
    RESULT["agent_files"] = summary


def step_login() -> str | None:
    hdr(2, "PASSWORD RESOLUTION + LOGIN")

    # The email presented the password followed by " ." which is probably
    # sentence punctuation. Try the obvious readings of one credential we were
    # given, in order, and stop at the first success. Three attempts on our own
    # demo account is disambiguation, not credential stuffing.
    raw = C.PASSWORD
    candidates, seen = [], set()
    for cand in (raw, raw.strip(), raw.strip().rstrip(".").strip(), raw.strip() + " ."):
        if cand and cand not in seen:
            seen.add(cand)
            candidates.append(cand)

    working, attempts = None, []
    for cand in candidates:
        r = C.login(DEMO_USERS[0], cand)
        attempts.append({"length": len(cand), "status": r.status,
                         "detail": r.detail, "ok": r.ok})
        print(f"  password len={len(cand)} -> {r.short(120)}")
        if r.ok:
            working = cand
            RESULT["login_sample_response"] = r.json
            break

    RESULT["password_attempts"] = attempts
    RESULT["password_resolved"] = working is not None
    if working is None:
        print("  !! No password variant worked. Stopping; everything below needs a token.")
        return None

    C.PASSWORD = working  # so the log redaction covers the real value
    RESULT["password_trailing_dot_in_email"] = (working != raw.strip())

    tokens = {}
    for user in DEMO_USERS:
        r = C.login(user, working)
        if r.ok and isinstance(r.json, dict):
            tokens[user] = r.json.get("token") or r.json.get("access_token")
            print(f"  {user} -> 200, response keys: {sorted(r.json.keys())}")
        else:
            print(f"  {user} -> {r.short(120)}")
    RESULT["tokens_obtained"] = {u: bool(t) for u, t in tokens.items()}
    RESULT["_tokens"] = tokens
    return tokens.get(DEMO_USERS[0])


# --------------------------------------------------------------------------
# 3. token TTL
# --------------------------------------------------------------------------

def step_token_ttl(token: str) -> None:
    hdr(3, "TOKEN TTL")
    resp = RESULT.get("login_sample_response") or {}
    documented = 86400
    claimed = resp.get("expires_in")
    payload = C.decode_jwt_payload(token)
    real = None
    if payload and "exp" in payload:
        iat = payload.get("iat") or int(time.time())
        real = int(payload["exp"]) - int(iat)
    RESULT["token"] = {
        "login_response_keys": sorted(resp.keys()) if isinstance(resp, dict) else None,
        "expires_in_claimed_by_login": claimed,
        "jwt_payload": payload,
        "ttl_seconds_from_jwt": real,
        "documented_ttl_seconds": documented,
    }
    print(f"  login body expires_in : {claimed}")
    print(f"  JWT exp - iat         : {real} s"
          + (f"  ({real / 60:.1f} min)" if real else ""))
    print(f"  documentation claims  : {documented} s (24 h)")
    if real and real < 3600:
        print("  >> TTL is under an hour. The frontend needs a re-auth strategy,")
        print("     and the 30-minute requirement in the statement now makes sense.")


# --------------------------------------------------------------------------
# 4. auth matrix
# --------------------------------------------------------------------------

def step_auth_matrix(token: str) -> None:
    hdr(4, "AUTH MATRIX  (what is actually required)")
    probe = {"limit": 1}
    cases = [
        ("key in query + bearer", dict(params=probe, token=token, api_key=True, api_key_mode="query")),
        ("key in query, no bearer", dict(params=probe, api_key=True, api_key_mode="query")),
        ("no key, bearer only", dict(params=probe, token=token, api_key=False)),
        ("key in X-API-Key header + bearer", dict(params=probe, token=token, api_key=True, api_key_mode="header")),
        ("key in X-API-Key header, no bearer", dict(params=probe, api_key=True, api_key_mode="header")),
        ("neither", dict(params=probe, api_key=False)),
        ("bad key in query", dict(params={**probe, "api_key": "IVY26-000000000000"}, token=token, api_key=False)),
        ("bad bearer", dict(params=probe, token="not.a.real.token", api_key=True)),
    ]
    table = []
    for name, kw in cases:
        r = C.get("/v1/listings", tag=f"auth:{name}", **kw)
        table.append({"case": name, "status": r.status, "detail": r.detail})
        print(f"  {name:38s} -> {r.short(110)}")
    RESULT["auth_matrix"] = table


# --------------------------------------------------------------------------
# 5. envelope shape
# --------------------------------------------------------------------------

def step_envelopes(token: str) -> dict:
    hdr(5, "ENVELOPE SHAPE + FIELD SETS")
    shapes = {}
    for path in COLLECTIONS:
        r = C.get(path, params={"limit": 3}, token=token, tag=f"shape:{path}")
        if not r.ok:
            print(f"  {path:16s} -> {r.short(140)}")
            shapes[path] = {"status": r.status, "detail": r.detail}
            continue
        recs = C.records_of(r.json)
        info = {
            "status": r.status,
            "top_level_keys": sorted(r.json.keys()) if isinstance(r.json, dict) else "ARRAY",
            "results_key": C.find_results_key(r.json),
            "envelope": C.envelope_meta(r.json),
            "record_count_returned": len(recs),
            "record_fields": sorted(recs[0].keys()) if recs else [],
            "samples": recs[:3],
        }
        shapes[path] = info
        print(f"  {path}")
        print(f"     envelope keys : {info['top_level_keys']}")
        print(f"     envelope meta : {info['envelope']}")
        print(f"     results key   : {info['results_key']!r}")
        print(f"     record fields : {info['record_fields']}")
    RESULT["envelopes"] = shapes
    return shapes


def _ids(obj, id_key: str = "listing_id") -> list:
    out = []
    for rec in C.records_of(obj):
        out.append(rec.get(id_key) or rec.get("id") or rec.get("project_id"))
    return out


# --------------------------------------------------------------------------
# 6. pagination semantics
# --------------------------------------------------------------------------

def step_pagination(token: str) -> dict:
    hdr(6, "PAGINATION SEMANTICS")
    path = "/v1/listings"

    base = C.get(path, params={"limit": 5}, token=token, tag="pg:base")
    base_ids = _ids(base.json)
    page2 = C.get(path, params={"limit": 5, "page": 2}, token=token, tag="pg:page2")
    off5 = C.get(path, params={"limit": 5, "offset": 5}, token=token, tag="pg:offset5")
    skip5 = C.get(path, params={"limit": 5, "skip": 5}, token=token, tag="pg:skip5")

    page_moves = _ids(page2.json) != base_ids and bool(_ids(page2.json))
    offset_moves = _ids(off5.json) != base_ids and bool(_ids(off5.json))
    skip_moves = _ids(skip5.json) != base_ids and bool(_ids(skip5.json))

    print(f"  baseline limit=5           ids: {base_ids}")
    print(f"  limit=5&page=2   moves it? {page_moves}   ids: {_ids(page2.json)}")
    print(f"  limit=5&offset=5 moves it? {offset_moves}   ids: {_ids(off5.json)}")
    print(f"  limit=5&skip=5   moves it? {skip_moves}   ids: {_ids(skip5.json)}")

    # Which parameter actually controls page size? The reference says `limit`,
    # but `page_size` appears in its own example envelope, so do not assume.
    # Ask for an odd number nobody would default to and see who obeys.
    alias = {}
    for name in ["limit", "page_size", "per_page", "size", "count"]:
        r = C.get(path, params={name: 7}, token=token, tag=f"pg:alias:{name}")
        got = len(C.records_of(r.json)) if r.ok else None
        alias[name] = {"status": r.status, "returned": got, "honoured": got == 7}
        print(f"  page-size param {name:10s} -> returned {got}")
    limit_param = next((k for k, v in alias.items() if v["honoured"]), "limit")
    print(f"  >> page size is controlled by: {limit_param!r}")

    # limit ceiling: ask for absurd values and read back what the server says
    # it used. The envelope reports the truth even when the request lied.
    ceiling = []
    for v in [20, 100, 200, 201, 500, 1000, 0, -1, "abc"]:
        r = C.get(path, params={limit_param: v}, token=token, tag=f"pg:limit={v}")
        ceiling.append({
            "requested": v, "status": r.status, "detail": r.detail,
            "returned": len(C.records_of(r.json)) if r.ok else None,
            "envelope": C.envelope_meta(r.json) if r.ok else None,
        })
        print(f"  limit={str(v):>5s} -> status {r.status}, returned "
              f"{len(C.records_of(r.json)) if r.ok else '-'}, "
              f"envelope {C.envelope_meta(r.json) if r.ok else r.detail}")

    # does the reported total/count react to a filter, and can it be trusted?
    env_base = C.envelope_meta(base.json)
    count_keys = [k for k, v in env_base.items() if isinstance(v, int)]

    # offset past the end: does it 400, empty out, or wrap around?
    far = C.get(path, params={"limit": 5, "offset": 10_000_000}, token=token, tag="pg:far")
    print(f"  offset=10,000,000 -> {far.short(140)}")

    pg = {
        "baseline_ids": base_ids,
        "limit_param_name": limit_param,
        "limit_param_probe": alias,
        "page_param_moves_window": page_moves,
        "offset_param_moves_window": offset_moves,
        "skip_param_moves_window": skip_moves,
        "page2_equals_page1": _ids(page2.json) == base_ids,
        "offset5_equals_page2": _ids(off5.json) == _ids(page2.json),
        "limit_probe": ceiling,
        "envelope_integer_keys": count_keys,
        "envelope_baseline": env_base,
        "far_offset": {"status": far.status, "detail": far.detail,
                       "returned": len(C.records_of(far.json)) if far.ok else None},
    }
    RESULT["pagination"] = pg

    style = "offset" if offset_moves else ("page" if page_moves else "UNKNOWN")
    print(f"\n  >> pagination style looks like: {style}")
    if page_moves and offset_moves:
        print("  >> both move the window. Check whether they agree before trusting either.")
    if not page_moves and offset_moves:
        print("  >> `page` is accepted and ignored. That is a pagination finding,")
        print("     and paging with `page` would silently re-read page 1 forever.")
    return pg


# --------------------------------------------------------------------------
# 7. path matrix
# --------------------------------------------------------------------------

def step_paths(token: str, shapes: dict) -> None:
    hdr(7, "PATH MATRIX")

    lid = pid = rid = None
    ls = shapes.get("/v1/listings", {})
    if ls.get("samples"):
        lid = ls["samples"][0].get("listing_id")
        pid = ls["samples"][0].get("project_id")
    ps = shapes.get("/v1/projects", {})
    if ps.get("samples"):
        pid = ps["samples"][0].get("project_id") or pid
    rs = shapes.get("/v1/rentals", {})
    if rs.get("samples"):
        rid = rs["samples"][0].get("listing_id")

    candidates = [
        # documented
        ("/v1/listings", None),
        (f"/v1/listing/{lid}", "documented singular path"),
        (f"/v1/listings/{lid}", "plural variant"),
        (f"/v1/listings/{lid}/similar", "documented"),
        ("/v1/rentals", None),
        (f"/v1/rentals/{rid}", "documented"),
        (f"/v1/rental/{rid}", "singular variant"),
        ("/v1/projects", None),
        (f"/v1/projects/{pid}", "documented"),
        ("/v1/favourites", "documented spelling"),
        ("/v1/favorites", "us spelling variant"),
        ("/v1/analytics/summary", "documented"),
        # plausible undocumented
        ("/v1/analytics", None), ("/v1/stats", None), ("/v1/summary", None),
        ("/v1/localities", None), ("/v1/cities", None), ("/v1/meta", None),
        ("/v1/enquiries", None), ("/v1/users/me", None),
        ("/auth/me", None), ("/auth/refresh", None),
        # a service that returns {"detail": ...} and 422 is very likely FastAPI,
        # in which case it may be publishing its own real schema
        ("/openapi.json", "would hand over the real contract"),
        ("/v1/openapi.json", None), ("/docs", None), ("/redoc", None),
        ("/", "not in the reference at all"),
        ("/llms.txt", "not in the reference; advertised by GET /"),
        ("/register", "advertised by GET /"), ("/v1", None),
        # advertised by /llms.txt, which is not in the reference either
        ("/v2/listings", "llms.txt: faster, de-duplicated"),
        ("/v2/listings/search", "llms.txt: natural-language search"),
        ("/v2/insights/summary", "llms.txt: the analytics summary"),
        (f"/v2/valuation/{lid}", "llms.txt: valuation"),
        ("/v2", None), ("/v2/rentals", None), ("/v2/projects", None),
        ("/humans.txt", None), ("/robots.txt", None), ("/ads.txt", None),
        ("/.well-known/ai-plugin.json", None),
    ]

    rows = []
    for path, note in candidates:
        if "None" in path or path.endswith("/None"):
            continue
        r = C.get(path, token=token, tag=f"path:{path}")
        keys = sorted(r.json.keys())[:14] if isinstance(r.json, dict) else (
            f"ARRAY[{len(r.json)}]" if isinstance(r.json, list) else None)
        rows.append({"path": path, "note": note, "status": r.status,
                     "detail": r.detail, "keys": keys,
                     "text_head": r.text[:200] if not r.json else None})
        flag = "OK " if r.ok else "   "
        print(f"  {flag}{path:44s} {r.status}  {r.detail or (keys if keys else '')}")
    RESULT["path_matrix"] = rows

    hits = [r["path"] for r in rows if r["status"] == 200]
    misses = [r["path"] for r in rows if r["status"] == 404]
    print(f"\n  200: {len(hits)}   404: {len(misses)}")
    if "/openapi.json" in hits:
        print("  >> /openapi.json is live. Save it. That is the real contract, and")
        print("     it is also an undocumented_endpoint finding in its own right.")


# --------------------------------------------------------------------------
# 8. filters and sorting
# --------------------------------------------------------------------------

def step_filters(token: str, pg: dict) -> None:
    hdr(8, "FILTERS AND SORTING  (accepted vs actually applied)")

    # A filter that is ignored returns exactly the unfiltered window. That is
    # the cleanest signal available, so everything is compared against one
    # fixed baseline window.
    n = 50
    base = C.get("/v1/listings", params={"limit": n}, token=token, tag="flt:baseline")
    recs = C.records_of(base.json)
    base_ids = [r.get("listing_id") for r in recs]
    if not recs:
        print("  no baseline records; skipping")
        return

    def pick(field):
        vals = [r.get(field) for r in recs if r.get(field) not in (None, "")]
        return vals[0] if vals else None

    prices = sorted(r["price"] for r in recs if isinstance(r.get("price"), (int, float)))
    mid = prices[len(prices) // 2] if prices else None

    tests = [
        ("locality", pick("locality"), lambda r, v: str(r.get("locality", "")).strip().lower() == str(v).strip().lower()),
        ("bhk", pick("bedroom"), lambda r, v: r.get("bedroom") == v),
        ("bedroom", pick("bedroom"), lambda r, v: r.get("bedroom") == v),
        ("property_type", pick("property_type"), lambda r, v: r.get("property_type") == v),
        ("furnishing", pick("furnishing"), lambda r, v: r.get("furnishing") == v),
        ("min_price", mid, lambda r, v: (r.get("price") or 0) >= v),
        ("max_price", mid, lambda r, v: (r.get("price") or 0) <= v),
        ("project_id", pick("project_id"), lambda r, v: r.get("project_id") == v),
        ("is_live", "true", None),
        ("is_verified", "true", None),
        ("website", pick("website"), lambda r, v: r.get("website") == v),
        ("posted_by", pick("posted_by"), lambda r, v: r.get("posted_by") == v),
        ("min_area", 500, None),
        ("max_area", 5000, None),
        ("q", pick("locality"), None),
        ("search", pick("locality"), None),
        ("city", "mumbai", None),
        ("city_id", 99, None),
    ]

    rows = []
    for param, value, predicate in tests:
        if value is None:
            continue
        r = C.get("/v1/listings", params={"limit": n, param: value},
                  token=token, tag=f"flt:{param}")
        got = C.records_of(r.json)
        ids = [x.get("listing_id") for x in got]
        satisfied = None
        if predicate and got:
            satisfied = round(sum(1 for x in got if predicate(x, value)) / len(got), 3)
        same_as_baseline = ids == base_ids
        verdict = ("ERROR" if not r.ok else
                   "IGNORED (identical window)" if same_as_baseline else
                   "APPLIED" if (satisfied == 1.0 or satisfied is None) else
                   f"PARTIAL ({satisfied:.0%} match)")
        rows.append({"param": param, "value": value, "status": r.status,
                     "detail": r.detail, "returned": len(got),
                     "identical_to_baseline": same_as_baseline,
                     "predicate_satisfied_fraction": satisfied,
                     "envelope": C.envelope_meta(r.json) if r.ok else None,
                     "verdict": verdict})
        print(f"  {param:14s}={str(value)[:22]:24s} {r.status}  n={len(got):3d}  {verdict}")

    # sorting: check monotonicity of the field it claims to sort on
    for field in ["price", "carpet_area", "posted_at", "bedroom", "nonsense_field"]:
        for order in ["asc", "desc"]:
            r = C.get("/v1/listings",
                      params={"limit": n, "sort_by": field, "order": order},
                      token=token, tag=f"sort:{field}:{order}")
            got = C.records_of(r.json)
            vals = [x.get(field) for x in got if x.get(field) is not None]
            mono = None
            if len(vals) > 2 and all(isinstance(v, type(vals[0])) for v in vals):
                mono = (all(a <= b for a, b in zip(vals, vals[1:])) if order == "asc"
                        else all(a >= b for a, b in zip(vals, vals[1:])))
            ids = [x.get("listing_id") for x in got]
            rows.append({"param": f"sort_by={field}&order={order}", "status": r.status,
                         "detail": r.detail, "returned": len(got),
                         "identical_to_baseline": ids == base_ids,
                         "monotonic": mono,
                         "verdict": ("ERROR" if not r.ok else
                                     "IGNORED (identical window)" if ids == base_ids else
                                     "SORTED" if mono else "NOT SORTED")})
            print(f"  sort_by={field:16s} {order:4s} {r.status}  "
                  f"monotonic={mono}  {'identical to baseline' if ids == base_ids else ''}")

    RESULT["filters_and_sorting"] = rows
    RESULT["filter_baseline_ids"] = base_ids


# --------------------------------------------------------------------------
# 9. favourites  (the only step that writes)
# --------------------------------------------------------------------------

def step_favourites(shapes: dict) -> None:
    hdr(9, "FAVOURITES  (write path, body key, per-user isolation)")
    tokens = RESULT.get("_tokens", {})
    t1, t2 = tokens.get(DEMO_USERS[0]), tokens.get(DEMO_USERS[1])
    if not (t1 and t2):
        print("  need two tokens; skipping")
        return
    samples = shapes.get("/v1/listings", {}).get("samples") or []
    if not samples:
        print("  no sample listing; skipping")
        return
    lid = samples[0]["listing_id"]

    out: dict = {"listing_used": lid, "path_used": None}

    before = C.get("/v1/favourites", token=t1, tag="fav:get:before")
    out["get_before"] = {"status": before.status, "envelope": C.envelope_meta(before.json),
                         "count": len(C.records_of(before.json))}
    print(f"  GET  favourites (demo1) -> {before.short(140)}")

    # The reference shows {"id": ...}. If that 422s, read the validation error,
    # which the reference says is written to be useful, and try what it asks for.
    for body in ({"id": lid}, {"listing_id": lid}, {"listing_ids": [lid]}):
        r = C.post("/v1/favourites", body=body, token=t1, tag="fav:post")
        print(f"  POST {body} -> {r.short(200)}")
        out.setdefault("post_attempts", []).append(
            {"body": body, "status": r.status, "detail": r.detail, "json": r.json})
        if r.ok:
            out["working_body_key"] = list(body.keys())[0]
            break

    after1 = C.get("/v1/favourites", token=t1, tag="fav:get:demo1")
    after2 = C.get("/v1/favourites", token=t2, tag="fav:get:demo2")
    ids1 = [r.get("listing_id") or r.get("id") for r in C.records_of(after1.json)]
    ids2 = [r.get("listing_id") or r.get("id") for r in C.records_of(after2.json)]
    out["demo1_after"] = ids1
    out["demo2_after"] = ids2
    out["isolated_per_user"] = (lid in ids1) and (lid not in ids2)
    print(f"  demo1 now has: {ids1}")
    print(f"  demo2 now has: {ids2}")
    print(f"  >> per-user isolation holds: {out['isolated_per_user']}")

    # idempotency: saving twice, using whichever body key actually worked
    key = out.get("working_body_key", "id")
    dup = C.post("/v1/favourites", body={key: lid}, token=t1, tag="fav:post:dup")
    out["duplicate_save"] = {"status": dup.status, "detail": dup.detail}
    print(f"  saving the same listing twice -> {dup.short(140)}")

    d = C.delete(f"/v1/favourites/{lid}", token=t1, tag="fav:delete")
    print(f"  DELETE -> {d.short(140)}")
    out["delete"] = {"status": d.status, "detail": d.detail}
    d2 = C.delete(f"/v1/favourites/{lid}", token=t1, tag="fav:delete:again")
    out["delete_again"] = {"status": d2.status, "detail": d2.detail}
    print(f"  DELETE again (idempotent?) -> {d2.short(140)}")

    RESULT["favourites"] = out


# --------------------------------------------------------------------------
# 10. auth extras
# --------------------------------------------------------------------------

def step_auth_extras(token: str) -> None:
    hdr(10, "AUTH EXTRAS")
    rows = []
    for method, path in [("GET", "/auth/me"), ("POST", "/auth/refresh"),
                         ("GET", "/auth/refresh"), ("POST", "/auth/logout")]:
        if path == "/auth/logout":
            print("  skipping /auth/logout: it would invalidate the token this run needs")
            rows.append({"path": path, "status": None, "note": "not called on purpose"})
            continue
        r = C.request(method, path, token=token, body={} if method == "POST" else None,
                      tag=f"authx:{path}")
        rows.append({"method": method, "path": path, "status": r.status,
                     "detail": r.detail,
                     "keys": sorted(r.json.keys()) if isinstance(r.json, dict) else None})
        print(f"  {method:5s} {path:18s} -> {r.short(150)}")
    RESULT["auth_extras"] = rows


# --------------------------------------------------------------------------

def discover_main() -> None:
    C.preflight()
    do_write = "--write" in sys.argv

    print(f"base url : {C.BASE_URL}")
    print(f"api key  : {C.API_KEY[:6]}...{C.API_KEY[-4:]}  (never printed in full)")
    print(f"output   : {C.OUT.resolve()}")

    step_health()
    step_agent_files()
    token = step_login()
    if not token:
        C.write_json(C.OUT / "discovery.json", RESULT)
        raise SystemExit("login failed - fix credentials and re-run")

    step_token_ttl(token)
    step_auth_matrix(token)
    shapes = step_envelopes(token)
    pg = step_pagination(token)
    step_paths(token, shapes)
    step_filters(token, pg)
    if do_write:
        step_favourites(shapes)
    else:
        print("\n(skipping the favourites write probes; re-run with --write for those)")
    step_auth_extras(token)

    RESULT["requests_used"] = C.request_count()
    saved = dict(RESULT)
    saved.pop("_tokens", None)  # never persist tokens
    C.write_json(C.OUT / "discovery.json", saved)

    print(f"\n{'=' * 72}")
    print(f"requests used : {C.request_count()}")
    print(f"written       : {(C.OUT / 'discovery.json').resolve()}")
    print(f"request log   : {C.REQUEST_LOG.resolve()}")
    print("next          : python fetch.py")




# ==========================================================================
# FETCH
# ==========================================================================

HARD_PAGE_CAP = 400  # 400 pages x 200 = 80k records; far above "a few thousand"

FETCH_COLLECTIONS = {
    "listings": {"path": "/v1/listings", "id": "listing_id"},
    "rentals": {"path": "/v1/rentals", "id": "listing_id"},
    "projects": {"path": "/v1/projects", "id": "project_id"},
}



# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------
#
# The access token lives 900 seconds. A full crawl plus a verification pass is
# several hundred requests, so the token WILL expire partway through on a slow
# link. The pager used to stop on the first non-2xx, which would have truncated
# the crawl silently and produced a confident wrong answer to question 1 - the
# worst possible failure here, because every other count is derived from it.
#
# So: one place holds the session, a 401 mid-page triggers a refresh, and the
# page is retried once. The refresh path is tried first because POST
# /auth/refresh turns out to exist; full re-login is the fallback.

SESSION: dict = {"access": None, "refresh": None, "email": "demo1@ivy.homes",
                 "logins": 0, "refreshes": 0}


def session_login(force: bool = False) -> str:
    if SESSION["access"] and not force:
        return SESSION["access"]
    pw = C.PASSWORD
    r = C.login(SESSION["email"], pw)
    if not r.ok:
        for cand in (pw.strip(), pw.strip().rstrip(".").strip()):
            if cand == pw:
                continue
            r = C.login(SESSION["email"], cand)
            if r.ok:
                C.PASSWORD = cand
                break
    if not r.ok:
        raise SystemExit(f"login failed: {r.short()}")
    j = r.json if isinstance(r.json, dict) else {}
    SESSION["access"] = j.get("access_token") or j.get("token")
    SESSION["refresh"] = j.get("refresh_token")
    SESSION["logins"] += 1
    return SESSION["access"]


def session_renew() -> str:
    """Refresh if the service lets us, otherwise log in again."""
    if SESSION.get("refresh"):
        r = C.post("/auth/refresh", body={"refresh_token": SESSION["refresh"]},
                   token=SESSION.get("access"), tag="session:refresh")
        if r.ok and isinstance(r.json, dict):
            SESSION["access"] = (r.json.get("access_token") or r.json.get("token")
                                 or SESSION["access"])
            SESSION["refresh"] = r.json.get("refresh_token") or SESSION["refresh"]
            SESSION["refreshes"] += 1
            return SESSION["access"]
    return session_login(force=True)


def load_discovery() -> dict:
    p = C.OUT / "discovery.json"
    if not p.exists():
        raise SystemExit("out/discovery.json not found. Run: python discover.py")
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_paging(disc: dict) -> tuple[str, int, str]:
    """Returns (style, limit, limit_param)."""
    pg = disc.get("pagination", {})
    if pg.get("offset_param_moves_window"):
        style = "offset"
    elif pg.get("skip_param_moves_window"):
        style = "skip"
    elif pg.get("page_param_moves_window"):
        style = "page"
    else:
        raise SystemExit(
            "Neither page nor offset moved the window in discovery. Read "
            "out/discovery.json['pagination'] before going further - paging "
            "blind here would produce a wrong answer to question 1."
        )

    # Largest limit the server actually honoured, not the largest it accepted.
    best = 20
    for probe in pg.get("limit_probe", []):
        req, ret = probe.get("requested"), probe.get("returned")
        if isinstance(req, int) and isinstance(ret, int) and ret > 0:
            best = max(best, ret)
    return style, best, pg.get("limit_param_name") or "limit"


def paginate(path: str, id_key: str, style: str, limit: int, token: str,
             envelopes: list, tag: str, limit_param: str = "limit"):
    """Yield (record, provenance) preserving duplicates and order."""
    seen_windows: set[tuple] = set()
    offset, page, pages = 0, 1, 0
    stop_reason = "cap"

    while pages < HARD_PAGE_CAP:
        params = {limit_param: limit}
        if style == "offset":
            params["offset"] = offset
        elif style == "skip":
            params["skip"] = offset
        else:
            params["page"] = page

        r = C.get(path, params=params, token=SESSION["access"] or token,
                  tag=f"{tag}:p{pages}")
        if r.status == 401:
            session_renew()
            r = C.get(path, params=params, token=SESSION["access"],
                      tag=f"{tag}:p{pages}:retry")
        if not r.ok:
            stop_reason = f"http_{r.status}:{r.detail}"
            envelopes.append({"path": path, "params": params, "status": r.status,
                              "detail": r.detail})
            break

        recs = C.records_of(r.json)
        env = C.envelope_meta(r.json)
        envelopes.append({"path": path, "params": params, "status": r.status,
                          "returned": len(recs), "envelope": env})
        pages += 1

        if not recs:
            stop_reason = "empty_page"
            break

        window = tuple(str(x.get(id_key) or x.get("id")) for x in recs)
        if window in seen_windows:
            # The window stopped moving. Either the paging parameter is being
            # ignored or we have wrapped. Either way, stop: continuing would
            # inflate the record count with re-reads of the same page.
            stop_reason = "window_repeated"
            break
        seen_windows.add(window)

        for i, rec in enumerate(recs):
            yield rec, {"_page": pages - 1, "_index_in_page": i,
                        "_params": dict(params)}

        has_more = env.get("has_more")
        if has_more is False:
            stop_reason = "has_more_false"
            break
        if len(recs) < limit:
            stop_reason = "short_page"
            break

        offset += len(recs)
        page += 1

    yield None, {"_stop_reason": stop_reason, "_pages": pages}


def pull(name: str, spec: dict, style: str, limit: int, token: str,
         suffix: str = "", limit_param: str = "limit") -> dict:
    path, id_key = spec["path"], spec["id"]
    out_path = C.OUT / f"{name}{suffix}.jsonl"
    envelopes: list = []
    n = 0
    ids: list = []
    stop: dict = {}
    started = time.monotonic()

    with out_path.open("w", encoding="utf-8") as f:
        for rec, prov in paginate(path, id_key, style, limit, token, envelopes,
                                  tag=f"pull:{name}{suffix}", limit_param=limit_param):
            if rec is None:
                stop = prov
                break
            rec = dict(rec)
            rec.update(prov)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            ids.append(rec.get(id_key))
            n += 1

    meta = {
        "collection": name,
        "path": path,
        "records": n,
        "distinct_ids": len(set(ids)),
        "duplicate_id_records": n - len(set(ids)),
        "pages": stop.get("_pages"),
        "stop_reason": stop.get("_stop_reason"),
        "limit_used": limit,
        "limit_param": limit_param,
        "style_used": style,
        "seconds": round(time.monotonic() - started, 1),
        "file": str(out_path),
        "last_envelope": envelopes[-1].get("envelope") if envelopes else None,
    }
    with (C.OUT / f"envelopes{suffix}.jsonl").open("a", encoding="utf-8") as f:
        for e in envelopes:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"  {name:9s} {n:6d} records  {meta['distinct_ids']:6d} distinct ids  "
          f"{meta['pages']} pages  stop={meta['stop_reason']}  {meta['seconds']}s")
    if meta["duplicate_id_records"]:
        print(f"            !! {meta['duplicate_id_records']} records share an id "
              f"with another record. The reference says ids are globally unique.")
    return meta


def pull_details(token: str, n: int) -> dict:
    """Compare a sample of single-record responses against the list rows.

    Two endpoints describing the same record differently is a `consistency`
    finding, and it is invisible unless you actually ask both.
    """
    src = C.OUT / "listings.jsonl"
    if not src.exists():
        return {}
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()[:5000]]
    step = max(1, len(rows) // max(1, n))
    sample = rows[::step][:n]

    # discovery told us which of the two documented shapes actually resolves
    disc = load_discovery()
    live = {r["path"] for r in disc.get("path_matrix", []) if r["status"] == 200}
    template = None
    for cand in ("/v1/listings/{id}", "/v1/listing/{id}"):
        probe = cand.replace("{id}", str(sample[0]["listing_id"]))
        if probe in live:
            template = cand
            break
    if template is None:
        template = "/v1/listings/{id}"

    diffs, errors = [], []
    for row in sample:
        lid = row["listing_id"]
        r = C.get(template.replace("{id}", str(lid)),
                  token=SESSION["access"] or token, tag="detail")
        if r.status == 401:
            session_renew()
            r = C.get(template.replace("{id}", str(lid)),
                      token=SESSION["access"], tag="detail:retry")
        if not r.ok:
            errors.append({"listing_id": lid, "status": r.status, "detail": r.detail})
            continue
        d = r.json if isinstance(r.json, dict) else {}
        d = d.get("result", d) if isinstance(d.get("result"), dict) else d
        changed = {k: [row.get(k), d.get(k)] for k in set(row) | set(d)
                   if not k.startswith("_") and row.get(k) != d.get(k)}
        if changed:
            diffs.append({"listing_id": lid, "differences": changed})

    print(f"  detail cross-check: {len(sample)} sampled, {len(diffs)} differ, "
          f"{len(errors)} errored  (template {template})")
    out = {"template": template, "sampled": len(sample),
           "differing": diffs[:40], "errors": errors[:40]}
    C.write_json(C.OUT / "detail_crosscheck.json", out)
    return out



def crawl_ids_sorted(path: str, id_key: str, limit: int, limit_param: str,
                     sort_by: str = "price", order: str = "asc",
                     cap: int = 300) -> list:
    """Enumerate the collection a second way, via a sorted traversal.

    Offset paging over a non-unique sort key can skip and repeat rows: if two
    records tie on price, the server is free to order them differently between
    requests, and a row can slide across a page boundary and be missed. Paging
    twice in the same order will not reveal that. Paging in a *different* order
    will. If this traversal and the default one disagree on the id set, the
    difference is the evidence, and question 1 depends on knowing about it.
    """
    ids, offset = [], 0
    for _ in range(cap):
        params = {limit_param: limit, "offset": offset,
                  "sort_by": sort_by, "order": order}
        r = C.get(path, params=params, token=SESSION["access"], tag="sortedcrawl")
        if r.status == 401:
            session_renew()
            continue
        if not r.ok:
            break
        recs = C.records_of(r.json)
        if not recs:
            break
        ids.extend(x.get(id_key) for x in recs)
        env = C.envelope_meta(r.json)
        if env.get("has_more") is False or len(recs) < limit:
            break
        offset += len(recs)
    return ids


def fetch_main() -> None:
    C.preflight()
    disc = load_discovery()
    style, limit, limit_param = resolve_paging(disc)

    # discovery does not persist tokens, so establish a session for this run
    token = session_login()

    print(f"\npaging style : {style}")
    print(f"page size    : {limit} via {limit_param!r}")
    print(f"\npass 1")
    meta = {"style": style, "limit": limit, "limit_param": limit_param,
            "pass1": {}, "pass2": {}}
    for name, spec in FETCH_COLLECTIONS.items():
        meta["pass1"][name] = pull(name, spec, style, limit, token,
                                   limit_param=limit_param)

    # A second read at a different page size. If the two passes disagree on the
    # set of ids, the endpoint is not stable under paging and every count in the
    # submission is suspect. Cheap check, and it protects every downstream answer.
    if "--no-verify" not in sys.argv:
        alt = max(5, limit // 2)
        print(f"\npass 2  (limit={alt}, stability check)")
        for name, spec in FETCH_COLLECTIONS.items():
            meta["pass2"][name] = pull(name, spec, style, alt, token,
                                       suffix="_pass2", limit_param=limit_param)

        print("\nstability")
        for name in FETCH_COLLECTIONS:
            a = _idset(C.OUT / f"{name}.jsonl", FETCH_COLLECTIONS[name]["id"])
            b = _idset(C.OUT / f"{name}_pass2.jsonl", FETCH_COLLECTIONS[name]["id"])
            only_a, only_b = sorted(a - b)[:10], sorted(b - a)[:10]
            same = a == b
            meta.setdefault("stability", {})[name] = {
                "identical_id_sets": same, "pass1_only_sample": only_a,
                "pass2_only_sample": only_b,
                "pass1_distinct": len(a), "pass2_distinct": len(b)}
            print(f"  {name:9s} identical id sets: {same}"
                  + ("" if same else f"   p1-only={len(a - b)} p2-only={len(b - a)}"))
            if not same:
                print("            !! paging is not stable. Do not trust any count "
                      "until you know why.")

    if "--details" in sys.argv:
        i = sys.argv.index("--details")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 25
        print("\ndetail cross-check")
        meta["detail_crosscheck"] = pull_details(token, n)

    # /v1/localities is a real endpoint that the reference never mentions. It is
    # also the only authoritative list of locality spellings, which matters
    # because the locality filter is an exact match.
    rl = C.get("/v1/localities", token=SESSION["access"], tag="localities")
    if rl.status == 401:
        session_renew()
        rl = C.get("/v1/localities", token=SESSION["access"], tag="localities")
    if rl.ok:
        C.write_json(C.OUT / "localities.json", rl.json)
        meta["localities"] = {"status": rl.status,
                              "count": len(C.records_of(rl.json))}
        print(f"\nlocalities   : {len(C.records_of(rl.json))} -> out/localities.json")

    # An independent traversal of the same collection, in a different order.
    print("\nsorted cross-check  (offset paging over a non-unique sort key)")
    sorted_ids = crawl_ids_sorted("/v1/listings", "listing_id", limit, limit_param)
    default_ids = [json.loads(l)["listing_id"]
                   for l in (C.OUT / "listings.jsonl").read_text(
                       encoding="utf-8").splitlines() if l.strip()]
    a, b = set(default_ids), set(sorted_ids)
    meta["sorted_crosscheck"] = {
        "sorted_records": len(sorted_ids), "sorted_distinct": len(b),
        "default_records": len(default_ids), "default_distinct": len(a),
        "only_in_default": sorted(a - b)[:20], "only_in_sorted": sorted(b - a)[:20],
        "n_only_in_default": len(a - b), "n_only_in_sorted": len(b - a),
    }
    print(f"  default traversal : {len(default_ids)} records, {len(a)} distinct")
    print(f"  sorted traversal  : {len(sorted_ids)} records, {len(b)} distinct")
    print(f"  only in default   : {len(a - b)}")
    print(f"  only in sorted    : {len(b - a)}")
    if a != b:
        print("  !! the two traversals disagree. Neither count is safe on its own;")
        print("     the union and the difference both matter for question 1.")

    # Reconcile every number that claims to answer question 1.
    env_total = None
    for line in (C.OUT / "envelopes.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("path") == "/v1/listings" and isinstance(e.get("envelope"), dict):
            env_total = e["envelope"].get("total")
            break
    meta["reconciliation"] = {
        "envelope_total": env_total,
        "records_pulled_default": len(default_ids),
        "distinct_ids_default": len(a),
        "union_of_both_traversals": len(a | b),
    }
    print("\nreconciliation for question 1")
    print(f"  envelope says total      : {env_total}")
    print(f"  records actually pulled  : {len(default_ids)}")
    print(f"  distinct listing_ids     : {len(a)}")
    print(f"  union of both traversals : {len(a | b)}")
    print("  (llms.txt claims a different number again. None of these is")
    print("   automatically the answer - decide which one the question asks for.)")

    meta["session"] = {"logins": SESSION["logins"], "refreshes": SESSION["refreshes"]}
    meta["requests_used"] = C.request_count()
    C.write_json(C.OUT / "fetch_meta.json", meta)
    print(f"\nrequests used : {C.request_count()}")
    print(f"written       : {C.OUT.resolve()}")
    print("next          : python report.py")


def _idset(path: Path, id_key: str) -> set:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.add(json.loads(line).get(id_key))
    return out




# ==========================================================================
# REPORT
# ==========================================================================

IST = timezone(timedelta(hours=5, minutes=30))
REFERENCE = datetime(2026, 9, 10, 0, 0, 0, tzinfo=IST)

MONEY_FIELDS = ["price", "deposit", "maintenance", "price_min", "price_max"]
AREA_FIELDS = ["carpet_area", "super_built_up_area", "super_builtup_area",
               "min_area_sqft", "max_area_sqft"]
CHENNAI_BBOX = (12.6, 13.5, 79.8, 80.5)  # lat_min, lat_max, lon_min, lon_max

R: dict = {}
LINES: list[str] = []


def w(s: str = "") -> None:
    LINES.append(s)


def load(name: str) -> list[dict]:
    p = C.OUT / f"{name}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------------

def num_summary(vals: list) -> dict:
    vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return {}
    vals.sort()
    return {"n": len(vals), "min": vals[0], "p25": vals[len(vals) // 4],
            "median": st.median(vals), "p75": vals[3 * len(vals) // 4],
            "max": vals[-1], "mean": round(st.fmean(vals), 2)}


def digit_hist(vals: list) -> dict:
    """Count values by number of digits. Mixed units show up as two clumps."""
    h: Counter = Counter()
    for v in vals:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if v is None:
            continue
        if v <= 0:
            h[f"<=0 ({v})" if v < 0 else "zero"] += 1
            continue
        h[f"{int(math.floor(math.log10(v))) + 1}d"] += 1
    return dict(sorted(h.items()))


def field_report(rows: list[dict], name: str) -> dict:
    out: dict = {"records": len(rows)}
    if not rows:
        return out

    keys: Counter = Counter()
    for r in rows:
        for k in r:
            if not k.startswith("_"):
                keys[k] += 1
    out["fields"] = {k: v for k, v in sorted(keys.items())}
    out["fields_missing_from_some_records"] = {
        k: len(rows) - v for k, v in keys.items() if v < len(rows)}

    out["numeric"] = {}
    out["digit_histogram"] = {}
    for f in MONEY_FIELDS + AREA_FIELDS + ["bedroom", "bathroom", "balcony", "floor",
                                           "total_floors", "covered_parking",
                                           "total_units", "total_towers",
                                           "total_listings", "city_id"]:
        vals = [r.get(f) for r in rows if f in r]
        s = num_summary(vals)
        if s:
            out["numeric"][f] = s
            if f in MONEY_FIELDS + AREA_FIELDS:
                out["digit_histogram"][f] = digit_hist(vals)

    out["categorical"] = {}
    for f in ["locality", "furnishing", "property_type", "project_status",
              "posted_by", "website", "facing_direction", "is_live",
              "is_verified", "city_id"]:
        vals = [r.get(f) for r in rows if f in r]
        if vals:
            out["categorical"][f] = dict(Counter(
                [v if not isinstance(v, (dict, list)) else str(v) for v in vals]
            ).most_common(40))

    # string hygiene: the reference claims these are lowercase everywhere
    hygiene = {}
    for f in ["locality", "furnishing", "property_type", "project_status"]:
        vals = [r.get(f) for r in rows if isinstance(r.get(f), str)]
        if not vals:
            continue
        hygiene[f] = {
            "distinct_raw": len(set(vals)),
            "distinct_normalised": len(set(v.strip().lower() for v in vals)),
            "not_lowercase": sum(1 for v in vals if v != v.lower()),
            "has_padding": sum(1 for v in vals if v != v.strip()),
            "examples_non_canonical": sorted(
                {v for v in vals if v != v.strip().lower()})[:12],
        }
    out["string_hygiene"] = hygiene
    return out


TS_PATTERNS = [
    ("iso_z", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")),
    ("iso_offset", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")),
    ("iso_naive", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")),
    ("space_sep", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")),
    ("date_only", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
]


def ts_report(rows: list[dict], field: str) -> dict:
    raw = [r.get(field) for r in rows if isinstance(r.get(field), str)]
    if not raw:
        return {}
    forms: Counter = Counter()
    for v in raw:
        for label, pat in TS_PATTERNS:
            if pat.match(v):
                forms[label] += 1
                break
        else:
            forms["other"] += 1

    # If the string says UTC but the clock inside it is local, the hour-of-day
    # histogram is the tell: listings get posted during waking hours, so a
    # distribution centred on 03:00 means the label is wrong, not the humans.
    hours_as_written: Counter = Counter()
    hours_if_utc_to_ist: Counter = Counter()
    parsed, unparsed = [], 0
    for v in raw:
        try:
            s = v.replace("Z", "+00:00").replace(" ", "T", 1)
            dt = datetime.fromisoformat(s)
        except Exception:
            unparsed += 1
            continue
        hours_as_written[dt.hour] += 1
        if dt.tzinfo is None:
            dt_aware = dt.replace(tzinfo=timezone.utc)
        else:
            dt_aware = dt
        hours_if_utc_to_ist[dt_aware.astimezone(IST).hour] += 1
        parsed.append(dt_aware)

    out = {
        "n": len(raw), "format_counts": dict(forms), "unparsed": unparsed,
        "hour_of_day_as_written": {h: hours_as_written.get(h, 0) for h in range(24)},
        "hour_of_day_if_treated_as_utc_then_ist":
            {h: hours_if_utc_to_ist.get(h, 0) for h in range(24)},
        "earliest": min(parsed).isoformat() if parsed else None,
        "latest": max(parsed).isoformat() if parsed else None,
        "after_reference_moment": sum(1 for d in parsed if d > REFERENCE),
        "reference_moment": REFERENCE.isoformat(),
    }

    # the seven-day window, computed both ways, to size the timezone risk
    for label, tz in (("as_written", None), ("forced_ist", IST)):
        lo, hi = REFERENCE - timedelta(days=7), REFERENCE
        cnt = 0
        for v in raw:
            try:
                s = v.replace("Z", "+00:00").replace(" ", "T", 1)
                dt = datetime.fromisoformat(s)
            except Exception:
                continue
            if tz is not None:
                dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else \
                    dt.replace(tzinfo=None).replace(tzinfo=tz)
            elif dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if lo <= dt < hi:
                cnt += 1
        out[f"in_7d_window_{label}"] = cnt
    return out


def duplicate_report(rows: list[dict], id_key: str) -> dict:
    ids = [r.get(id_key) for r in rows]
    id_counts = Counter(ids)
    repeated = {k: v for k, v in id_counts.items() if v > 1}

    def keyfn(r, precision):
        return (
            str(r.get("apartment_name", "")).strip().lower(),
            str(r.get("locality", "")).strip().lower(),
            r.get("bedroom"),
            r.get("carpet_area"),
            round(r.get("latitude"), precision) if isinstance(r.get("latitude"), float) else None,
            round(r.get("longitude"), precision) if isinstance(r.get("longitude"), float) else None,
        )

    clusters = {}
    for precision in (3, 4):
        g = defaultdict(list)
        for r in rows:
            g[keyfn(r, precision)].append(r.get(id_key))
        sizes = Counter(len(v) for v in g.values())
        multi = {k: v for k, v in g.items() if len(v) > 1}
        clusters[f"latlong_{precision}dp"] = {
            "distinct_keys": len(g),
            "cluster_size_histogram": dict(sorted(sizes.items())),
            "records_in_multi_clusters": sum(len(v) for v in multi.values()),
            "implied_distinct_properties": len(g),
            "example_clusters": [v for v in list(multi.values())[:12]],
        }

    # cross-portal signal: the same property syndicated to several websites
    cross = 0
    g = defaultdict(set)
    for r in rows:
        g[keyfn(r, 4)].add(r.get("website"))
    cross = sum(1 for k, v in g.items() if len(v) > 1)

    return {
        "total_records": len(rows),
        "distinct_ids": len(set(ids)),
        "ids_appearing_more_than_once": len(repeated),
        "extra_records_from_repeated_ids": sum(v - 1 for v in repeated.values()),
        "most_repeated_ids": dict(Counter(repeated).most_common(20)),
        "clustering": clusters,
        "clusters_spanning_multiple_websites": cross,
    }


def contact_report(rows: list[dict]) -> dict:
    g = defaultdict(list)
    for r in rows:
        c = r.get("posted_by_contact")
        if c:
            g[str(c).strip()].append(r)

    fanout = []
    for c, rs in g.items():
        fanout.append({
            "contact": c, "listings": len(rs),
            "distinct_localities": len({str(r.get("locality", "")).lower() for r in rs}),
            "distinct_names": len({r.get("posted_by_name") for r in rs}),
            "distinct_posted_by": sorted({str(r.get("posted_by")) for r in rs}),
            "verified_true": sum(1 for r in rs if r.get("is_verified") is True),
            "sample_ids": [r.get("listing_id") for r in rs[:8]],
        })
    fanout.sort(key=lambda x: -x["listings"])

    shapes = Counter()
    for c in g:
        digits = re.sub(r"\D", "", c)
        shapes[f"{len(digits)}digits{'+' if c.startswith('+') else ''}"] += 1

    return {
        "distinct_contacts": len(g),
        "contact_number_shapes": dict(shapes),
        "top_fanout": fanout[:25],
        "contacts_with_multiple_names": sum(1 for f in fanout if f["distinct_names"] > 1),
    }


def impossibility_report(rows: list[dict]) -> dict:
    def sample(pred, n=20):
        out = [r.get("listing_id") for r in rows if _safe(pred, r)]
        return {"count": len(out), "sample": sorted(x for x in out if x)[:n]}

    lat0, lat1, lon0, lon1 = CHENNAI_BBOX
    checks = {
        "carpet_area_greater_than_super_built_up": lambda r: (
            r.get("carpet_area") and r.get("super_built_up_area")
            and r["carpet_area"] > r["super_built_up_area"]),
        "floor_greater_than_total_floors": lambda r: (
            r.get("floor") is not None and r.get("total_floors") is not None
            and r["floor"] > r["total_floors"]),
        "price_non_positive": lambda r: isinstance(r.get("price"), (int, float)) and r["price"] <= 0,
        "carpet_area_non_positive": lambda r: isinstance(r.get("carpet_area"), (int, float)) and r["carpet_area"] <= 0,
        "bedroom_zero_or_negative": lambda r: isinstance(r.get("bedroom"), int) and r["bedroom"] <= 0,
        "bedroom_above_15": lambda r: isinstance(r.get("bedroom"), int) and r["bedroom"] > 15,
        "bathroom_above_15": lambda r: isinstance(r.get("bathroom"), int) and r["bathroom"] > 15,
        "negative_floor_below_minus5": lambda r: isinstance(r.get("floor"), int) and r["floor"] < -5,
        "latlong_outside_chennai_box": lambda r: (
            isinstance(r.get("latitude"), (int, float))
            and not (lat0 <= r["latitude"] <= lat1 and lon0 <= r["longitude"] <= lon1)),
        "latlong_null_or_zero": lambda r: (
            r.get("latitude") in (None, 0) or r.get("longitude") in (None, 0)),
        "total_floors_zero_or_negative": lambda r: (
            isinstance(r.get("total_floors"), int) and r["total_floors"] <= 0),
    }
    return {k: sample(v) for k, v in checks.items()}


def _safe(pred, r):
    try:
        return bool(pred(r))
    except Exception:
        return False


def project_count_report(listings: list[dict], projects: list[dict]) -> dict:
    by_pid: Counter = Counter()
    live_by_pid: Counter = Counter()
    for r in listings:
        pid = r.get("project_id")
        if pid:
            by_pid[pid] += 1
            if r.get("is_live") is True:
                live_by_pid[pid] += 1

    rows, mismatch_all, mismatch_live = [], 0, 0
    for p in projects:
        pid = p.get("project_id")
        claimed = p.get("total_listings")
        actual_all, actual_live = by_pid.get(pid, 0), live_by_pid.get(pid, 0)
        ok_all = claimed == actual_all
        ok_live = claimed == actual_live
        mismatch_all += (not ok_all)
        mismatch_live += (not ok_live)
        rows.append({"project_id": pid, "claimed": claimed,
                     "actual_all_records": actual_all,
                     "actual_is_live_only": actual_live})

    return {
        "projects": len(projects),
        "projects_where_claim_differs_from_all_records": mismatch_all,
        "projects_where_claim_differs_from_is_live_only": mismatch_live,
        "note": "two plausible readings of 'how many listings it has'. "
                "They give different answers to question 10; pick one and say why.",
        "sample": rows[:30],
        "listings_with_project_id": sum(by_pid.values()),
        "listings_without_project_id": sum(1 for r in listings if not r.get("project_id")),
        "project_ids_in_listings_not_in_projects": sorted(
            set(by_pid) - {p.get("project_id") for p in projects})[:20],
    }


# --------------------------------------------------------------------------

def report_main() -> None:
    disc_path = C.OUT / "discovery.json"
    disc = json.loads(disc_path.read_text(encoding="utf-8")) if disc_path.exists() else {}
    fmeta_path = C.OUT / "fetch_meta.json"
    fmeta = json.loads(fmeta_path.read_text(encoding="utf-8")) if fmeta_path.exists() else {}

    listings, rentals, projects = load("listings"), load("rentals"), load("projects")

    R["discovery_summary"] = {
        "health": disc.get("health", {}).get("json"),
        "token": disc.get("token"),
        "auth_matrix": disc.get("auth_matrix"),
        "pagination": {k: v for k, v in (disc.get("pagination") or {}).items()
                       if k != "limit_probe"},
        "limit_probe": (disc.get("pagination") or {}).get("limit_probe"),
        "paths_200": [r["path"] for r in disc.get("path_matrix", []) if r["status"] == 200],
        "paths_404": [r["path"] for r in disc.get("path_matrix", []) if r["status"] == 404],
        "paths_other": [(r["path"], r["status"]) for r in disc.get("path_matrix", [])
                        if r["status"] not in (200, 404)],
        "filters_and_sorting": disc.get("filters_and_sorting"),
        "favourites": disc.get("favourites"),
        "auth_extras": disc.get("auth_extras"),
    }
    R["fetch_meta"] = fmeta
    R["listings"] = field_report(listings, "listings")
    R["rentals"] = field_report(rentals, "rentals")
    R["projects"] = field_report(projects, "projects")
    R["listings_posted_at"] = ts_report(listings, "posted_at")
    R["rentals_posted_at"] = ts_report(rentals, "posted_at")
    R["projects_launch_date"] = ts_report(projects, "launch_date")
    R["listings_duplicates"] = duplicate_report(listings, "listing_id")
    R["rentals_duplicates"] = duplicate_report(rentals, "listing_id")
    R["listings_contacts"] = contact_report(listings)
    R["rentals_contacts"] = contact_report(rentals)
    R["listings_impossibilities"] = impossibility_report(listings)
    R["rentals_impossibilities"] = impossibility_report(rentals)
    R["project_listing_counts"] = project_count_report(listings, projects)

    # the assigned locality, raw. Question 5 wants a sum, so the unit matters
    # more here than anywhere else.
    loc = os.environ.get("IVY_LOCALITY", "guindy").strip().lower()
    sel = [r for r in rentals if str(r.get("locality", "")).strip().lower() == loc]
    rents = [r.get("price") for r in sel if isinstance(r.get("price"), (int, float))]
    R["assigned_locality"] = {
        "locality": loc, "rental_records": len(sel),
        "raw_sum_of_price": sum(rents),
        "price_summary": num_summary(rents),
        "digit_histogram": digit_hist(rents),
        "localities_that_normalise_to_this": sorted(
            {r.get("locality") for r in rentals
             if str(r.get("locality", "")).strip().lower() == loc}),
    }

    C.write_json(C.OUT / "report.json", R)
    _markdown()
    (C.OUT / "REPORT.md").write_text("\n".join(LINES), encoding="utf-8")
    print(f"written: {(C.OUT / 'REPORT.md').resolve()}")
    print(f"written: {(C.OUT / 'report.json').resolve()}")


def _markdown() -> None:
    d = R["discovery_summary"]
    w("# Ivy snapshot report")
    w("")
    w(f"- base url: `{C.BASE_URL}`")
    w(f"- listings: {R['listings'].get('records')} | rentals: "
      f"{R['rentals'].get('records')} | projects: {R['projects'].get('records')}")
    w(f"- requests used in fetch: {R['fetch_meta'].get('requests_used')}")
    w("")
    w("## Discovery")
    w(f"- health: `{json.dumps(d.get('health'))[:300]}`")
    tok = d.get("token") or {}
    w(f"- token: login claims `expires_in={tok.get('expires_in_claimed_by_login')}`, "
      f"jwt says `{tok.get('ttl_seconds_from_jwt')}`s, docs say `86400`s")
    pg = d.get("pagination") or {}
    w(f"- pagination: page moves window = `{pg.get('page_param_moves_window')}`, "
      f"offset moves window = `{pg.get('offset_param_moves_window')}`")
    w(f"- envelope: `{json.dumps(pg.get('envelope_baseline'))}`")
    w(f"- paths 200: {', '.join('`%s`' % p for p in d.get('paths_200', [])) or 'none'}")
    w(f"- paths 404: {', '.join('`%s`' % p for p in d.get('paths_404', [])) or 'none'}")
    w("")
    w("### Auth matrix")
    w("| case | status | detail |")
    w("| --- | --- | --- |")
    for row in d.get("auth_matrix") or []:
        w(f"| {row['case']} | {row['status']} | {str(row.get('detail'))[:70]} |")
    w("")
    w("### Filters and sorting")
    w("| param | status | n | verdict |")
    w("| --- | --- | --- | --- |")
    for row in d.get("filters_and_sorting") or []:
        w(f"| `{row['param']}` | {row['status']} | {row.get('returned')} | "
          f"{row.get('verdict')} |")
    w("")
    w("## Magnitude histograms  (mixed units show up here, not in a mean)")
    for coll in ("listings", "rentals", "projects"):
        dh = R[coll].get("digit_histogram") or {}
        if not dh:
            continue
        w(f"")
        w(f"**{coll}**")
        w("")
        w("| field | digit counts | min | median | max |")
        w("| --- | --- | --- | --- | --- |")
        for f, h in dh.items():
            s = R[coll]["numeric"].get(f, {})
            w(f"| `{f}` | {h} | {s.get('min')} | {s.get('median')} | {s.get('max')} |")
    w("")
    w("## Timestamps")
    for label, key in (("listings.posted_at", "listings_posted_at"),
                       ("rentals.posted_at", "rentals_posted_at")):
        t = R.get(key) or {}
        if not t:
            continue
        w(f"")
        w(f"**{label}** — formats {t.get('format_counts')}, range "
          f"{t.get('earliest')} .. {t.get('latest')}")
        w(f"- records after the reference moment: {t.get('after_reference_moment')}")
        w(f"- 7-day window counted as written: {t.get('in_7d_window_as_written')}")
        w(f"- 7-day window if the clock is really IST: {t.get('in_7d_window_forced_ist')}")
        hw = t.get("hour_of_day_as_written") or {}
        w(f"- hour-of-day as written: {[hw.get(h, 0) for h in range(24)]}")
        hi = t.get("hour_of_day_if_treated_as_utc_then_ist") or {}
        w(f"- hour-of-day if UTC then converted to IST: {[hi.get(h, 0) for h in range(24)]}")
    w("")
    w("## Duplicates")
    for label, key in (("listings", "listings_duplicates"), ("rentals", "rentals_duplicates")):
        t = R.get(key) or {}
        w(f"- **{label}**: {t.get('total_records')} records, "
          f"{t.get('distinct_ids')} distinct ids, "
          f"{t.get('ids_appearing_more_than_once')} ids repeat, "
          f"{t.get('clusters_spanning_multiple_websites')} property clusters span "
          f"more than one website")
        for prec, c in (t.get("clustering") or {}).items():
            w(f"  - {prec}: {c['distinct_keys']} distinct property keys, "
              f"cluster sizes {c['cluster_size_histogram']}")
    w("")
    w("## Contact fan-out  (top 10)")
    w("| contact | listings | localities | names | verified |")
    w("| --- | --- | --- | --- | --- |")
    for f in (R["listings_contacts"].get("top_fanout") or [])[:10]:
        w(f"| {f['contact']} | {f['listings']} | {f['distinct_localities']} | "
          f"{f['distinct_names']} | {f['verified_true']} |")
    w("")
    w("## Impossibility checks (listings)")
    w("| check | count |")
    w("| --- | --- |")
    for k, v in R["listings_impossibilities"].items():
        w(f"| {k} | {v['count']} |")
    w("")
    w("## Project listing counts")
    pc = R["project_listing_counts"]
    w(f"- projects: {pc['projects']}")
    w(f"- claim differs from all records: {pc['projects_where_claim_differs_from_all_records']}")
    w(f"- claim differs from is_live only: {pc['projects_where_claim_differs_from_is_live_only']}")
    w("")
    w("## Assigned locality")
    al = R["assigned_locality"]
    w(f"- `{al['locality']}`: {al['rental_records']} rental records, "
      f"raw price sum {al['raw_sum_of_price']}")
    w(f"- price digit histogram: {al['digit_histogram']}")
    w(f"- raw locality spellings that normalise to it: {al['localities_that_normalise_to_this']}")
    w("")
    w("String hygiene, full field lists, cluster examples and evidence ID samples "
      "are in `out/report.json`.")




# ==========================================================================
# entry point
# ==========================================================================

USAGE = """usage: python3 ivy.py <command> [flags]

  discover [--write]        probe the API; --write also exercises favourites
  fetch [--details N]       pull everything; N single-record cross-checks
  report                    distributions over the local snapshot
  all [--write] [--details N]

Credentials come from .env in the current directory."""


def cli() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cmd = args[0] if args else ""
    if cmd == "discover":
        discover_main()
    elif cmd == "fetch":
        fetch_main()
    elif cmd == "report":
        report_main()
    elif cmd == "all":
        discover_main()
        print()
        fetch_main()
        print()
        report_main()
    else:
        print(USAGE)
        raise SystemExit(2)


if __name__ == "__main__":
    cli()
