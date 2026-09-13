"""
Build submission.json from the snapshot.

Nothing here is hand-typed. Every answer is recomputed from out/*.jsonl each time
this runs, so the file in the repository can be regenerated and checked rather
than trusted. Two consequences worth stating: if the crawl is re-run the answers
move with it, and if one of the rules below is wrong, it is wrong in exactly one
place.

Every finding carries the evidence that reproduced it. Findings I could not
reproduce are not here, and the ones I deliberately left out are listed in the
README with the reason - precision is scored the same as recall, so a guess
costs more than a gap.

    python3 build_submission.py
"""

from __future__ import annotations

import json
import math
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

# ---- constants established by the probes ---------------------------------
IST = timezone(timedelta(hours=5, minutes=30))
REFERENCE = datetime(2026, 9, 10, 0, 0, 0, tzinfo=IST)
SQFT_PER_SQM = 10.7639
SQM_CUTOFF = 200            # clean gap: sqm group tops out at 197, sqft starts at 206
DISPLAY_UNIT_SWITCH = 10    # project prices: below 10 means crores, 10+ means lakhs
DEDUP_METRES = 100          # the count plateaus from 100 m to 300 m
M_PER_DEG_LAT, M_PER_DEG_LON = 110_600, 108_500
ASSIGNED_LOCALITY = "guindy"

CANDIDATE = {
    "name": "Bhavishya Gulati",
    "email": "bhavishya.20234049@mnnit.ac.in",
    "repo_url": "https://github.com/Bhavgulati/ivy-assignment",
    "demo_url": "",
}
API_KEY = "IVY26-C947D32E59D7"

OUT = Path("out")
RAW = [json.loads(l) for l in (OUT / "listings.jsonl").read_text().splitlines() if l.strip()]
RENT = [json.loads(l) for l in (OUT / "rentals.jsonl").read_text().splitlines() if l.strip()]
PROJ = [json.loads(l) for l in (OUT / "projects.jsonl").read_text().splitlines() if l.strip()]


def norm(s):
    return " ".join(str(s or "").strip().lower().split())


def parse_ist(s):
    """Listings carry no timezone marker; /health reports Asia/Kolkata and a
    reference_date with a +05:30 offset, so a naive stamp is IST."""
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return d.replace(tzinfo=IST) if d.tzinfo is None else d


# ---- unit normalisation --------------------------------------------------
SQM_IDS = {r["listing_id"] for r in RAW
           if isinstance(r.get("carpet_area"), (int, float))
           and 0 < r["carpet_area"] < SQM_CUTOFF}

L = []
for r in RAW:
    d = dict(r)
    if d["listing_id"] in SQM_IDS:
        for f in ("carpet_area", "super_built_up_area"):
            if isinstance(d.get(f), (int, float)):
                d[f] = d[f] * SQFT_PER_SQM
    L.append(d)

DEPOSIT_MONTH_IDS = sorted(r["listing_id"] for r in RENT
                           if isinstance(r.get("deposit"), (int, float))
                           and 0 < r["deposit"] < 100)


def to_inr(v):
    """Project prices are in display units, not rupees."""
    if not isinstance(v, (int, float)):
        return None
    return int(round(v * (1e5 if v >= DISPLAY_UNIT_SWITCH else 1e7)))


# ---- Q4: impossible records --------------------------------------------
def corrupt_ids():
    def hits(pred):
        out = []
        for r in RAW:
            try:
                if pred(r):
                    out.append(r["listing_id"])
            except Exception:
                pass
        return out

    checks = {
        "price <= 0": lambda r: r["price"] <= 0,
        "price < 100000 and not a plot": lambda r: r.get("property_type") != "plot"
            and 0 < r["price"] < 100_000,
        "carpet_area > super_built_up_area": lambda r: r["carpet_area"] > r["super_built_up_area"],
        "floor > total_floors and not a plot": lambda r: r.get("property_type") != "plot"
            and r["floor"] > r["total_floors"],
        "coordinates outside chennai": lambda r: not (12.6 <= r["latitude"] <= 13.5
                                                      and 79.8 <= r["longitude"] <= 80.5),
        "posted_at after the reference moment": lambda r: parse_ist(r["posted_at"]) > REFERENCE,
        "no bedroom and no bathroom, not a plot": lambda r: r.get("property_type") != "plot"
            and r["bedroom"] <= 0 and r["bathroom"] <= 0,
    }
    groups, ids = {}, set()
    for name, pred in checks.items():
        h = hits(pred)
        groups[name] = sorted(h)
        ids |= set(h)
    return sorted(ids), groups


# ---- Q9: listings that exist to generate enquiries ---------------------
def fake_ids():
    """Three properties hold together and nothing else in the data does.

    A contact whose entire portfolio is verified while the market runs at 60%;
    whose every listing is priced below the locality and bedroom median, not
    just its median one; and which posts under several different seller names.
    Ranking on any one of these alone does not separate anything - the largest
    portfolios in the city are ordinary agents.
    """
    base = defaultdict(list)
    corrupt = set(corrupt_ids()[0])
    for r in L:
        if r["listing_id"] in corrupt:
            continue
        if (r.get("carpet_area") or 0) > 0 and (r.get("price") or 0) > 0:
            base[(norm(r.get("locality")), r.get("bedroom"))].append(
                r["price"] / r["carpet_area"])
    med = {k: st.median(v) for k, v in base.items() if len(v) >= 8}

    def cheap(r):
        k = (norm(r.get("locality")), r.get("bedroom"))
        if k not in med or (r.get("carpet_area") or 0) <= 0 or (r.get("price") or 0) <= 0:
            return None
        return (r["price"] / r["carpet_area"]) / med[k]

    g = defaultdict(list)
    for r in L:
        if r["listing_id"] not in corrupt and r.get("posted_by_contact"):
            g[str(r["posted_by_contact"]).strip()].append(r)

    ring, ids = [], []
    for c, rs in g.items():
        ch = [x for x in (cheap(r) for r in rs) if x is not None]
        if not ch or len(rs) < 3:
            continue
        all_verified = all(r.get("is_verified") is True for r in rs)
        every_below = max(ch) < 0.85
        many_names = len({r.get("posted_by_name") for r in rs}) > 1
        if all_verified and every_below and many_names:
            ring.append(c)
            ids.extend(r["listing_id"] for r in rs)
    return sorted(ids), sorted(ring)


# ---- Q2: distinct properties -------------------------------------------
def unique_properties():
    """Cross-portal copies jitter the coordinates and re-type the building name,
    so matching is geometric: within 100 m, same bedroom count, carpet area
    within 2%, same floor, same facing. The cluster count is flat from 100 m to
    300 m, which is why 100 m is the threshold rather than a number chosen to
    land somewhere convenient."""
    blocks = defaultdict(list)
    for r in L:
        if not isinstance(r.get("latitude"), (int, float)):
            continue
        gy = int(r["latitude"] * M_PER_DEG_LAT // 200)
        gx = int(r["longitude"] * M_PER_DEG_LON // 200)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                blocks[(gy + dy, gx + dx, r.get("bedroom"))].append(r)

    parent = {r["listing_id"]: r["listing_id"] for r in L}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen, merged_pairs = set(), []
    for rows in blocks.values():
        if len(rows) < 2 or len(rows) > 600:
            continue
        for a, b in combinations(rows, 2):
            pk = (a["listing_id"], b["listing_id"])
            if pk in seen or (pk[1], pk[0]) in seen:
                continue
            seen.add(pk)
            dm = math.hypot((a["latitude"] - b["latitude"]) * M_PER_DEG_LAT,
                            (a["longitude"] - b["longitude"]) * M_PER_DEG_LON)
            if dm > DEDUP_METRES:
                continue
            ca, cb = a.get("carpet_area") or 0, b.get("carpet_area") or 0
            if ca <= 0 or cb <= 0 or abs(ca - cb) / max(ca, cb) > 0.02:
                continue
            if a.get("floor") != b.get("floor"):
                continue
            if norm(a.get("facing_direction")) != norm(b.get("facing_direction")):
                continue
            ra, rb = find(pk[0]), find(pk[1])
            if ra != rb:
                parent[ra] = rb
                merged_pairs.append(pk)
    clusters = {find(k) for k in parent}
    return len(clusters), merged_pairs


# ---- Q10 ----------------------------------------------------------------
def wrong_listing_counts():
    """total_listings is documented as listings "currently available", which is
    is_live true. Under that reading 341 of 460 projects are already correct and
    the error has no bias; under any other reading most projects are wrong,
    which describes a wrong rule rather than a broken field."""
    live = Counter()
    for r in L:
        if r.get("project_id") and r.get("is_live") is True:
            live[r["project_id"]] += 1
    wrong = [p["project_id"] for p in PROJ
             if p.get("total_listings") != live.get(p["project_id"], 0)]
    return len(wrong), sorted(wrong)


# ==========================================================================
def main():
    corrupt, corrupt_groups = corrupt_ids()
    fake, ring_contacts = fake_ids()
    q2, merged_pairs = unique_properties()
    q10, q10_ids = wrong_listing_counts()

    live = sum(1 for r in L if r.get("is_live") is True)
    inactive_ids = sorted(r["listing_id"] for r in L if r.get("is_live") is False)

    guindy = [r for r in RENT if norm(r.get("locality")) == ASSIGNED_LOCALITY]
    q5 = sum(r["price"] for r in guindy if isinstance(r.get("price"), (int, float)))

    excl = set(corrupt) | set(fake)
    sel = [r for r in L if r.get("is_live") is True and r.get("bedroom") == 2
           and r["listing_id"] not in excl
           and (r.get("carpet_area") or 0) > 0 and (r.get("price") or 0) > 0]
    q6 = round(st.fmean(r["price"] / r["carpet_area"] for r in sel), 2)

    priced = [(to_inr(p.get("price_max")), p) for p in PROJ
              if isinstance(p.get("price_max"), (int, float))]
    best_inr, best_p = max(priced, key=lambda t: t[0])

    lo, hi = REFERENCE - timedelta(days=7), REFERENCE
    q8 = sum(1 for r in L if (d := parse_ist(r.get("posted_at"))) and lo <= d < hi)
    q8_ids = sorted(r["listing_id"] for r in L
                    if (d := parse_ist(r.get("posted_at"))) and lo <= d < hi)

    answers = {
        "total_listing_records": len(L),
        "unique_properties": q2,
        "active_listings": live,
        "corrupt_listing_ids": corrupt,
        "total_monthly_rent": q5,
        "avg_price_per_sqft_2bhk": q6,
        "costliest_project": {"project_id": best_p["project_id"],
                              "price_max_inr": best_inr},
        "listings_last_7_days": q8,
        "fake_listing_ids": fake,
        "projects_with_wrong_listing_count": q10,
    }

    dup_ids = sorted({i for pk in merged_pairs for i in pk})[:20]
    sqm_evidence = sorted(SQM_IDS)[:20]
    proj_unit_evidence = [p["project_id"] for p in
                          sorted((p for p in PROJ if isinstance(p.get("price_max"), (int, float))),
                                 key=lambda p: -p["price_max"])[:20]]

    F = []

    def add(endpoint, category, documented, actual, how, impact, evidence=None):
        # the brief allows up to twenty identifiers per finding
        F.append({"endpoint": endpoint, "category": category,
                  "documented": documented, "actual": actual,
                  "how_found": how, "impact": impact,
                  "evidence": list(evidence or [])[:20]})

    # --- auth ---
    add("*", "auth",
        "the API key is appended as a query parameter, GET /v1/listings?api_key=...",
        "the key must be sent in an X-API-Key request header; sending it as a query "
        "parameter returns 401 and the error body says so",
        "first authenticated call of the session failed; read the 401 body instead of "
        "assuming the reference was right",
        "nothing works until this is fixed, so it blocks every other endpoint")
    add("/auth/login", "auth",
        "the body is email and password; the key is not mentioned for this endpoint",
        "login also requires the X-API-Key header, and returns 401 without it",
        "login failed with the documented body until the key header was added",
        "a client that only sends credentials cannot obtain a token")
    add("/auth/login", "auth",
        "the response field holding the token is called `token`",
        "the field is `access_token`; there is also `refresh_token` and `refresh_url`",
        "compared the documented response shape against the keys actually returned",
        "reading `token` yields undefined, and the session silently never authenticates")
    add("/auth/login", "auth",
        "expires_in is 86400, and a single login is enough for one working session",
        "expires_in is 900, so the access token lasts fifteen minutes",
        "read expires_in from the login response and decoded the token payload",
        "a long crawl or a browsing session dies partway through unless it re-authenticates")
    add("/auth/refresh", "auth",
        "tokens last 24 hours and there is no refresh flow",
        "POST /auth/refresh exists and takes {\"refresh_token\": ...}; the login "
        "response even advertises it in refresh_url",
        "probed the path after the 900 second TTL made a refresh mechanism likely",
        "without it the app cannot stay usable, and the crawl truncates on expiry")

    # --- pagination ---
    add("*", "pagination",
        "collections take `page`, 1-indexed, and `limit`",
        "`page` is accepted and silently ignored; the window is moved by `offset`",
        "requested limit=5&page=2 and compared the returned ids against page 1 - identical",
        "paging by `page` re-reads the first window forever, so any count derived "
        "that way is wrong and nothing warns you")
    add("*", "pagination",
        "limit may be up to 200",
        "limit is silently clamped to 50; the envelope reports limit=50 for any "
        "larger request, with no error",
        "asked for 100, 200, 500 and 1000 and read back the limit the envelope reported",
        "a client trusting the documented maximum makes four times fewer requests "
        "than it needs and stops early")
    add("*", "pagination",
        "responses are shaped {total, page, page_size, results}",
        "responses are {limit, offset, count, total, has_more, results}",
        "read the raw envelope on the first call rather than the documented example",
        "page_size and page do not exist, so a client reading them gets undefined")
    add("/v1/listings", "pagination",
        "total is the exact number of records matching your filters; to fetch every "
        "record, read total, divide by your limit, and request that many pages",
        "total reports 3749 while 4100 records are retrievable; the shortfall is about "
        "8.6% and it holds under filters too (property_type=plot reports 126 against "
        "138 served). Following the documented procedure loses 351 listings",
        "paged to exhaustion with limit=50, then again with limit=25, then a third time "
        "ordered by price; all three traversals returned the same 4100 distinct ids",
        "every count in an analysis built on total is short by 8.6%, including the "
        "record count, the live count and the seven-day count",
        sorted(r["listing_id"] for r in L)[-20:])

    # --- endpoints ---
    add("/v1/listing/{id}", "missing_endpoint",
        "a single listing is served from /v1/listing/{listing_id}",
        "404; the path that works is /v1/listings/{listing_id}",
        "called both spellings with an id taken from the collection",
        "a detail page built on the documented path 404s for every listing")
    add("/v1/listings/{id}/similar", "missing_endpoint",
        "up to ten comparable listings, same locality, same bedroom count, price within 15%",
        "404",
        "called it with a known-good listing id",
        "the documented 'you may also like' strip cannot be built from this endpoint")
    add("/v1/favourites", "missing_endpoint",
        "a logged-in user can save listings via GET, POST and DELETE /v1/favourites",
        "404 on all three methods, with both the British and American spellings",
        "called each method with a valid token and a valid listing id",
        "saved listings has no server side at all, so per-user persistence has to be "
        "built client side")
    add("/v1/analytics/summary", "missing_endpoint",
        "pre-computed aggregates for the city: total_listings, median_price, "
        "median_price_per_sqft, by_locality, by_bhk",
        "404",
        "called it while building the insights screen",
        "every figure on an analytics screen has to be computed from the listing "
        "records instead")
    add("/v1/localities", "undocumented_endpoint",
        "not in the reference",
        "exists and returns {city, count, results} - the authoritative list of "
        "locality spellings for the city",
        "probed plausible paths after /v1/analytics/summary turned out to be absent",
        "it is the only reliable source for the locality filter, since that filter "
        "is an exact match")
    add("/", "undocumented_endpoint",
        "not in the reference",
        "exists unauthenticated and returns a service descriptor that names /health, "
        "/register and /llms.txt",
        "opened the base URL in a browser before writing any client code",
        "it advertises paths the reference never mentions")
    add("/llms.txt", "undocumented_endpoint",
        "not in the reference",
        "exists unauthenticated, 3001 bytes, advertised by GET / as for_agents",
        "followed the for_agents pointer from the service root",
        "it publishes per-city figures for four of the assignment's questions, and "
        "they disagree with the API: it claims 3916 listing records for this city "
        "against 4100 retrievable")

    # --- filters and sorting ---
    add("/v1/listings", "filters",
        "project_id filters the collection - the reference states that total_listings "
        "always agrees with what GET /v1/listings?project_id=... returns",
        "project_id is accepted and ignored; the response is byte-identical to the "
        "unfiltered window",
        "requested a real project_id and compared the returned id list against the "
        "unfiltered baseline",
        "a per-project listing count cannot be obtained from the API and has to be "
        "derived from a full crawl")
    add("/v1/listings", "sorting",
        "sort_by accepts price, carpet_area, posted_at and bedroom",
        "price and bedroom sort correctly; carpet_area and posted_at change the "
        "order but the result is not sorted by those fields",
        "requested each field ascending and descending and checked the returned "
        "values for monotonicity",
        "a client offering 'newest first' or 'largest first' shows an order that "
        "looks deliberate and is not")

    # --- units ---
    add("/v1/projects", "units",
        "price_min and price_max are in rupees",
        "they are in Indian display units, where the unit depends on the magnitude: "
        "a value below 10 is crores and a value of 10 or more is lakhs. Measured as "
        "listings_max/price_max, which splits cleanly into 431 projects at about 1e7 "
        "and 27 at about 1.5e5, and price_min splits the same way at 384 and 76 - "
        "matching the digit counts exactly",
        "the raw values made price_min exceed price_max for 357 of 460 projects, "
        "which is impossible for a minimum and a maximum, so the multiplier was "
        "measured against each project's own listing prices rather than assumed",
        "taken literally the costliest project is wrong by a factor of 26, and every "
        "project price shown to a user is wrong by 100x one way or the other",
        proj_unit_evidence)
    add("/v1/listings", "units",
        "area is square feet, integer, everywhere in the API",
        "333 records state carpet_area and super_built_up_area in square metres. The "
        "two groups do not overlap: the square-metre values run 34 to 197 and the "
        "square-foot values start at 206, and super/carpet stays near 1.34 in both, "
        "so these are small units rather than small flats",
        "a duplicate cluster held four records near 1261 sqft and one at 113; "
        "113 x 10.7639 is 1216",
        "rupees per square foot comes out 63.7% too high if these are left alone, "
        "and the affected records never match their own duplicates on area",
        sqm_evidence)
    add("/v1/rentals", "units",
        "deposit is the security deposit in rupees",
        "301 records give deposit as a number of months instead. Their values run 2 "
        "to 10, and multiplying by that record's rent lands them on the same "
        "distribution as the rupee records, whose deposit is a median 6 months of rent",
        "the digit histogram was bimodal - about 300 records in the single and double "
        "digits against 1249 in the five and six digit range",
        "a deposit of 6 is shown to a user as six rupees",
        DEPOSIT_MONTH_IDS[:20])

    # --- timestamps ---
    add("/v1/listings", "timestamps",
        "timestamps are ISO 8601, UTC, with a Z suffix, everywhere in the API",
        "posted_at carries no timezone marker at all on any of the 4100 records, and "
        "the clock is IST: /health reports timezone Asia/Kolkata and a reference_date "
        "with a +05:30 offset",
        "classified every posted_at string by format; all 4100 were naive ISO",
        "reading them as UTC shifts the seven-day window by five and a half hours and "
        "changes the count from 122 to 106")
    add("*", "consistency",
        "one timestamp convention for the whole API",
        "listings use naive ISO with no offset while rentals use a Z suffix - two "
        "formats in the same API, and neither collection matches the other",
        "the same format classifier run over both collections",
        "a single parser written against either collection mis-reads the other")

    # --- records ---
    add("/v1/listings", "completeness",
        "returns active sale listings only; inactive, expired and withdrawn listings "
        "are excluded server side, so anything returned is safe to show a user",
        "867 of 4100 retrievable records have is_live false, and is_live is not in "
        "the documented listing object at all",
        "counted is_live across the full crawl after noticing the reference never "
        "mentions the field",
        "a client trusting the documentation shows withdrawn listings to users",
        inactive_ids[:20])
    add("/v1/listings", "duplicates",
        "every listing_id is globally unique, and each listing corresponds to exactly "
        "one physical property",
        "the first half is true - 4100 ids, 4100 distinct. The second is not: the same "
        "property is syndicated across portals under different ids, and 983 records "
        "are a second or later copy of a property already present, leaving 3117 "
        "distinct properties",
        "matched on geometry rather than text, because copies jitter the coordinates "
        "and re-type the building name - they agree on apartment_name only 48% of the "
        "time. Within 100 m, same bedroom, carpet within 2%, same floor and facing, "
        "the cluster count is flat from 100 m through 300 m",
        "any count, average or listing page treats one flat as five, and 79% of these "
        "pairs cross portals so a user sees the same property repeatedly",
        dup_ids)
    add("/v1/listings", "data_quality",
        "the listing object describes a real property; description is the seller's "
        "own text and posted_by_contact is a verified contact number",
        "63 records describe something that cannot exist, in seven disjoint groups of "
        "exactly nine: non-positive price, price under a lakh, carpet area larger than "
        "super built up area, floor above total_floors, coordinates outside the city, "
        "posted_at after the reference moment, and no bedroom and no bathroom on a "
        "non-plot property",
        "ran impossibility predicates over the full crawl. The count of nine recurring "
        "across unrelated defects is what made the set identifiable - and it is also "
        "what ruled out the 133 records a naive area check flags, which are the "
        "square-metre records rather than corrupt ones",
        "they poison any mean that divides by price or area, which is why question 6 "
        "excludes them",
        corrupt[:20])
    add("/v1/listings", "fraud",
        "posted_by_contact is the seller's verified contact number, and is_verified "
        "means the operations team has checked the listing",
        "110 listings across 7 phone numbers are not genuine. Each of those numbers "
        "has a fully verified portfolio while the market runs at 60% verified, every "
        "one of its listings is priced below its locality and bedroom median with none "
        "above 0.79 of it, and each number posts under three to six different seller "
        "names",
        "volume alone separates nothing - the five largest portfolios in the city are "
        "ordinary agents at 38% to 73% verified and market prices. The three signals "
        "together leave a clean gap: the next contact down is 29% verified with one "
        "name and prices above the median",
        "is_verified is worse than useless here, since being fully verified is the "
        "signal of the fake set. Contact details shown to users route enquiries to "
        "these numbers",
        ring_contacts + fake[:13])
    add("/v1/projects", "consistency",
        "total_listings is the number of listings currently available in the project, "
        "recomputed whenever a listing is added or withdrawn, so it always agrees with "
        "what GET /v1/listings?project_id=... returns",
        "it disagrees for 119 of 460 projects when counted against the live listings "
        "actually served",
        "counted live listings per project_id from the full crawl, since the "
        "project_id filter is ignored. Of the readings tried, currently-available - "
        "is_live true - leaves 341 projects exactly correct with no bias in the error; "
        "counting all records instead leaves only 124 correct, which describes a wrong "
        "rule rather than a broken field",
        "a project page showing this number is wrong about a quarter of the time",
        q10_ids[:20])

    submission = {"api_key": API_KEY, "candidate": CANDIDATE,
                  "answers": answers, "findings": F}

    root = Path("..")
    (root / "submission.json").write_text(
        json.dumps(submission, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # The frontend must show the same numbers this file reports. Rather than
    # reimplementing the correction rules in TypeScript and hoping the two stay
    # in step, export them once from here and have the app read them. If a rule
    # changes, both outputs change together.
    manifest = {
        "generated_from": "analysis/build_submission.py",
        "api_key": API_KEY,
        "reference_moment": REFERENCE.isoformat(),
        "rules": {
            "area": {"note": "carpet_area and super_built_up_area below the "
                             "cutoff are square metres, not square feet",
                     "sqm_cutoff": SQM_CUTOFF, "sqft_per_sqm": SQFT_PER_SQM},
            "project_price": {"note": "display units: below the switch means "
                                      "crores, at or above means lakhs",
                              "switch": DISPLAY_UNIT_SWITCH,
                              "below_multiplier": 1e7,
                              "at_or_above_multiplier": 1e5},
            "rental_deposit": {"note": "deposit below the cutoff is a count of "
                                       "months of rent", "months_cutoff": 100},
            "dedup": {"metres": DEDUP_METRES, "carpet_tolerance": 0.02,
                      "also_requires": ["bedroom", "floor", "facing_direction"]},
        },
        "flags": {
            "corrupt_listing_ids": corrupt,
            "fake_listing_ids": fake,
            "fake_contacts": ring_contacts,
            "sqm_listing_ids": sorted(SQM_IDS),
            "duplicate_listing_ids": sorted({i for pk in merged_pairs for i in pk}),
            "deposit_in_months_rental_ids": DEPOSIT_MONTH_IDS,
            "projects_with_wrong_listing_count": q10_ids,
        },
        "answers": answers,
    }
    app_public = root / "app" / "public"
    app_public.mkdir(parents=True, exist_ok=True)
    (app_public / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"manifest: {(app_public / 'manifest.json').resolve()}")

    print("answers")
    for k, v in answers.items():
        print(f"  {k:36s} {v if not isinstance(v, list) else str(len(v)) + ' ids'}")
    print(f"\ncorrupt groups (Q4)")
    for k, v in corrupt_groups.items():
        print(f"  {k:42s} {len(v)}")
    print(f"\nfake ring contacts (Q9): {ring_contacts}")
    print(f"findings: {len(F)}")
    print(f"by category: {dict(Counter(f['category'] for f in F))}")
    print(f"findings without evidence: "
          f"{sum(1 for f in F if not f['evidence'])} of {len(F)}")
    print(f"\nwritten: {(root / 'submission.json').resolve()}")


if __name__ == "__main__":
    main()
