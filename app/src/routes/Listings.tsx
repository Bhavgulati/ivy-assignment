import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import type { Envelope, Listing } from "../api/types";
import { useManifest } from "../state/data";
import { correctListing, inr, postedAgo, sqft, titleCase } from "../core/corrections";
import type { CorrectedListing } from "../core/corrections";
import { useSaved } from "../state/saved";
import { Badge, Empty, ErrorBox, Spinner } from "../components/bits";

interface Filters {
  locality: string;
  bhk: string;
  min_price: string;
  max_price: string;
  furnishing: string;
  liveOnly: boolean;
  hideFlagged: boolean;
}

const EMPTY: Filters = {
  locality: "",
  bhk: "",
  min_price: "",
  max_price: "",
  furnishing: "",
  liveOnly: true,
  hideFlagged: true,
};

export default function Listings() {
  const { manifest, flags, error: mErr } = useManifest();
  const saved = useSaved();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [rows, setRows] = useState<Listing[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localities, setLocalities] = useState<string[]>([]);

  // /v1/localities is not in the documentation and is the only authoritative
  // list of spellings. The locality filter is an exact match, so a typed
  // free-text box would silently return nothing.
  useEffect(() => {
    api
      .get<{ results: Array<string | { locality: string }> }>("/v1/localities")
      .then((r) =>
        setLocalities(
          (r.results ?? [])
            .map((x) => (typeof x === "string" ? x : x.locality))
            .filter(Boolean)
            .sort()
        )
      )
      .catch(() => setLocalities([]));
  }, []);

  // Server-side params that were verified to actually filter. bedroom,
  // project_id, is_live, is_verified, website, posted_by, min_area, max_area, q
  // and search are all accepted and ignored, so none of them are sent - asking
  // for a filter the server drops would show the user an unfiltered list.
  const serverParams = useMemo(
    () => ({
      locality: filters.locality || undefined,
      bhk: filters.bhk || undefined,
      min_price: filters.min_price || undefined,
      max_price: filters.max_price || undefined,
      furnishing: filters.furnishing || undefined,
    }),
    [filters.locality, filters.bhk, filters.min_price, filters.max_price, filters.furnishing]
  );

  useEffect(() => {
    setRows([]);
    setOffset(0);
    setHasMore(true);
  }, [serverParams]);

  useEffect(() => {
    let alive = true;
    if (!hasMore && offset > 0) return;
    setBusy(true);
    setError(null);
    api
      .page<Listing>("/v1/listings", offset, serverParams)
      .then((env: Envelope<Listing>) => {
        if (!alive) return;
        setRows((prev) => (offset === 0 ? env.results : [...prev, ...env.results]));
        setTotal(env.total);
        setHasMore(env.has_more);
      })
      .catch((e) => alive && setError(e?.message ?? String(e)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [offset, serverParams, hasMore]);

  // is_live is not a working server filter, and the flags come from the local
  // manifest, so both of these have to be applied here.
  const shown: CorrectedListing[] = useMemo(() => {
    if (!manifest || !flags) return [];
    let out = rows.map((r) => correctListing(r, manifest, flags));
    if (filters.liveOnly) out = out.filter((c) => c.raw.is_live);
    if (filters.hideFlagged) out = out.filter((c) => !c.corrupt && !c.fake && !c.duplicate);
    return out;
  }, [rows, manifest, flags, filters.liveOnly, filters.hideFlagged]);

  const hidden = rows.length - shown.length;

  if (mErr) return <ErrorBox message={`corrections manifest failed to load: ${mErr}`} />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Sale listings</h1>
          <p className="sub">
            {rows.length.toLocaleString("en-IN")} fetched
            {total ? ` — the server's total says ${total.toLocaleString("en-IN")}, which under-reports by about 8.6%` : ""}
          </p>
        </div>
      </header>

      <div className="filters">
        <label>
          Locality
          <select
            value={filters.locality}
            onChange={(e) => setFilters({ ...filters, locality: e.target.value })}
          >
            <option value="">Any</option>
            {localities.map((l) => (
              <option key={l} value={l}>
                {titleCase(l)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Bedrooms
          <select value={filters.bhk} onChange={(e) => setFilters({ ...filters, bhk: e.target.value })}>
            <option value="">Any</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {n} BHK
              </option>
            ))}
          </select>
        </label>
        <label>
          Min price
          <select
            value={filters.min_price}
            onChange={(e) => setFilters({ ...filters, min_price: e.target.value })}
          >
            <option value="">No minimum</option>
            {[2500000, 5000000, 7500000, 10000000, 15000000, 20000000].map((v) => (
              <option key={v} value={v}>
                {inr(v)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Max price
          <select
            value={filters.max_price}
            onChange={(e) => setFilters({ ...filters, max_price: e.target.value })}
          >
            <option value="">No maximum</option>
            {[5000000, 10000000, 15000000, 20000000, 30000000].map((v) => (
              <option key={v} value={v}>
                {inr(v)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Furnishing
          <select
            value={filters.furnishing}
            onChange={(e) => setFilters({ ...filters, furnishing: e.target.value })}
          >
            <option value="">Any</option>
            {["unfurnished", "semi-furnished", "fully-furnished"].map((f) => (
              <option key={f} value={f}>
                {titleCase(f)}
              </option>
            ))}
          </select>
        </label>
        <div className="toggles">
          <label className="check">
            <input
              type="checkbox"
              checked={filters.liveOnly}
              onChange={(e) => setFilters({ ...filters, liveOnly: e.target.checked })}
            />
            Live only
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={filters.hideFlagged}
              onChange={(e) => setFilters({ ...filters, hideFlagged: e.target.checked })}
            />
            Hide flagged
          </label>
          <button className="ghost" onClick={() => setFilters(EMPTY)}>
            Reset
          </button>
        </div>
      </div>

      {hidden > 0 ? (
        <p className="note">
          {hidden} of the {rows.length} fetched records are hidden by the two
          toggles above: withdrawn listings the endpoint returns anyway, plus
          records flagged impossible, not genuine, or a duplicate of another
          property.
        </p>
      ) : null}

      {error ? <ErrorBox message={error} /> : null}

      <div className="cards">
        {shown.map((c) => (
          <article key={c.raw.listing_id} className="card">
            <div className="card-top">
              <Link to={`/listings/${c.raw.listing_id}`} className="card-title">
                {c.raw.bedroom > 0 ? `${c.raw.bedroom} BHK ` : ""}
                {titleCase(c.raw.property_type)} in {titleCase(c.raw.locality)}
              </Link>
              <button
                className={saved.has(c.raw.listing_id) ? "save on" : "save"}
                onClick={() => saved.toggle(c.raw.listing_id)}
                aria-label="Save listing"
              >
                {saved.has(c.raw.listing_id) ? "★" : "☆"}
              </button>
            </div>
            <div className="price">{inr(c.raw.price)}</div>
            <div className="meta">
              {c.raw.apartment_name} · {sqft(c.carpet_area)} carpet ·{" "}
              {titleCase(c.raw.furnishing)}
            </div>
            <div className="meta dim">
              {c.price_per_sqft ? `${Math.round(c.price_per_sqft).toLocaleString("en-IN")} /sqft · ` : ""}
              posted {postedAgo(c.raw.posted_at)} · {c.raw.website}
            </div>
            <div className="badges">
              {!c.raw.is_live ? <Badge kind="off" title="The endpoint returns withdrawn listings despite the documentation">Withdrawn</Badge> : null}
              {c.corrupt ? <Badge kind="corrupt" title="Describes something that cannot exist">Impossible data</Badge> : null}
              {c.fake ? <Badge kind="fake" title="Matches the enquiry-farming pattern">Not genuine</Badge> : null}
              {c.duplicate ? <Badge kind="dup" title="The same property is listed on other portals too">Duplicate</Badge> : null}
              {c.unitFixed ? <Badge kind="unit" title="Area was stated in square metres and has been converted">Area corrected</Badge> : null}
            </div>
          </article>
        ))}
      </div>

      {busy ? <Spinner label="Fetching listings" /> : null}
      {!busy && shown.length === 0 ? (
        <Empty>
          Nothing matches. Try clearing a filter, or turn off "hide flagged" to
          see records that were filtered out for data-quality reasons.
        </Empty>
      ) : null}

      {hasMore && !busy ? (
        <button className="more" onClick={() => setOffset(rows.length)}>
          Load more
        </button>
      ) : null}
      {!hasMore && rows.length > 0 ? (
        <p className="note center">
          End of the collection — reached by paging on offset until has_more went
          false, not by dividing the server's total.
        </p>
      ) : null}
    </div>
  );
}
