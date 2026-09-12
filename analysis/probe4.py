"""
Third round. Two earlier mistakes are corrected here.

Mistake one: I guessed dedup keys and read off whichever cluster count landed
nearest 3749. That is fitting the answer to a number produced by `total`, which
this same analysis has already shown under-reports in every slice. If the key is
chosen to hit 3749, then "the dedup agrees with total" is circular and means
nothing. So this file does not use a target. It measures how far apart records
are, looks for a gap in that distribution, and cuts there. Whatever count comes
out, comes out.

Mistake two: I treated `apartment_name` as a property identifier. It is a
building name, and a building holds many flats, which is why every key including
it over-merged. Within-building separation has to come from floor, facing,
exact area and coordinates.

    python3 probe4.py
"""

from __future__ import annotations

import json
import math
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
REFERENCE = datetime(2026, 9, 10, 0, 0, 0, tzinfo=IST)
SQFT_PER_SQM = 10.7639
SQM_CUTOFF = 200          # from probe3: a clean gap between 200 and 206
M_PER_DEG_LAT = 110_600   # at Chennai's latitude
M_PER_DEG_LON = 108_500
OUT = Path("out")
R: dict = {}

L = [json.loads(l) for l in (OUT / "listings.jsonl").read_text().splitlines() if l.strip()]
P = [json.loads(l) for l in (OUT / "projects.jsonl").read_text().splitlines() if l.strip()]


def hdr(n, t):
    print(f"\n{'=' * 70}\n{n}. {t}\n{'=' * 70}")


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


def pct(vals, q):
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


# ==========================================================================
# area units, fixed once and reused everywhere below
# ==========================================================================
def fix_areas():
    hdr("A", "AREA UNITS")
    sqm_ids = {r["listing_id"] for r in L
               if isinstance(r.get("carpet_area"), (int, float))
               and 0 < r["carpet_area"] < SQM_CUTOFF}
    sqft = [r["carpet_area"] for r in L if r["listing_id"] not in sqm_ids
            and isinstance(r.get("carpet_area"), (int, float))]
    sqm = [r["carpet_area"] for r in L if r["listing_id"] in sqm_ids]
    print(f"  sqft group: n={len(sqft)}, range {min(sqft)}-{max(sqft)}")
    print(f"  sqm  group: n={len(sqm)}, range {min(sqm)}-{max(sqm)}"
          f"  -> {min(sqm) * SQFT_PER_SQM:.0f}-{max(sqm) * SQFT_PER_SQM:.0f} sqft")
    print(f"  gap between the two groups: {max(sqm)} .. {min(sqft)}")

    out = []
    for r in L:
        d = dict(r)
        if r["listing_id"] in sqm_ids:
            for f in ("carpet_area", "super_built_up_area"):
                if isinstance(d.get(f), (int, float)):
                    d[f] = d[f] * SQFT_PER_SQM
            d["_sqm_fixed"] = True
        out.append(d)
    after = [r["carpet_area"] for r in out]
    print(f"  after normalisation: range {min(after):.0f}-{max(after):.0f}, "
          f"median {st.median(after):.0f}")
    R["area_units"] = {"sqm_count": len(sqm_ids), "cutoff": SQM_CUTOFF,
                       "sqm_ids": sorted(sqm_ids)[:20]}
    return out, sqm_ids


# ==========================================================================
# duplicates, by looking for a gap rather than by aiming at a number
# ==========================================================================
def duplicates(LN):
    hdr("B", "DUPLICATE DETECTION  (Q2)  -  no target number used")

    # Block on things a syndicated copy cannot change, then measure everything
    # that a portal might re-type or jitter.
    blocks = defaultdict(list)
    for r in LN:
        blocks[(norm(r.get("locality")), r.get("bedroom"),
                int((r.get("carpet_area") or 0) // 100))].append(r)
    # a copy could round the area across a 100 boundary, so also block one lower
    for r in LN:
        blocks[(norm(r.get("locality")), r.get("bedroom"),
                int((r.get("carpet_area") or 0) // 100) - 1)].append(r)

    seen = set()
    pairs = []
    for key, rows in blocks.items():
        if len(rows) < 2 or len(rows) > 400:
            continue
        for a, b in combinations(rows, 2):
            pk = tuple(sorted((a["listing_id"], b["listing_id"])))
            if pk in seen:
                continue
            seen.add(pk)
            ca, cb = a.get("carpet_area") or 0, b.get("carpet_area") or 0
            pa, pb = a.get("price") or 0, b.get("price") or 0
            if ca <= 0 or cb <= 0 or pa <= 0 or pb <= 0:
                continue
            dm = math.hypot((a["latitude"] - b["latitude"]) * M_PER_DEG_LAT,
                            (a["longitude"] - b["longitude"]) * M_PER_DEG_LON)
            pairs.append({
                "a": a["listing_id"], "b": b["listing_id"],
                "metres": dm,
                "carpet_pct": abs(ca - cb) / max(ca, cb),
                "price_pct": abs(pa - pb) / max(pa, pb),
                "same_building": norm(a.get("apartment_name")) == norm(b.get("apartment_name")),
                "same_floor": a.get("floor") == b.get("floor"),
                "same_facing": norm(a.get("facing_direction")) == norm(b.get("facing_direction")),
                "same_contact": a.get("posted_by_contact") == b.get("posted_by_contact"),
                "same_site": a.get("website") == b.get("website"),
                "same_bath": a.get("bathroom") == b.get("bathroom"),
            })
    print(f"  candidate pairs examined: {len(pairs):,d}")

    close = [p for p in pairs if p["metres"] < 2000]
    print(f"\n  distance distribution for pairs within 2 km (metres):")
    for q in (0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5):
        print(f"      p{q * 100:<6.1f} {pct([p['metres'] for p in close], q):>10.1f}")
    buckets = Counter()
    for p in pairs:
        m = p["metres"]
        b = ("<10m" if m < 10 else "<25m" if m < 25 else "<50m" if m < 50 else
             "<100m" if m < 100 else "<250m" if m < 250 else "<1km" if m < 1000 else ">=1km")
        buckets[b] += 1
    print(f"\n  pair counts by distance: {dict(buckets)}")

    # characterise the very-close pairs: if they are duplicates rather than
    # neighbouring flats, they should agree on the things a copy preserves
    for label, sel in (("<25m", [p for p in pairs if p["metres"] < 25]),
                       ("25-100m", [p for p in pairs if 25 <= p["metres"] < 100]),
                       ("100-1000m", [p for p in pairs if 100 <= p["metres"] < 1000])):
        if not sel:
            continue
        print(f"\n  pairs {label}: n={len(sel)}")
        for f in ("same_building", "same_floor", "same_facing", "same_contact",
                  "same_bath", "same_site"):
            print(f"      {f:16s} {sum(1 for p in sel if p[f]) / len(sel):6.1%}")
        print(f"      carpet within 2%  {sum(1 for p in sel if p['carpet_pct'] < 0.02) / len(sel):6.1%}")
        print(f"      price  within 10% {sum(1 for p in sel if p['price_pct'] < 0.10) / len(sel):6.1%}")

    # build clusters under a few explicit rules and report each count
    rules = {
        "<=25m & same bhk & carpet<=2%": lambda p: p["metres"] <= 25 and p["carpet_pct"] <= 0.02,
        "<=50m & carpet<=2% & same floor": lambda p: p["metres"] <= 50
            and p["carpet_pct"] <= 0.02 and p["same_floor"],
        "<=100m & carpet<=2% & same floor & same facing": lambda p: p["metres"] <= 100
            and p["carpet_pct"] <= 0.02 and p["same_floor"] and p["same_facing"],
        "same building & same floor & same facing & carpet<=2%": lambda p:
            p["same_building"] and p["same_floor"] and p["same_facing"]
            and p["carpet_pct"] <= 0.02,
        "same contact & same building & carpet<=2%": lambda p: p["same_contact"]
            and p["same_building"] and p["carpet_pct"] <= 0.02,
    }
    print()
    rows = []
    for label, pred in rules.items():
        parent = {r["listing_id"]: r["listing_id"] for r in LN}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        merged = 0
        for p in pairs:
            if pred(p):
                ra, rb = find(p["a"]), find(p["b"])
                if ra != rb:
                    parent[ra] = rb
                    merged += 1
        clusters = len({find(k) for k in parent})
        sizes = Counter(Counter(find(k) for k in parent).values())
        rows.append({"rule": label, "clusters": clusters, "merges": merged,
                     "sizes": dict(sorted(sizes.items()))})
        print(f"  {label:52s} -> {clusters:5d} properties "
              f"({len(LN) - clusters} records absorbed)")
    R["duplicates"] = {"rules": rows,
                       "distance_buckets": dict(buckets)}
    return rows


# ==========================================================================
def fraud(LN, corrupt_ids):
    hdr("C", "FRAUD  (Q9)  -  where does the cheap tail separate?")
    base = defaultdict(list)
    for r in LN:
        if r["listing_id"] in corrupt_ids:
            continue
        if (r.get("carpet_area") or 0) > 0 and (r.get("price") or 0) > 0:
            base[(norm(r.get("locality")), r.get("bedroom"))].append(
                r["price"] / r["carpet_area"])
    med = {k: st.median(v) for k, v in base.items() if len(v) >= 8}

    scored = []
    for r in LN:
        if r["listing_id"] in corrupt_ids:
            continue
        k = (norm(r.get("locality")), r.get("bedroom"))
        if k not in med or (r.get("carpet_area") or 0) <= 0 or (r.get("price") or 0) <= 0:
            continue
        scored.append(((r["price"] / r["carpet_area"]) / med[k], r))
    scored.sort(key=lambda t: t[0])
    vals = [s for s, _ in scored]
    print(f"  scored {len(scored)} listings (corrupt excluded)")
    print("  cheapness percentiles (1.0 = locality+bhk median rupees per sqft):")
    for q in (0.001, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.25, 0.5):
        print(f"      p{q * 100:<6.1f} {pct(vals, q):.4f}")

    print("\n  histogram of the cheap end:")
    for lo, hi in ((0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6),
                   (.6, .7), (.7, .8), (.8, .9), (.9, 1.0)):
        n = sum(1 for v in vals if lo <= v < hi)
        print(f"      {lo:.1f}-{hi:.1f}  {n:5d}  {'#' * min(60, n // 5)}")

    print("\n  the 40 cheapest, with the attributes a bait listing would share:")
    print(f"      {'listing_id':15s} {'cheap':>6s} {'ver':>5s} {'live':>5s} "
          f"{'by':8s} contact")
    for s, r in scored[:40]:
        print(f"      {r['listing_id']:15s} {s:6.3f} {str(r.get('is_verified')):>5s} "
              f"{str(r.get('is_live')):>5s} {str(r.get('posted_by')):8s} "
              f"{r.get('posted_by_contact')}")

    # do the cheap listings concentrate on particular phone numbers?
    for cut in (0.35, 0.4, 0.5):
        cheap = [r for s, r in scored if s < cut]
        c = Counter(r.get("posted_by_contact") for r in cheap)
        multi = {k: v for k, v in c.items() if v > 1}
        print(f"\n  cheapness < {cut}: {len(cheap)} listings across "
              f"{len(c)} contacts; {len(multi)} contacts hold more than one")
        if multi:
            print(f"      repeated contacts: {dict(sorted(multi.items(), key=lambda kv: -kv[1])[:10])}")
            print(f"      verified fraction here: "
                  f"{sum(1 for r in cheap if r.get('is_verified') is True) / len(cheap):.3f}"
                  f"   (overall {sum(1 for r in LN if r.get('is_verified') is True) / len(LN):.3f})")
            print(f"      posted_by: {dict(Counter(r.get('posted_by') for r in cheap))}")
    R["fraud"] = {"percentiles": {str(q): pct(vals, q) for q in
                                  (0.001, 0.01, 0.02, 0.05, 0.1)},
                  "cheapest_40": [{"listing_id": r["listing_id"],
                                   "cheapness": round(s, 4),
                                   "is_verified": r.get("is_verified"),
                                   "contact": r.get("posted_by_contact")}
                                  for s, r in scored[:40]]}
    return scored


# ==========================================================================
def q6_and_q7(LN, corrupt_ids, fake_ids):
    hdr("D", "Q6 PRICE PER SQFT, AND Q7 VERIFICATION")
    sel = [r for r in LN
           if r.get("is_live") is True and r.get("bedroom") == 2
           and r["listing_id"] not in corrupt_ids
           and r["listing_id"] not in fake_ids
           and (r.get("carpet_area") or 0) > 0 and (r.get("price") or 0) > 0]
    pps = [r["price"] / r["carpet_area"] for r in sel]
    print(f"  live 2BHK after exclusions: {len(sel)} records")
    print(f"  mean price per sqft: {st.fmean(pps):.2f}")
    print(f"  median {st.median(pps):.2f}, range {min(pps):.0f}-{max(pps):.0f}")

    # the same figure without the unit fix, to size what the fix is worth
    raw = [r for r in L if r.get("is_live") is True and r.get("bedroom") == 2
           and r["listing_id"] not in corrupt_ids and r["listing_id"] not in fake_ids
           and (r.get("carpet_area") or 0) > 0 and (r.get("price") or 0) > 0]
    rawpps = [r["price"] / r["carpet_area"] for r in raw]
    print(f"  without the square-metre fix it would be {st.fmean(rawpps):.2f} "
          f"({(st.fmean(rawpps) / st.fmean(pps) - 1) * 100:+.1f}%)")

    print()
    by_pid = defaultdict(list)
    for r in LN:
        if r.get("project_id") and (r.get("price") or 0) > 0:
            by_pid[r["project_id"]].append(r["price"])
    top = sorted((p for p in P if isinstance(p.get("price_max"), (int, float))),
                 key=lambda p: -p["price_max"])[:10]
    print(f"  {'project':10s} {'price_min':>10s} {'price_max':>10s} "
          f"{'max_inr':>14s} {'listings':>9s} {'their max price':>16s}")
    for p in top:
        pr = by_pid.get(p["project_id"]) or []
        print(f"  {p['project_id']:10s} {p['price_min']:>10.2f} {p['price_max']:>10.2f} "
              f"{int(round(p['price_max'] * 1e7)):>14,d} {len(pr):>9d} "
              f"{max(pr) if pr else 0:>16,d}")
    R["q6_q7"] = {"q6_mean_pps": round(st.fmean(pps), 2), "q6_n": len(sel),
                  "q6_without_unit_fix": round(st.fmean(rawpps), 2),
                  "q7_top": [{"project_id": p["project_id"],
                              "price_max": p["price_max"],
                              "price_max_inr": int(round(p["price_max"] * 1e7))}
                             for p in top[:3]]}


# ==========================================================================
if __name__ == "__main__":
    corrupt = set(json.loads((OUT / "probe3.json").read_text())["nine_sweep"]["nine_union"])
    print(f"corrupt set carried forward from probe3: {len(corrupt)} ids")
    LN, sqm_ids = fix_areas()
    duplicates(LN)
    fraud(LN, corrupt)
    q6_and_q7(LN, corrupt, set())
    (OUT / "probe4.json").write_text(json.dumps(R, indent=2, default=str))
    print(f"\nwritten: {OUT / 'probe4.json'}")
    print("Q6 above still has an empty fake set - rerun after Q9 is decided.")
