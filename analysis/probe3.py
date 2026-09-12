"""
Second round of hypothesis tests.

Three things drove this file:

1. A dedup cluster contained four records with carpet_area around 1261 and one
   with 113. 113 x 10.764 = 1216. That record is in square metres. So area units
   have to be normalised before anything that divides by area or matches on it -
   which is question 6 and question 2 respectively.

2. Six independent impossibility checks each matched exactly 9 records, with no
   overlap. That is not what natural data corruption looks like. If the corrupt
   set was planted 9 at a time, then sweeping a wide set of predicates and
   keeping the ones that hit exactly 9 should recover the rest of it - and,
   importantly, should reject the predicate that matched 133 records, because
   those were a unit error rather than corruption.

3. `total` is about 8.6% below the number of records served, in every
   collection and under every filter tried. One explanation that fits all of
   them is that `total` counts distinct properties. That is testable: if an
   independently-built dedup lands on the same number, two different methods
   agree and question 2 is answered.

    python3 probe3.py
"""

from __future__ import annotations

import json
import math
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
REFERENCE = datetime(2026, 9, 10, 0, 0, 0, tzinfo=IST)
SQFT_PER_SQM = 10.7639
OUT = Path("out")
R: dict = {}

L = [json.loads(l) for l in (OUT / "listings.jsonl").read_text().splitlines() if l.strip()]
RN = [json.loads(l) for l in (OUT / "rentals.jsonl").read_text().splitlines() if l.strip()]
P = [json.loads(l) for l in (OUT / "projects.jsonl").read_text().splitlines() if l.strip()]
ENVELOPE_TOTAL = {"listings": 3749, "rentals": 1417, "projects": 421}


def hdr(n, t):
    print(f"\n{'=' * 70}\n{n}. {t}\n{'=' * 70}")


def norm(s):
    return " ".join(str(s or "").strip().lower().split())


def safe(pred, r):
    try:
        return bool(pred(r))
    except Exception:
        return False


def parse_ist(s):
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return d.replace(tzinfo=IST) if d.tzinfo is None else d


# ==========================================================================
def a_area_units():
    """Which records state area in square metres rather than square feet?

    Two signals, and they should agree. First, super/carpet ratio: that ratio is
    a property of the building, not of the unit of measurement, so it stays
    around 1.2-1.5 whichever unit is used - meaning a record whose ratio is
    normal but whose magnitude is a tenth of everyone else's is a unit error, not
    a small flat. Second, multiplying by 10.7639 should move the value into the
    main distribution rather than past it.
    """
    hdr("A", "AREA UNITS  (blocks Q6 and Q2)")
    ratios = [r["super_built_up_area"] / r["carpet_area"] for r in L
              if r.get("carpet_area") and r.get("super_built_up_area")
              and r["carpet_area"] > 0]
    print(f"  super/carpet ratio: median {st.median(ratios):.3f}, "
          f"p5 {sorted(ratios)[len(ratios)//20]:.3f}, "
          f"p95 {sorted(ratios)[19*len(ratios)//20]:.3f}")

    big = [r["carpet_area"] for r in L if (r.get("carpet_area") or 0) >= 200]
    print(f"  carpet_area >= 200 (assumed sqft): n={len(big)}, "
          f"median {st.median(big):.0f}, range {min(big)}-{max(big)}")

    for cut in (100, 150, 200, 250, 300):
        small = [r for r in L if 0 < (r.get("carpet_area") or 0) < cut]
        if not small:
            continue
        conv = [r["carpet_area"] * SQFT_PER_SQM for r in small]
        inside = sum(1 for c in conv if min(big) * 0.8 <= c <= max(big) * 1.2)
        print(f"  cut <{cut:4d}: {len(small):4d} records; after x10.7639 "
              f"{inside}/{len(small)} land inside the sqft distribution "
              f"(median {st.median(conv):.0f})")

    # the ratio test, independent of any cutoff
    suspect = [r for r in L
               if r.get("carpet_area") and r.get("super_built_up_area")
               and r["carpet_area"] > 0
               and 1.1 <= r["super_built_up_area"] / r["carpet_area"] <= 1.6
               and r["carpet_area"] < 250]
    print(f"\n  ratio-normal but magnitude-small: {len(suspect)} records")
    print(f"  ids: {sorted(r['listing_id'] for r in suspect)[:20]}")
    R["area_units"] = {"suspect_count": len(suspect),
                       "suspect_ids": sorted(r["listing_id"] for r in suspect)[:20],
                       "ratio_median": round(st.median(ratios), 3)}
    return {r["listing_id"] for r in suspect}


def normalised(rows, sqm_ids):
    """A copy with areas in square feet throughout."""
    out = []
    for r in rows:
        d = dict(r)
        if r.get("listing_id") in sqm_ids:
            for f in ("carpet_area", "super_built_up_area", "super_builtup_area"):
                if isinstance(d.get(f), (int, float)):
                    d[f] = d[f] * SQFT_PER_SQM
            d["_area_unit_fixed"] = True
        out.append(d)
    return out


# ==========================================================================
def b_nine_sweep():
    """Sweep many impossibility predicates; keep the ones that hit exactly 9.

    The point of the sweep is not that 9 is magic. It is that six unrelated
    checks all landing on 9 with no overlap says the corrupt set was planted in
    groups of 9, so a predicate hitting exactly 9 is probably one of the planted
    defects and a predicate hitting 133 probably is not.
    """
    hdr("B", "IMPOSSIBILITY SWEEP  (Q4)")
    checks = {
        "price <= 0": lambda r: r.get("price") is not None and r["price"] <= 0,
        "carpet > super": lambda r: r["carpet_area"] > r["super_built_up_area"],
        "floor > total_floors (non-plot)": lambda r: r.get("property_type") != "plot"
            and r["floor"] > r["total_floors"],
        "latlong outside chennai": lambda r: not (12.6 <= r["latitude"] <= 13.5
                                                  and 79.8 <= r["longitude"] <= 80.5),
        "posted_at after REFERENCE": lambda r: parse_ist(r.get("posted_at")) > REFERENCE,
        "bedroom <= 0 (non-plot)": lambda r: r.get("property_type") != "plot"
            and r["bedroom"] <= 0,
        "total_floors <= 0 (non-plot)": lambda r: r.get("property_type") != "plot"
            and r["total_floors"] <= 0,
        "bathroom <= 0 (non-plot)": lambda r: r.get("property_type") != "plot"
            and r["bathroom"] <= 0,
        "bathroom > bedroom + 3": lambda r: r["bathroom"] > r["bedroom"] + 3,
        "balcony < 0": lambda r: r["balcony"] < 0,
        "covered_parking < 0": lambda r: r["covered_parking"] < 0,
        "floor < -3": lambda r: r["floor"] < -3,
        "total_floors > 100": lambda r: r["total_floors"] > 100,
        "bedroom > 12": lambda r: r["bedroom"] > 12,
        "carpet_area <= 0": lambda r: r["carpet_area"] <= 0,
        "super <= 0": lambda r: r["super_built_up_area"] <= 0,
        "plot with bedrooms": lambda r: r.get("property_type") == "plot" and r["bedroom"] > 0,
        "plot with floors": lambda r: r.get("property_type") == "plot" and r["total_floors"] > 0,
        "carpet > 10x super": lambda r: r["carpet_area"] > 10 * r["super_built_up_area"],
        "latitude exactly 0": lambda r: r["latitude"] == 0,
        "price < 100000 (non-plot)": lambda r: r.get("property_type") != "plot"
            and 0 < r["price"] < 100_000,
        "posted_at before 2020": lambda r: parse_ist(r.get("posted_at")).year < 2020,
        "carpet_area < 100": lambda r: 0 < r["carpet_area"] < 100,
    }
    hits, nines = {}, set()
    for name, pred in checks.items():
        ids = sorted(r["listing_id"] for r in L if safe(pred, r))
        hits[name] = ids
        flag = ""
        if len(ids) == 9:
            flag = "  <== exactly 9"
            nines |= set(ids)
        elif 0 < len(ids) <= 20:
            flag = "  <== small, inspect"
        print(f"  {name:34s} {len(ids):5d}{flag}")

    print(f"\n  union of the exactly-9 predicates: {len(nines)} records")
    print(f"  sorted: {sorted(nines)}")
    R["nine_sweep"] = {"hits": {k: v[:30] for k, v in hits.items()},
                       "nine_union": sorted(nines)}
    return nines


# ==========================================================================
def c_dedup(sqm_ids, corrupt_ids):
    """Cluster after normalising units. Does any key land near 3749?"""
    hdr("C", "DEDUP AFTER UNIT NORMALISATION  (Q2)")
    LN = normalised(L, sqm_ids)
    target = ENVELOPE_TOTAL["listings"]
    print(f"  records {len(LN)}, envelope total {target}, "
          f"difference {len(LN) - target}\n")

    def band(v, width):
        return None if v is None else int(round(v / width))

    keys = {
        "apartment+locality+bhk+carpet50": lambda r: (
            norm(r.get("apartment_name")), norm(r.get("locality")), r.get("bedroom"),
            band(r.get("carpet_area"), 50)),
        "apartment+locality+bhk+carpet100": lambda r: (
            norm(r.get("apartment_name")), norm(r.get("locality")), r.get("bedroom"),
            band(r.get("carpet_area"), 100)),
        "apartment+bhk+carpet50+bath": lambda r: (
            norm(r.get("apartment_name")), r.get("bedroom"),
            band(r.get("carpet_area"), 50), r.get("bathroom")),
        "latlong4dp+bhk": lambda r: (
            band(r.get("latitude"), 1e-4), band(r.get("longitude"), 1e-4),
            r.get("bedroom")),
        "latlong3dp+bhk+carpet100": lambda r: (
            band(r.get("latitude"), 1e-3), band(r.get("longitude"), 1e-3),
            r.get("bedroom"), band(r.get("carpet_area"), 100)),
        "apartment+bhk+floor+facing+carpet100": lambda r: (
            norm(r.get("apartment_name")), r.get("bedroom"), r.get("floor"),
            norm(r.get("facing_direction")), band(r.get("carpet_area"), 100)),
        "apartment+locality+bhk+floor": lambda r: (
            norm(r.get("apartment_name")), norm(r.get("locality")),
            r.get("bedroom"), r.get("floor")),
        "apartment+bhk+super100+facing": lambda r: (
            norm(r.get("apartment_name")), r.get("bedroom"),
            band(r.get("super_built_up_area"), 100), norm(r.get("facing_direction"))),
    }
    rows = []
    for label, fn in keys.items():
        g = defaultdict(list)
        for r in LN:
            g[fn(r)].append(r)
        multi = [v for v in g.values() if len(v) > 1]
        cross = sum(1 for v in multi if len({x.get("website") for x in v}) > 1)
        delta = len(g) - target
        rows.append({"key": label, "clusters": len(g), "delta_vs_total": delta,
                     "cross_website_clusters": cross,
                     "size_histogram": dict(sorted(Counter(len(v) for v in g.values()).items()))})
        print(f"  {label:38s} clusters={len(g):5d}  delta={delta:+6d}  "
              f"cross-site={cross:4d}")
    best = min(rows, key=lambda r: abs(r["delta_vs_total"]))
    print(f"\n  closest to the envelope total: {best['key']} "
          f"({best['clusters']}, delta {best['delta_vs_total']:+d})")
    print(f"  size histogram: {best['size_histogram']}")
    print("  >> if this is within 1% of 3749, two independent methods agree")
    R["dedup"] = rows
    return LN


# ==========================================================================
def d_fraud():
    """Question 9. Listings that exist to generate enquiries.

    Part 3 allows phone numbers as evidence, which points at posted_by_contact.
    But volume alone is a weak signal: a busy agency legitimately lists a lot.
    Look for the combination - a contact whose listings are unverified, priced
    far below comparable stock, and spread across localities a real local agent
    would not cover.
    """
    hdr("D", "FRAUD SIGNALS  (Q9)")
    # price per sqft by locality and bedroom, as a comparison baseline
    base = defaultdict(list)
    for r in L:
        if (r.get("carpet_area") or 0) > 200 and (r.get("price") or 0) > 0:
            base[(norm(r.get("locality")), r.get("bedroom"))].append(
                r["price"] / r["carpet_area"])
    med = {k: st.median(v) for k, v in base.items() if len(v) >= 8}

    def cheapness(r):
        k = (norm(r.get("locality")), r.get("bedroom"))
        if k not in med or (r.get("carpet_area") or 0) <= 200 or (r.get("price") or 0) <= 0:
            return None
        return (r["price"] / r["carpet_area"]) / med[k]

    g = defaultdict(list)
    for r in L:
        if r.get("posted_by_contact"):
            g[str(r["posted_by_contact"]).strip()].append(r)

    rows = []
    for c, rs in g.items():
        ch = [x for x in (cheapness(r) for r in rs) if x is not None]
        rows.append({
            "contact": c, "n": len(rs),
            "localities": len({norm(r.get("locality")) for r in rs}),
            "names": len({r.get("posted_by_name") for r in rs}),
            "verified_frac": round(sum(1 for r in rs if r.get("is_verified") is True) / len(rs), 3),
            "live_frac": round(sum(1 for r in rs if r.get("is_live") is True) / len(rs), 3),
            "median_cheapness": round(st.median(ch), 3) if ch else None,
            "ids": sorted(r["listing_id"] for r in rs),
        })
    print(f"  distinct contacts: {len(rows)}")
    print(f"  overall verified fraction: "
          f"{sum(1 for r in L if r.get('is_verified') is True) / len(L):.3f}")

    print("\n  contacts with >=3 listings, zero verified, sorted by cheapness:")
    cand = [r for r in rows if r["n"] >= 3 and r["verified_frac"] == 0.0]
    cand.sort(key=lambda r: (r["median_cheapness"] if r["median_cheapness"] is not None else 9))
    for r in cand[:15]:
        print(f"    {r['contact']:16s} n={r['n']:3d} loc={r['localities']:2d} "
              f"names={r['names']:2d} cheapness={r['median_cheapness']} "
              f"live={r['live_frac']}")
    print(f"  total such contacts: {len(cand)}, "
          f"covering {sum(r['n'] for r in cand)} listings")

    print("\n  most repeated descriptions:")
    for d, c in Counter(norm(r.get("description")) for r in L).most_common(6):
        if c > 1:
            print(f"    {c:4d}x  {d[:80]}")

    print("\n  cheapest 1% of listings vs their locality+bhk median:")
    scored = [(cheapness(r), r) for r in L]
    scored = [(s, r) for s, r in scored if s is not None]
    scored.sort(key=lambda t: t[0])
    for s, r in scored[:12]:
        print(f"    {r['listing_id']:14s} cheapness={s:.3f} "
              f"verified={r.get('is_verified')} contact={r.get('posted_by_contact')}")
    R["fraud"] = {"candidate_contacts": cand[:25],
                  "cheapest": [{"listing_id": r["listing_id"], "cheapness": round(s, 3),
                                "contact": r.get("posted_by_contact"),
                                "is_verified": r.get("is_verified")}
                               for s, r in scored[:40]]}


# ==========================================================================
def e_q10_variants(corrupt_ids):
    """Which reading of 'how many listings it has' does the data support?

    The right reading is the one under which most projects are already correct.
    A rule that makes 336 of 460 projects wrong is more likely to be the wrong
    rule than a description of a broken field.
    """
    hdr("E", "PROJECT LISTING COUNTS  (Q10)")
    claimed = {p["project_id"]: p.get("total_listings") for p in P}
    variants = {
        "all records": lambda r: True,
        "is_live true": lambda r: r.get("is_live") is True,
        "is_live and not corrupt": lambda r: r.get("is_live") is True
                                             and r["listing_id"] not in corrupt_ids,
        "is_verified true": lambda r: r.get("is_verified") is True,
        "is_live and is_verified": lambda r: r.get("is_live") is True
                                             and r.get("is_verified") is True,
        "not corrupt": lambda r: r["listing_id"] not in corrupt_ids,
    }
    for label, pred in variants.items():
        c = Counter()
        for r in L:
            if r.get("project_id") and safe(pred, r):
                c[r["project_id"]] += 1
        wrong = [pid for pid, n in claimed.items() if n != c.get(pid, 0)]
        diffs = [claimed[pid] - c.get(pid, 0) for pid in claimed]
        exact = len(claimed) - len(wrong)
        print(f"  {label:26s} wrong={len(wrong):4d}  exact={exact:4d}  "
              f"median diff={st.median(diffs):+.1f}  "
              f"mean |diff|={st.fmean(abs(d) for d in diffs):.2f}")
        R.setdefault("q10", {})[label] = {"wrong": len(wrong), "exact": exact}
    print("\n  >> the reading with the most exact matches is the one the field meant")


# ==========================================================================
if __name__ == "__main__":
    sqm_ids = a_area_units()
    corrupt_ids = b_nine_sweep()
    c_dedup(sqm_ids, corrupt_ids)
    d_fraud()
    e_q10_variants(corrupt_ids)
    (OUT / "probe3.json").write_text(json.dumps(R, indent=2, default=str))
    print(f"\nwritten: {OUT / 'probe3.json'}")
