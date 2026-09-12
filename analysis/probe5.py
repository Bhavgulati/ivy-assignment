"""
Fourth round. Closes Q2, Q9, Q7 and Q6.

Corrections carried in from probe4:

* My probe3 test of the project price units allowed a 0.5x to 2.0x band. That is
  wide enough that "98.1% of listings fall in range" carried no information, and
  it let me announce a wrong answer for Q7. Here the multiplier is measured
  directly - listings_max / price_max per project - and its distribution decides
  the unit instead of a tolerance I picked.

* Duplicates do not share posted_by_contact (0.2% of close pairs) and agree on
  apartment_name only about half the time. So the building name is not usable as
  a dedup key, and the phone-number hint in the brief belongs to fraud, not
  duplicates. Dedup here is geometric: distance, then the attributes a copy
  preserves.

    python3 probe5.py
"""

from __future__ import annotations

import json
import math
import statistics as st
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

SQFT_PER_SQM = 10.7639
SQM_CUTOFF = 200
M_PER_DEG_LAT = 110_600
M_PER_DEG_LON = 108_500
OUT = Path("out")
R: dict = {}

RAW = [json.loads(l) for l in (OUT / "listings.jsonl").read_text().splitlines() if l.strip()]
P = [json.loads(l) for l in (OUT / "projects.jsonl").read_text().splitlines() if l.strip()]
CORRUPT = set(json.loads((OUT / "probe3.json").read_text())["nine_sweep"]["nine_union"])


def hdr(n, t):
    print(f"\n{'=' * 70}\n{n}. {t}\n{'=' * 70}")


def norm(s):
    return " ".join(str(s or "").strip().lower().split())


L = []
for r in RAW:
    d = dict(r)
    if isinstance(d.get("carpet_area"), (int, float)) and 0 < d["carpet_area"] < SQM_CUTOFF:
        for f in ("carpet_area", "super_built_up_area"):
            if isinstance(d.get(f), (int, float)):
                d[f] = d[f] * SQFT_PER_SQM
        d["_sqm_fixed"] = True
    L.append(d)
print(f"listings {len(L)}  (square-metre records normalised: "
      f"{sum(1 for r in L if r.get('_sqm_fixed'))})")
print(f"corrupt set from probe3: {len(CORRUPT)}")


# ==========================================================================
def pairs_under(max_m=300):
    blocks = defaultdict(list)
    for r in L:
        if not isinstance(r.get("latitude"), (int, float)):
            continue
        gy = int(r["latitude"] * M_PER_DEG_LAT // 200)
        gx = int(r["longitude"] * M_PER_DEG_LON // 200)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                blocks[(gy + dy, gx + dx, r.get("bedroom"))].append(r)
    seen, out = set(), []
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
            if dm > max_m:
                continue
            ca, cb = a.get("carpet_area") or 0, b.get("carpet_area") or 0
            out.append({
                "a": a["listing_id"], "b": b["listing_id"], "m": dm,
                "carpet_ok": ca > 0 and cb > 0 and abs(ca - cb) / max(ca, cb) <= 0.02,
                "same_floor": a.get("floor") == b.get("floor"),
                "same_facing": norm(a.get("facing_direction")) == norm(b.get("facing_direction")),
                "same_locality": norm(a.get("locality")) == norm(b.get("locality")),
                "same_site": a.get("website") == b.get("website"),
            })
    return out


def a_dedup():
    """One rule, swept over distance. The count should plateau, and the plateau
    is the answer - not whichever threshold lands on a number I was hoping for.

    Blocking here is a 200 m grid on raw coordinates plus bedroom, with
    neighbouring cells included, so locality spelling cannot hide a duplicate.
    """
    hdr("A", "DEDUP: DISTANCE SWEEP  (Q2)")
    pr = pairs_under(300)
    print(f"  pairs within 300 m: {len(pr):,d}")

    def clusters_at(limit, require_floor=True, require_facing=True):
        parent = {r["listing_id"]: r["listing_id"] for r in L}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for p in pr:
            if p["m"] > limit or not p["carpet_ok"]:
                continue
            if require_floor and not p["same_floor"]:
                continue
            if require_facing and not p["same_facing"]:
                continue
            ra, rb = find(p["a"]), find(p["b"])
            if ra != rb:
                parent[ra] = rb
        groups = Counter(find(k) for k in parent)
        return len(groups), Counter(groups.values())

    print(f"\n  rule: carpet within 2%, same floor, same facing, same bedroom")
    print(f"  {'max distance':>14s} {'properties':>11s} {'absorbed':>9s}  size histogram")
    rows = []
    for limit in (10, 25, 50, 75, 100, 125, 150, 200, 250, 300):
        n, sizes = clusters_at(limit)
        rows.append({"limit_m": limit, "properties": n, "absorbed": len(L) - n,
                     "sizes": dict(sorted(sizes.items()))})
        print(f"  {limit:>12d} m {n:>11d} {len(L) - n:>9d}  {dict(sorted(sizes.items()))}")

    print("\n  same sweep without requiring floor and facing to match:")
    for limit in (50, 100, 150, 250):
        n, _ = clusters_at(limit, False, False)
        print(f"  {limit:>12d} m {n:>11d} {len(L) - n:>9d}")

    close = [p for p in pr if p["m"] <= 100 and p["carpet_ok"]
             and p["same_floor"] and p["same_facing"]]
    print(f"\n  matched pairs at 100 m: {len(close)}")
    print(f"      cross-locality: {sum(1 for p in close if not p['same_locality'])}")
    print(f"      cross-website : {sum(1 for p in close if not p['same_site'])}")
    R["dedup_sweep"] = rows
    return clusters_at(100)[0]


# ==========================================================================
def b_fraud():
    """Profile every contact, then look for a group that stands apart.

    The tell from probe4 was not volume. It was a contact whose entire
    portfolio is verified while the market runs at 60%, combined with prices
    well below the locality and bedroom median. Rank on both and see whether a
    group separates from the rest.
    """
    hdr("B", "FRAUD: PER-CONTACT PROFILE  (Q9)")
    base = defaultdict(list)
    for r in L:
        if r["listing_id"] in CORRUPT:
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
        if r["listing_id"] not in CORRUPT and r.get("posted_by_contact"):
            g[str(r["posted_by_contact"]).strip()].append(r)

    prof = []
    for c, rs in g.items():
        ch = [x for x in (cheap(r) for r in rs) if x is not None]
        prof.append({
            "contact": c, "n": len(rs),
            "verified_frac": sum(1 for r in rs if r.get("is_verified") is True) / len(rs),
            "live_frac": sum(1 for r in rs if r.get("is_live") is True) / len(rs),
            "median_cheap": st.median(ch) if ch else None,
            "max_cheap": max(ch) if ch else None,
            "localities": len({norm(r.get("locality")) for r in rs}),
            "names": len({r.get("posted_by_name") for r in rs}),
            "posted_by": sorted({str(r.get("posted_by")) for r in rs}),
            "ids": sorted(r["listing_id"] for r in rs),
        })
    print(f"  contacts: {len(prof)};  n>=5: {sum(1 for p in prof if p['n'] >= 5)}")
    print(f"  market verified fraction: "
          f"{sum(1 for r in L if r.get('is_verified') is True) / len(L):.3f}")

    print(f"\n  contacts with n>=5, ranked by median cheapness (lowest first):")
    print(f"      {'contact':17s} {'n':>4s} {'ver':>6s} {'live':>6s} "
          f"{'medChp':>7s} {'maxChp':>7s} {'loc':>4s} {'names':>6s}")
    big = [p for p in prof if p["n"] >= 5 and p["median_cheap"] is not None]
    big.sort(key=lambda p: p["median_cheap"])
    for p in big[:20]:
        print(f"      {p['contact']:17s} {p['n']:>4d} {p['verified_frac']:>6.2f} "
              f"{p['live_frac']:>6.2f} {p['median_cheap']:>7.3f} "
              f"{p['max_cheap']:>7.3f} {p['localities']:>4d} {p['names']:>6d}")

    print(f"\n  for contrast, the same table for the 8 largest portfolios:")
    for p in sorted(prof, key=lambda p: -p["n"])[:8]:
        print(f"      {p['contact']:17s} {p['n']:>4d} {p['verified_frac']:>6.2f} "
              f"{p['live_frac']:>6.2f} "
              f"{(p['median_cheap'] if p['median_cheap'] is not None else 0):>7.3f}")

    # the combined rule, at a few thresholds, so the boundary is visible
    print(f"\n  contacts that are fully verified AND priced below the market:")
    for vt, ct in ((1.0, 0.6), (1.0, 0.7), (1.0, 0.8), (0.95, 0.7)):
        ring = [p for p in prof if p["n"] >= 3 and p["verified_frac"] >= vt
                and p["median_cheap"] is not None and p["median_cheap"] < ct]
        ids = sorted(i for p in ring for i in p["ids"])
        print(f"      verified>={vt}, medianCheap<{ct}: {len(ring)} contacts, "
              f"{len(ids)} listings")
        R.setdefault("fraud_rules", {})[f"v{vt}_c{ct}"] = {
            "contacts": [p["contact"] for p in ring], "n_listings": len(ids),
            "ids": ids}

    best = R["fraud_rules"].get("v1.0_c0.7", {})
    if best.get("ids"):
        print(f"\n  candidate fake set ({len(best['ids'])} listings, "
              f"{len(best['contacts'])} contacts):")
        print(f"      contacts: {best['contacts']}")
        print(f"      listing_ids: {best['ids']}")
    R["fraud_profiles"] = [p for p in big[:25]]
    return set(best.get("ids") or [])


# ==========================================================================
def c_project_units():
    """Measure the multiplier instead of assuming it.

    For every project with listings, listings_max / price_max is the factor that
    would make the field agree with reality. If the field is in one unit, that
    ratio clusters at one value. If it clusters at two, the field carries two
    units and the records in the smaller cluster are the interesting ones.
    """
    hdr("C", "PROJECT PRICE UNITS  (Q7)  -  measured, not assumed")
    by_pid = defaultdict(list)
    for r in L:
        if r.get("project_id") and (r.get("price") or 0) > 0 and r["listing_id"] not in CORRUPT:
            by_pid[r["project_id"]].append(r["price"])

    rows = []
    for p in P:
        pr = by_pid.get(p.get("project_id")) or []
        if not pr or not isinstance(p.get("price_max"), (int, float)) or p["price_max"] <= 0:
            continue
        rows.append({
            "project_id": p["project_id"], "price_min": p.get("price_min"),
            "price_max": p["price_max"], "listings": len(pr),
            "lmax": max(pr), "lmin": min(pr),
            "mult_max": max(pr) / p["price_max"],
            "mult_min": (min(pr) / p["price_min"]) if p.get("price_min") else None,
            "ratio_raw": p["price_max"] / p["price_min"] if p.get("price_min") else None,
        })
    print(f"  projects with listings: {len(rows)}")

    print(f"\n  log10(listings_max / price_max) histogram:")
    h = Counter(round(math.log10(r["mult_max"]), 1) for r in rows)
    for k in sorted(h):
        print(f"      10^{k:<5.1f} {h[k]:5d}  {'#' * min(60, h[k] // 2)}")
    print(f"\n  log10(listings_min / price_min) histogram:")
    h2 = Counter(round(math.log10(r["mult_min"]), 1) for r in rows if r["mult_min"])
    for k in sorted(h2):
        print(f"      10^{k:<5.1f} {h2[k]:5d}  {'#' * min(60, h2[k] // 2)}")

    def med(vals, fmt=",.0f"):
        vals = [v for v in vals if v is not None]
        return format(st.median(vals), fmt) if vals else "n/a"

    two_digit = [r for r in rows if r["price_max"] >= 10]
    one_digit = [r for r in rows if r["price_max"] < 10]
    print(f"\n  price_max >= 10 : {len(two_digit)} projects, "
          f"median multiplier {med(r['mult_max'] for r in two_digit)}")
    print(f"  price_max <  10 : {len(one_digit)} projects, "
          f"median multiplier {med(r['mult_max'] for r in one_digit)}")
    print(f"\n  raw price_max/price_min ratio:")
    print(f"      overall          {med((r['ratio_raw'] for r in rows), '.4f')}")
    print(f"      price_max>=10    {med((r['ratio_raw'] for r in two_digit), '.4f')}")
    print(f"      price_max<10     {med((r['ratio_raw'] for r in one_digit), '.4f')}")
    print(f"\n  all projects with price_max >= 10 ({len([p for p in P if isinstance(p.get('price_max'), (int, float)) and p['price_max'] >= 10])} total):")
    for p in sorted((p for p in P if isinstance(p.get("price_max"), (int, float))
                     and p["price_max"] >= 10), key=lambda p: -p["price_max"])[:30]:
        pr = by_pid.get(p["project_id"]) or []
        print(f"      {p['project_id']:9s} min={p.get('price_min'):>7} "
              f"max={p['price_max']:>7} listings={len(pr):>3d} "
              f"their_max={max(pr) if pr else 0:>12,d} status={p.get('project_status')}")

    print(f"\n  highest price_max among price_max < 10:")
    for p in sorted((p for p in P if isinstance(p.get("price_max"), (int, float))
                     and p["price_max"] < 10), key=lambda p: -p["price_max"])[:5]:
        pr = by_pid.get(p["project_id"]) or []
        print(f"      {p['project_id']:9s} min={p.get('price_min'):>7} "
              f"max={p['price_max']:>7} listings={len(pr):>3d} "
              f"their_max={max(pr) if pr else 0:>12,d}")
    R["project_units"] = {"log10_mult_max": {str(k): v for k, v in sorted(h.items())},
                          "log10_mult_min": {str(k): v for k, v in sorted(h2.items())},
                          "two_digit_n": len(two_digit), "one_digit_n": len(one_digit)}


# ==========================================================================
def d_q6(fake_ids):
    hdr("D", "Q6 FINAL  (excluding corrupt and fake)")
    for label, excl in (("nothing excluded", set()),
                        ("corrupt only", CORRUPT),
                        ("corrupt + fake", CORRUPT | fake_ids)):
        sel = [r for r in L if r.get("is_live") is True and r.get("bedroom") == 2
               and r["listing_id"] not in excl
               and (r.get("carpet_area") or 0) > 0 and (r.get("price") or 0) > 0]
        pps = [r["price"] / r["carpet_area"] for r in sel]
        print(f"  {label:20s} n={len(sel):5d}  mean {st.fmean(pps):9.2f}  "
              f"median {st.median(pps):9.2f}")
    sel = [r for r in L if r.get("is_live") is True and r.get("bedroom") == 2
           and r["listing_id"] not in (CORRUPT | fake_ids)
           and (r.get("carpet_area") or 0) > 0 and (r.get("price") or 0) > 0]
    pps = [r["price"] / r["carpet_area"] for r in sel]
    R["q6"] = {"n": len(sel), "mean": round(st.fmean(pps), 2)}
    print(f"\n  Q6 avg_price_per_sqft_2bhk = {st.fmean(pps):.2f}")


# ==========================================================================
if __name__ == "__main__":
    q2 = a_dedup()
    fake = b_fraud()
    c_project_units()
    d_q6(fake)
    R["q2_at_100m"] = q2
    (OUT / "probe5.json").write_text(json.dumps(R, indent=2, default=str))
    print(f"\nwritten: {OUT / 'probe5.json'}")
