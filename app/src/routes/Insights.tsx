import { useMemo } from "react";
import type { Listing } from "../api/types";
import { useCollection, useManifest } from "../state/data";
import { correctListing, inr, inrExact, parseIst, titleCase } from "../core/corrections";
import { ErrorBox, Progress, Stat } from "../components/bits";

// The documented /v1/analytics/summary is a 404, so every figure here is
// computed in the browser from the full collection. That is eighty-two requests
// at the API's real page size of fifty, which is why the screen reports progress
// and fills in as records arrive.
//
// The second half of the screen is the part the brief actually asks for: what a
// user would want to know about this data that the API does not tell them.

function median(xs: number[]) {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export default function Insights() {
  const { manifest, flags } = useManifest();
  const { rows, progress, done, error } = useCollection<Listing>("/v1/listings");

  const c = useMemo(
    () => (manifest && flags ? rows.map((r) => correctListing(r, manifest, flags)) : []),
    [rows, manifest, flags]
  );

  const stats = useMemo(() => {
    if (!c.length) return null;
    const live = c.filter((x) => x.raw.is_live);
    const clean = live.filter((x) => !x.corrupt && !x.fake);
    const prices = clean.map((x) => x.raw.price).filter((p) => p > 0);
    const pps = clean.map((x) => x.price_per_sqft).filter((v): v is number => !!v);

    const byLocality = new Map<string, { n: number; prices: number[] }>();
    for (const x of clean) {
      const k = x.raw.locality;
      const e = byLocality.get(k) ?? { n: 0, prices: [] };
      e.n += 1;
      if (x.raw.price > 0) e.prices.push(x.raw.price);
      byLocality.set(k, e);
    }
    const byBhk = new Map<number, number>();
    for (const x of clean) byBhk.set(x.raw.bedroom, (byBhk.get(x.raw.bedroom) ?? 0) + 1);

    const reference = manifest ? new Date(manifest.reference_moment) : new Date();
    const weekAgo = new Date(reference.getTime() - 7 * 86400000);
    const lastWeek = c.filter((x) => {
      const d = parseIst(x.raw.posted_at);
      return d && d >= weekAgo && d < reference;
    }).length;

    const twoBhk = clean.filter((x) => x.raw.bedroom === 2 && x.price_per_sqft);

    return {
      total: c.length,
      live: live.length,
      withdrawn: c.length - live.length,
      corrupt: c.filter((x) => x.corrupt).length,
      fake: c.filter((x) => x.fake).length,
      duplicate: c.filter((x) => x.duplicate).length,
      unitFixed: c.filter((x) => x.unitFixed).length,
      medianPrice: median(prices),
      medianPps: median(pps),
      meanPps2Bhk:
        twoBhk.reduce((a, x) => a + (x.price_per_sqft ?? 0), 0) / (twoBhk.length || 1),
      byLocality: [...byLocality.entries()]
        .map(([locality, e]) => ({ locality, n: e.n, median: median(e.prices) }))
        .sort((a, b) => b.n - a.n),
      byBhk: [...byBhk.entries()].filter(([b]) => b > 0).sort((a, b) => a[0] - b[0]),
      lastWeek,
    };
  }, [c, manifest]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Insights</h1>
          <p className="sub">
            The documented <code>/v1/analytics/summary</code> returns 404, so
            everything below is computed in the browser from the whole
            collection.
          </p>
        </div>
      </header>

      {error ? <ErrorBox message={error} /> : null}
      {!done ? <Progress got={progress.got} total={progress.total} /> : null}

      {stats ? (
        <>
          <h2>What the analytics endpoint was supposed to give</h2>
          <div className="stats">
            <Stat label="Listing records" value={stats.total.toLocaleString("en-IN")} note="paged to has_more=false" />
            <Stat label="Median price" value={inr(stats.medianPrice)} note="live, excluding flagged" />
            <Stat label="Median per sqft" value={inrExact(Math.round(stats.medianPps))} note="on corrected carpet area" />
            <Stat label="Live listings" value={stats.live.toLocaleString("en-IN")} />
          </div>

          <div className="two-col">
            <section>
              <h3>By locality</h3>
              <table>
                <thead>
                  <tr><th>Locality</th><th className="num">Listings</th><th className="num">Median price</th></tr>
                </thead>
                <tbody>
                  {stats.byLocality.map((l) => (
                    <tr key={l.locality}>
                      <td>{titleCase(l.locality)}</td>
                      <td className="num">{l.n}</td>
                      <td className="num">{inr(l.median)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section>
              <h3>By bedroom count</h3>
              <table>
                <thead><tr><th>Size</th><th className="num">Listings</th></tr></thead>
                <tbody>
                  {stats.byBhk.map(([b, n]) => (
                    <tr key={b}><td>{b} BHK</td><td className="num">{n}</td></tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>

          <h2>What the documentation did not mention</h2>
          <p className="sub">
            Each of these came out of comparing the reference against the running
            service. They are the reason the numbers above differ from what a
            client written to the documentation would show.
          </p>

          <div className="findings-grid">
            <Finding
              title="The server's own count is short by 8.6%"
              body={`Paging to exhaustion returns ${stats.total.toLocaleString("en-IN")} records. The envelope's total says 3,749. The documented procedure — read total, divide by your limit, request that many pages — loses 351 listings, and the same shortfall holds under every filter tried.`}
            />
            <Finding
              title={`${stats.withdrawn.toLocaleString("en-IN")} withdrawn listings are served anyway`}
              body="The reference says inactive, expired and withdrawn listings are excluded server side and anything returned is safe to show a user. An undocumented is_live field says otherwise, and the is_live query parameter is accepted and ignored, so filtering has to happen here."
            />
            <Finding
              title={`${stats.unitFixed} records state area in square metres`}
              body="Area is documented as square feet everywhere. The two groups do not overlap: metres run 34 to 197, feet start at 206. Leaving them alone puts rupees per square foot 63.7% too high."
            />
            <Finding
              title={`${stats.duplicate.toLocaleString("en-IN")} records are copies of a property already listed`}
              body="The reference says each listing is exactly one physical property. The same flat is syndicated across five portals with jittered coordinates and a re-typed building name, leaving 3,117 distinct properties behind 4,100 records."
            />
            <Finding
              title={`${stats.corrupt} records describe something impossible`}
              body="Negative prices, carpet area larger than super built-up, floors above the building's height, coordinates outside the city, and future-dated posts — in seven disjoint groups of exactly nine."
            />
            <Finding
              title={`${stats.fake} listings exist to farm enquiries`}
              body="Seven phone numbers, each with a fully verified portfolio while the market runs at 60% verified, every listing priced below its local median, and each number posting under several different seller names. is_verified is true for all of them, which is what makes it useless as a signal."
            />
            <Finding
              title="Timestamps carry no timezone, and the two collections disagree"
              body={`Listings use naive ISO with no offset; rentals use a Z suffix. /health reports Asia/Kolkata, so the naive stamps are IST. Reading them as UTC changes the seven-day count from ${stats.lastWeek} to 106.`}
            />
            <Finding
              title="Mean price per sqft for a live 2 BHK"
              body={`${inrExact(Math.round(stats.meanPps2Bhk))} per sqft, after converting the square-metre records and excluding the impossible and non-genuine ones. Skipping any of those three corrections moves this number by more than 60%.`}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}

function Finding({ title, body }: { title: string; body: string }) {
  return (
    <article className="finding">
      <h4>{title}</h4>
      <p>{body}</p>
    </article>
  );
}
