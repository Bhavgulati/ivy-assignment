"""
Re-verify every finding in submission.json.

The point is not to check that the findings are well written. It is to re-run,
against the live service and the local snapshot, the observation each one claims
to have made, and to fail loudly where the claim and the result disagree.

Findings are scored as F1, so a claim that no longer reproduces costs the same
as one never made. Anything this script marks FAIL should be deleted from
build_submission.py before submitting, not argued with.

    python3 verify_findings.py

Uses about 40 requests.
"""

from __future__ import annotations

import json
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ivy as C

IST = timezone(timedelta(hours=5, minutes=30))
REFERENCE = datetime(2026, 9, 10, 0, 0, 0, tzinfo=IST)
OUT = Path("out")
SUB = json.loads((Path("..") / "submission.json").read_text())

L = [json.loads(l) for l in (OUT / "listings.jsonl").read_text().splitlines() if l.strip()]
RENT = [json.loads(l) for l in (OUT / "rentals.jsonl").read_text().splitlines() if l.strip()]
PROJ = [json.loads(l) for l in (OUT / "projects.jsonl").read_text().splitlines() if l.strip()]

RESULTS: list[dict] = []


def check(endpoint: str, category: str, ok: bool, detail: str):
    RESULTS.append({"endpoint": endpoint, "category": category,
                    "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {category:22s} {endpoint:28s} {detail}")


def norm(s):
    return " ".join(str(s or "").strip().lower().split())


def parse_ist(s):
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return d.replace(tzinfo=IST) if d.tzinfo is None else d


# ==========================================================================
print("live checks")
C.session_login()
tok = C.SESSION["access"]

# auth: key must be a header
r = C.get("/v1/listings", params={"limit": 1, "api_key": C.API_KEY},
          token=tok, api_key=False, tag="v:querykey")
check("*", "auth", r.status == 401,
      f"query-param key -> {r.status} {str(r.detail)[:60]}")

# auth: login needs the key too
r = C.post("/auth/login", body={"email": "demo1@ivy.homes", "password": C.PASSWORD},
           api_key=False, tag="v:loginnokey")
check("/auth/login", "auth", r.status == 401, f"login without key -> {r.status}")

# auth: token field name and ttl
r = C.login("demo1@ivy.homes", C.PASSWORD)
keys = sorted(r.json.keys()) if isinstance(r.json, dict) else []
check("/auth/login", "auth", "access_token" in keys and "token" not in keys,
      f"login keys {keys}")
check("/auth/login", "auth", r.json.get("expires_in") == 900,
      f"expires_in={r.json.get('expires_in')} (docs say 86400)")

# auth: refresh exists
rr = C.post("/auth/refresh", body={}, token=tok, tag="v:refresh")
check("/auth/refresh", "auth", rr.status in (200, 422),
      f"POST /auth/refresh -> {rr.status} (404 would mean it does not exist)")

# pagination: page ignored, offset works
a = C.get("/v1/listings", params={"limit": 5}, token=tok, tag="v:pg1")
b = C.get("/v1/listings", params={"limit": 5, "page": 2}, token=tok, tag="v:pg2")
c = C.get("/v1/listings", params={"limit": 5, "offset": 5}, token=tok, tag="v:pg3")
ids = lambda x: [r.get("listing_id") for r in C.records_of(x.json)]
check("*", "pagination", ids(a) == ids(b) and ids(a) != ids(c),
      f"page=2 same as page 1: {ids(a) == ids(b)}; offset=5 differs: {ids(a) != ids(c)}")

# pagination: limit clamp
r = C.get("/v1/listings", params={"limit": 200}, token=tok, tag="v:limit200")
n = len(C.records_of(r.json))
check("*", "pagination", n == 50, f"limit=200 returned {n} (docs allow 200)")

# pagination: envelope shape
env = C.envelope_meta(a.json)
check("*", "pagination",
      set(env) >= {"limit", "offset", "count", "total", "has_more"},
      f"envelope keys {sorted(env)}")

# pagination: total under-reports
check("/v1/listings", "pagination", env.get("total") == 3749 and len(L) == 4100,
      f"total={env.get('total')} vs {len(L)} crawled")

# endpoints
sample = L[0]["listing_id"]
for path, cat, want in [
    (f"/v1/listing/{sample}", "missing_endpoint", 404),
    (f"/v1/listings/{sample}/similar", "missing_endpoint", 404),
    ("/v1/favourites", "missing_endpoint", 404),
    ("/v1/analytics/summary", "missing_endpoint", 404),
    ("/v1/localities", "undocumented_endpoint", 200),
    ("/", "undocumented_endpoint", 200),
    ("/llms.txt", "undocumented_endpoint", 200),
]:
    r = C.get(path, token=tok, tag=f"v:path:{path}")
    shown = path.replace(sample, "{id}")
    check(shown, cat, r.status == want, f"{r.status} (expected {want})")

# filters: project_id ignored
pid = next((x["project_id"] for x in L if x.get("project_id")), None)
base = C.get("/v1/listings", params={"limit": 50}, token=tok, tag="v:fbase")
f = C.get("/v1/listings", params={"limit": 50, "project_id": pid}, token=tok, tag="v:fpid")
check("/v1/listings", "filters", ids(base) == ids(f),
      f"project_id={pid} returns the unfiltered window: {ids(base) == ids(f)}")

# sorting: carpet_area and posted_at accepted but not sorted
for field in ("carpet_area", "posted_at"):
    r = C.get("/v1/listings", params={"limit": 50, "sort_by": field, "order": "asc"},
              token=tok, tag=f"v:sort:{field}")
    vals = [x.get(field) for x in C.records_of(r.json) if x.get(field) is not None]
    mono = all(x <= y for x, y in zip(vals, vals[1:]))
    check("/v1/listings", "sorting", r.ok and not mono,
          f"sort_by={field} -> {r.status}, monotonic={mono}")

# ==========================================================================
print("\nsnapshot checks")

# units: project prices
inverted = sum(1 for p in PROJ if p.get("price_min", 0) > p.get("price_max", 0))
two_digit = sum(1 for p in PROJ if isinstance(p.get("price_max"), (int, float))
                and p["price_max"] >= 10)
check("/v1/projects", "units", inverted == 357 and two_digit == 27,
      f"{inverted} projects inverted as rupees; {two_digit} have price_max >= 10")

# units: square metres
sqm = [r for r in L if 0 < r.get("carpet_area", 0) < 200]
sqft = [r["carpet_area"] for r in L if r.get("carpet_area", 0) >= 200]
check("/v1/listings", "units", len(sqm) == 333 and max(r["carpet_area"] for r in sqm) < min(sqft),
      f"{len(sqm)} below 200, top {max(r['carpet_area'] for r in sqm)}, "
      f"sqft starts {min(sqft)} — groups disjoint")

# units: rental deposit in months
small = [r for r in RENT if 0 < r.get("deposit", 0) < 100]
large = [r for r in RENT if r.get("deposit", 0) >= 100]
ratio = st.median([r["deposit"] / r["price"] for r in large if r.get("price")])
check("/v1/rentals", "units", 290 <= len(small) <= 310 and 4 <= ratio <= 8,
      f"{len(small)} deposits under 100; rupee group is a median {ratio:.1f} months of rent")

# timestamps
naive = sum(1 for r in L if isinstance(r.get("posted_at"), str)
            and not r["posted_at"].endswith("Z"))
zsuf = sum(1 for r in RENT if isinstance(r.get("posted_at"), str)
           and r["posted_at"].endswith("Z"))
check("/v1/listings", "timestamps", naive == len(L),
      f"{naive}/{len(L)} listing stamps carry no timezone marker")
check("*", "consistency", zsuf == len(RENT),
      f"{zsuf}/{len(RENT)} rental stamps use Z — two conventions")

# completeness
inactive = sum(1 for r in L if r.get("is_live") is False)
check("/v1/listings", "completeness", inactive == 867,
      f"{inactive} records have is_live false, and is_live is undocumented")

# duplicates / data_quality / fraud counts must match the answers
ans = SUB["answers"]
check("/v1/listings", "duplicates", ans["unique_properties"] == 3117,
      f"{len(L)} records describe {ans['unique_properties']} properties")
check("/v1/listings", "data_quality", len(ans["corrupt_listing_ids"]) == 63,
      f"{len(ans['corrupt_listing_ids'])} impossible records")
check("/v1/listings", "fraud", len(ans["fake_listing_ids"]) == 110,
      f"{len(ans['fake_listing_ids'])} listings on 7 contacts")

# consistency: project listing counts
live = Counter()
for r in L:
    if r.get("project_id") and r.get("is_live") is True:
        live[r["project_id"]] += 1
wrong = sum(1 for p in PROJ if p.get("total_listings") != live.get(p["project_id"], 0))
check("/v1/projects", "consistency", wrong == ans["projects_with_wrong_listing_count"],
      f"{wrong} projects disagree, submission says "
      f"{ans['projects_with_wrong_listing_count']}")

# ==========================================================================
# evidence sanity: every id cited must exist in the snapshot
print("\nevidence checks")
lids = {r["listing_id"] for r in L}
rids = {r["listing_id"] for r in RENT}
pids = {p["project_id"] for p in PROJ}
contacts = {str(r.get("posted_by_contact")) for r in L}
known = lids | rids | pids | contacts

bad = []
for f in SUB["findings"]:
    unknown = [e for e in f["evidence"] if e not in known]
    if unknown:
        bad.append((f["endpoint"], f["category"], unknown[:3]))
    if len(f["evidence"]) > 20:
        bad.append((f["endpoint"], f["category"], ["over 20 identifiers"]))
if bad:
    for e, c, u in bad:
        print(f"  [FAIL] {c:22s} {e:28s} unrecognised evidence {u}")
else:
    print(f"  [PASS] every evidence identifier in all "
          f"{len(SUB['findings'])} findings resolves to a real record")

# ==========================================================================
failed = [r for r in RESULTS if not r["ok"]]
print(f"\n{'=' * 70}")
print(f"{len(RESULTS) - len(failed)} passed, {len(failed)} failed, "
      f"{len(SUB['findings'])} findings in submission.json")
if failed:
    print("\nDELETE these from build_submission.py before submitting:")
    for r in failed:
        print(f"  {r['category']:22s} {r['endpoint']:28s} {r['detail']}")
else:
    print("Every finding reproduced. Nothing to remove.")
print(f"requests used: {C.request_count()}")
Path("out/verify_findings.json").write_text(json.dumps(RESULTS, indent=2))
