"""
Hypothesis tests over the local snapshot.

Each section states a hypothesis, runs the check that would falsify it, and
prints the numbers. It concludes nothing on its own - a section that prints an
inconvenient number has done its job.

    python3 probe2.py

Reads out/listings.jsonl, out/rentals.jsonl, out/projects.jsonl.
Writes out/probe2.json with the full detail, including evidence id lists.
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
OUT = Path("out")
R: dict = {}


def load(name):
    p = OUT / f"{name}.jsonl"
    if not p.exists():
        raise SystemExit(f"missing {p} - run: python3 ivy.py fetch")
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def hdr(n, t):
    print(f"\n{'=' * 70}\n{n}. {t}\n{'=' * 70}")


L, RN, P = load("listings"), load("rentals"), load("projects")


# ==========================================================================
def h1_project_price_units():
    """H: price_min is in lakhs and price_max is in crores, not rupees.

    The falsifier is the listings themselves. If the hypothesis holds, a
    project's own listing prices should sit inside [price_min x 1e5,
    price_max x 1e7]. If the documentation were right and both were rupees,
    every listing would be millions of times above the range - and price_min
    would exceed price_max, which is impossible for a min and a max.
    """
    hdr(1, "PROJECT PRICE UNITS  (Q7)")
    by_pid = defaultdict(list)
    for r in L:
        if r.get("project_id") and isinstance(r.get("price"), (int, float)) and r["price"] > 0:
            by_pid[r["project_id"]].append(r["price"])

    inverted = sum(1 for p in P
                   if isinstance(p.get("price_min"), (int, float))
                   and isinstance(p.get("price_max"), (int, float))
                   and p["price_min"] > p["price_max"])
    print(f"  projects where price_min > price_max, taken literally: "
          f"{inverted} / {len(P)}")

    candidates = {
        "both rupees (as documented)": (1, 1),
        "both lakhs": (1e5, 1e5),
        "both crores": (1e7, 1e7),
        "min lakhs, max crores": (1e5, 1e7),
        "min crores, max crores": (1e7, 1e7),
    }
    rows = []
    for label, (fmin, fmax) in candidates.items():
        ok = tot = 0
        bad_inversion = 0
        for p in P:
            lo, hi = p.get("price_min"), p.get("price_max")
            if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
                continue
            lo_r, hi_r = lo * fmin, hi * fmax
            if lo_r > hi_r:
                bad_inversion += 1
            prices = by_pid.get(p.get("project_id")) or []
            for pr in prices:
                tot += 1
                if lo_r * 0.5 <= pr <= hi_r * 2.0:
                    ok += 1
        frac = ok / tot if tot else 0
        rows.append({"interpretation": label, "listings_inside_range": ok,
                     "listings_checked": tot, "fraction": round(frac, 4),
                     "projects_still_inverted": bad_inversion})
        print(f"  {label:28s} {frac:7.1%} of {tot} project listings in range, "
              f"{bad_inversion} still inverted")

    best = max(rows, key=lambda r: (r["fraction"], -r["projects_still_inverted"]))
    print(f"\n  best fit: {best['interpretation']}")
    print("  >> look at the records this gets WRONG before accepting it")

    if best["interpretation"] == "min lakhs, max crores":
        costly = max((p for p in P if isinstance(p.get("price_max"), (int, float))),
                     key=lambda p: p["price_max"])
        print(f"  costliest by price_max: {costly['project_id']}  "
              f"price_max={costly['price_max']}  -> INR "
              f"{int(round(costly['price_max'] * 1e7)):,d}")
        R["q7_candidate"] = {"project_id": costly["project_id"],
                             "price_max_raw": costly["price_max"],
                             "price_max_inr": int(round(costly["price_max"] * 1e7))}
    R["h1_project_price_units"] = {"inverted_literally": inverted, "candidates": rows}


# ==========================================================================
def h2_corrupt_vs_plots():
    """H: bedroom=0 and total_floors=0 are plots, not corruption.

    Q4 says a small number of records describe something that cannot exist.
    147 and 138 are not small. If these cluster on property_type, they are
    legitimate and a naive impossibility rule is wrong about them.
    """
    hdr(2, "CORRUPT vs LEGITIMATELY ZERO  (Q4)")
    for field in ("bedroom", "total_floors", "bathroom"):
        zero = [r for r in L if isinstance(r.get(field), int) and r[field] <= 0]
        print(f"  {field} <= 0 : {len(zero)} records")
        print(f"      property_type: {dict(Counter(r.get('property_type') for r in zero))}")
    print(f"\n  property_type overall: {dict(Counter(r.get('property_type') for r in L))}")

    plots = [r for r in L if r.get("property_type") == "plot"]
    print(f"  plots: {len(plots)}; of those bedroom<=0: "
          f"{sum(1 for r in plots if (r.get('bedroom') or 0) <= 0)}, "
          f"total_floors<=0: {sum(1 for r in plots if (r.get('total_floors') or 0) <= 0)}")

    # impossibilities that survive the plot explanation
    def bad(pred):
        return sorted(r["listing_id"] for r in L if _safe(pred, r))

    checks = {
        "price_non_positive": lambda r: r.get("price") is not None and r["price"] <= 0,
        "carpet_gt_super": lambda r: r.get("carpet_area") and r.get("super_built_up_area")
                                     and r["carpet_area"] > r["super_built_up_area"],
        "floor_gt_total_floors": lambda r: r.get("property_type") != "plot"
                                           and r.get("floor") is not None
                                           and r.get("total_floors") is not None
                                           and r["floor"] > r["total_floors"],
        "latlong_outside_chennai": lambda r: isinstance(r.get("latitude"), (int, float))
                                             and not (12.6 <= r["latitude"] <= 13.5
                                                      and 79.8 <= r["longitude"] <= 80.5),
        "posted_at_after_reference": lambda r: _after_ref(r.get("posted_at")),
        "carpet_area_under_100": lambda r: isinstance(r.get("carpet_area"), (int, float))
                                           and 0 < r["carpet_area"] < 100,
        "bedroom_zero_but_not_plot": lambda r: r.get("property_type") != "plot"
                                               and isinstance(r.get("bedroom"), int)
                                               and r["bedroom"] <= 0,
        "total_floors_zero_but_not_plot": lambda r: r.get("property_type") != "plot"
                                                    and isinstance(r.get("total_floors"), int)
                                                    and r["total_floors"] <= 0,
    }
    out = {}
    union = set()
    print()
    for name, pred in checks.items():
        ids = bad(pred)
        out[name] = {"count": len(ids), "ids": ids[:30]}
        if name not in ("carpet_area_under_100", "posted_at_after_reference"):
            union |= set(ids)
        print(f"  {name:34s} {len(ids):4d}   {ids[:4]}")
    print(f"\n  union of the strict impossibility checks: {len(union)} records")
    print(f"  ids: {sorted(union)}")
    R["h2_corrupt"] = {"checks": out, "strict_union": sorted(union)}


def _safe(pred, r):
    try:
        return bool(pred(r))
    except Exception:
        return False


def _parse_ist(s):
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return d.replace(tzinfo=IST) if d.tzinfo is None else d


def _after_ref(s):
    d = _parse_ist(s)
    return d is not None and d > REFERENCE


# ==========================================================================
def h3_the_total_gap():
    """H: some identifiable class of record is served but not counted by `total`.

    listings 4100 vs 3749, rentals 1550 vs 1417, projects 460 vs 421 - all
    about 8.5%. city_id is uniform, so it is not city leakage. Test whether any
    single predicate has exactly the size of the gap.
    """
    hdr(3, "THE ~8.5% GAP BETWEEN total AND WHAT IS SERVED")
    for name, rows, total in (("listings", L, 3749), ("rentals", RN, 1417),
                              ("projects", P, 421)):
        gap = len(rows) - total
        print(f"\n  {name}: served {len(rows)}, total says {total}, gap {gap} "
              f"({gap / len(rows):.2%})")
        cands = {}
        if name != "projects":
            cands["is_live false"] = sum(1 for r in rows if r.get("is_live") is False)
            cands["is_verified false"] = sum(1 for r in rows if r.get("is_verified") is False)
            cands["no project_id"] = sum(1 for r in rows if not r.get("project_id"))
            cands["property_type plot"] = sum(1 for r in rows if r.get("property_type") == "plot")
            cands["posted_at after REFERENCE"] = sum(1 for r in rows if _after_ref(r.get("posted_at")))
            cands["price non-positive"] = sum(1 for r in rows
                                              if isinstance(r.get("price"), (int, float))
                                              and r["price"] <= 0)
        for f in ("website", "posted_by", "furnishing", "locality", "project_status"):
            vals = Counter(r.get(f) for r in rows if f in r)
            for v, c in vals.items():
                if abs(c - gap) <= max(2, gap * 0.02):
                    cands[f"{f}={v}"] = c
        for k, v in sorted(cands.items(), key=lambda kv: abs(kv[1] - gap)):
            mark = "  <== matches the gap" if abs(v - gap) <= 2 else ""
            print(f"      {k:34s} {v:5d}{mark}")
        R.setdefault("h3_gap", {})[name] = {"served": len(rows), "total": total,
                                            "gap": gap, "candidates": cands}


# ==========================================================================
def h4_dedup_keys():
    """H: the same property appears under several listing_ids across portals.

    Q2 asks for distinct properties, so duplicates must exist. An exact key on
    lat/long at 4dp found none, which means the key is wrong rather than that
    the duplicates are absent - coordinates and areas are probably jittered
    between copies. Try progressively looser keys and watch where the cluster
    count stabilises.
    """
    hdr(4, "DEDUP KEYS  (Q2)")
    print(f"  records: {len(L)}, distinct listing_id: {len({r['listing_id'] for r in L})}")
    print(f"  websites: {dict(Counter(r.get('website') for r in L))}\n")

    def norm(s):
        return " ".join(str(s or "").strip().lower().split())

    keys = {
        "apartment+locality+bhk+carpet": lambda r: (
            norm(r.get("apartment_name")), norm(r.get("locality")),
            r.get("bedroom"), r.get("carpet_area")),
        "apartment+locality+bhk": lambda r: (
            norm(r.get("apartment_name")), norm(r.get("locality")), r.get("bedroom")),
        "apartment+bhk+super": lambda r: (
            norm(r.get("apartment_name")), r.get("bedroom"),
            r.get("super_built_up_area")),
        "latlong 2dp+bhk": lambda r: (
            round(r["latitude"], 2) if isinstance(r.get("latitude"), float) else None,
            round(r["longitude"], 2) if isinstance(r.get("longitude"), float) else None,
            r.get("bedroom")),
        "latlong 3dp+bhk+carpet": lambda r: (
            round(r["latitude"], 3) if isinstance(r.get("latitude"), float) else None,
            round(r["longitude"], 3) if isinstance(r.get("longitude"), float) else None,
            r.get("bedroom"), r.get("carpet_area")),
        "apartment+bhk+floor+facing": lambda r: (
            norm(r.get("apartment_name")), r.get("bedroom"), r.get("floor"),
            norm(r.get("facing_direction"))),
        "description text": lambda r: norm(r.get("description")),
        "url tail": lambda r: str(r.get("listing_url", "")).rstrip("/").split("/")[-1],
    }
    rows = []
    for label, fn in keys.items():
        g = defaultdict(list)
        for r in L:
            g[fn(r)].append(r)
        sizes = Counter(len(v) for v in g.values())
        multi = [v for v in g.values() if len(v) > 1]
        cross = sum(1 for v in multi if len({x.get("website") for x in v}) > 1)
        rows.append({"key": label, "clusters": len(g),
                     "size_histogram": dict(sorted(sizes.items())),
                     "multi_clusters": len(multi),
                     "cross_website_clusters": cross})
        print(f"  {label:30s} clusters={len(g):5d}  sizes={dict(sorted(sizes.items()))}"
              f"  cross-site={cross}")
        if multi:
            ex = multi[0]
            print(f"      example: {[(x['listing_id'], x.get('website'), x.get('price'), x.get('carpet_area')) for x in ex][:5]}")
    R["h4_dedup"] = rows


# ==========================================================================
def h5_rental_deposit_units():
    """H: for a few hundred rentals, `deposit` is a number of months, not rupees.

    The digit histogram is bimodal: ~300 records in the single and double
    digits, ~1250 in the five and six digit range. If the small ones are months,
    deposit x price should land in the same range as the large ones.
    """
    hdr(5, "RENTAL DEPOSIT UNITS  (Q5 neighbourhood)")
    small = [r for r in RN if isinstance(r.get("deposit"), (int, float)) and 0 < r["deposit"] < 100]
    large = [r for r in RN if isinstance(r.get("deposit"), (int, float)) and r["deposit"] >= 100]
    print(f"  deposit < 100 : {len(small)} records, values "
          f"{sorted({r['deposit'] for r in small})[:20]}")
    print(f"  deposit >= 100: {len(large)} records")
    if large:
        ratios = [r["deposit"] / r["price"] for r in large
                  if isinstance(r.get("price"), (int, float)) and r["price"]]
        print(f"  large group deposit/rent months: median "
              f"{st.median(ratios):.2f}, range {min(ratios):.2f}-{max(ratios):.2f}")
    if small:
        implied = [r["deposit"] * r["price"] for r in small
                   if isinstance(r.get("price"), (int, float))]
        print(f"  small group, if months: implied deposit median "
              f"{st.median(implied):,.0f}  (large group median "
              f"{st.median([r['deposit'] for r in large]):,.0f})")
        print(f"  small group ids: {[r['listing_id'] for r in small][:12]}")
    R["h5_deposit"] = {"small_n": len(small), "large_n": len(large),
                       "small_ids": [r["listing_id"] for r in small][:20]}


# ==========================================================================
def h6_answers_now():
    """The answers the snapshot already supports, with the reasoning printed."""
    hdr(6, "ANSWERS THE SNAPSHOT ALREADY SUPPORTS")
    live = sum(1 for r in L if r.get("is_live") is True)
    print(f"  Q1 total_listing_records : {len(L)}")
    print(f"  Q3 active_listings       : {live}   (is_live true)")
    print(f"     is_live values        : {dict(Counter(r.get('is_live') for r in L))}")

    loc = "guindy"
    sel = [r for r in RN if str(r.get("locality", "")).strip().lower() == loc]
    rent = sum(r["price"] for r in sel if isinstance(r.get("price"), (int, float)))
    print(f"\n  Q5 {loc} rentals        : {len(sel)} records, rent sum {rent:,d}")
    print(f"     rent range            : {min(r['price'] for r in sel):,d} .. "
          f"{max(r['price'] for r in sel):,d}")
    print(f"     any non-positive rent : "
          f"{sum(1 for r in sel if r['price'] <= 0)}")

    lo, hi = REFERENCE - timedelta(days=7), REFERENCE
    as_ist = sum(1 for r in L if (d := _parse_ist(r.get("posted_at"))) and lo <= d < hi)
    as_utc = 0
    for r in L:
        s = r.get("posted_at")
        if not isinstance(s, str):
            continue
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            continue
        d = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
        if lo <= d < hi:
            as_utc += 1
    print(f"\n  Q8 last 7 days           : {as_ist} if naive timestamps are IST")
    print(f"                             {as_utc} if naive timestamps are UTC")
    print(f"     posted_at formats      : "
          f"{dict(Counter('Z' if isinstance(r.get('posted_at'), str) and r['posted_at'].endswith('Z') else 'naive' for r in L))}")

    claimed = {p["project_id"]: p.get("total_listings") for p in P}
    all_c, live_c = Counter(), Counter()
    for r in L:
        pid = r.get("project_id")
        if pid:
            all_c[pid] += 1
            if r.get("is_live") is True:
                live_c[pid] += 1
    wrong_all = sum(1 for pid, c in claimed.items() if c != all_c.get(pid, 0))
    wrong_live = sum(1 for pid, c in claimed.items() if c != live_c.get(pid, 0))
    print(f"\n  Q10 wrong listing counts : {wrong_all} using all records")
    print(f"                             {wrong_live} using is_live only")
    print(f"      projects with no listings at all: "
          f"{sum(1 for pid in claimed if all_c.get(pid, 0) == 0)}")
    R["h6_answers"] = {"q1": len(L), "q3": live, "q5": rent, "q5_records": len(sel),
                       "q8_ist": as_ist, "q8_utc": as_utc,
                       "q10_all": wrong_all, "q10_live": wrong_live}


# ==========================================================================
if __name__ == "__main__":
    h1_project_price_units()
    h2_corrupt_vs_plots()
    h3_the_total_gap()
    h4_dedup_keys()
    h5_rental_deposit_units()
    h6_answers_now()
    (OUT / "probe2.json").write_text(json.dumps(R, indent=2, default=str))
    print(f"\nwritten: {OUT / 'probe2.json'}")
