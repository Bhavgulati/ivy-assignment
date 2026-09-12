import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import * as api from "../api/client";
import type { Listing } from "../api/types";
import { useManifest } from "../state/data";
import {
  correctListing, inr, inrExact, parseIst, sqft, titleCase,
} from "../core/corrections";
import type { CorrectedListing } from "../core/corrections";
import { useSaved } from "../state/saved";
import { Badge, ErrorBox, Spinner } from "../components/bits";

export default function ListingDetail() {
  const { id = "" } = useParams();
  const { manifest, flags } = useManifest();
  const saved = useSaved();
  const [row, setRow] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The documented path for a single listing is /v1/listing/{id}, singular.
  // That 404s. The plural form works.
  useEffect(() => {
    let alive = true;
    setRow(null);
    setError(null);
    api
      .get<Listing>(`/v1/listings/${encodeURIComponent(id)}`)
      .then((r) => alive && setRow(r))
      .catch((e) => alive && setError(e?.message ?? String(e)));
    return () => {
      alive = false;
    };
  }, [id]);

  if (error) return <div className="page"><ErrorBox message={error} /></div>;
  if (!row || !manifest || !flags) return <div className="page"><Spinner /></div>;

  const c: CorrectedListing = correctListing(row, manifest, flags);
  const posted = parseIst(row.posted_at);

  return (
    <div className="page detail">
      <Link to="/listings" className="back">← All listings</Link>

      <header className="detail-head">
        <div>
          <h1>
            {row.bedroom > 0 ? `${row.bedroom} BHK ` : ""}
            {titleCase(row.property_type)} in {titleCase(row.locality)}
          </h1>
          <p className="sub">{row.apartment_name}</p>
        </div>
        <button
          className={saved.has(row.listing_id) ? "save big on" : "save big"}
          onClick={() => saved.toggle(row.listing_id)}
        >
          {saved.has(row.listing_id) ? "★ Saved" : "☆ Save"}
        </button>
      </header>

      <div className="badges">
        {!row.is_live ? <Badge kind="off">Withdrawn</Badge> : <Badge kind="ok">Live</Badge>}
        {c.corrupt ? <Badge kind="corrupt">Impossible data</Badge> : null}
        {c.fake ? <Badge kind="fake">Not genuine</Badge> : null}
        {c.duplicate ? <Badge kind="dup">Also listed elsewhere</Badge> : null}
        {c.unitFixed ? <Badge kind="unit">Area corrected from square metres</Badge> : null}
      </div>

      {c.fake ? (
        <div className="warnbox">
          This listing matches the enquiry-farming pattern found in this
          dataset: its phone number carries a fully verified portfolio while the
          market runs at 60% verified, every one of its listings is priced below
          the local median for its size, and the same number posts under several
          different seller names. Treat the contact details below with caution —
          note that <code>is_verified</code> is <strong>true</strong> here, which
          is exactly why it is not a useful signal.
        </div>
      ) : null}

      {c.corrupt ? (
        <div className="warnbox">
          This record describes something that cannot exist, so it is shown for
          transparency rather than as an offer. It is one of 63 such records,
          which fall into seven disjoint groups of exactly nine.
        </div>
      ) : null}

      <div className="price big">{inr(row.price)}</div>
      <p className="sub">
        {inrExact(row.price)}
        {c.price_per_sqft
          ? ` · ${Math.round(c.price_per_sqft).toLocaleString("en-IN")} per sqft of carpet area`
          : ""}
      </p>

      <div className="specs">
        <Spec k="Carpet area" v={sqft(c.carpet_area)} note={c.unitFixed ? `stated as ${row.carpet_area} sq m` : undefined} />
        <Spec k="Super built-up" v={sqft(c.super_built_up_area)} />
        <Spec k="Bedrooms" v={row.bedroom > 0 ? String(row.bedroom) : "—"} />
        <Spec k="Bathrooms" v={row.bathroom > 0 ? String(row.bathroom) : "—"} />
        <Spec k="Balconies" v={String(row.balcony ?? 0)} />
        <Spec k="Floor" v={row.total_floors > 0 ? `${row.floor} of ${row.total_floors}` : "—"} />
        <Spec k="Facing" v={titleCase(row.facing_direction)} />
        <Spec k="Furnishing" v={titleCase(row.furnishing)} />
        <Spec k="Parking" v={String(row.covered_parking ?? 0)} />
        <Spec k="Posted by" v={`${row.posted_by_name} (${row.posted_by})`} />
        <Spec k="Posted at" v={posted ? posted.toLocaleString("en-IN") : "—"} note="stamp carries no timezone; read as IST" />
        <Spec k="Source" v={row.website} />
      </div>

      <h2>Description</h2>
      <p className="desc">{row.description || "No description provided."}</p>

      <h2>Contact</h2>
      <p className="desc">
        {row.posted_by_contact}
        {c.fake ? " — flagged above" : ""}
      </p>

      {row.project_id ? (
        <p className="note">
          Part of project <Link to="/projects">{row.project_id}</Link>.
        </p>
      ) : null}

      <p className="note">
        <a href={row.listing_url} target="_blank" rel="noreferrer">
          Original listing on {row.website}
        </a>
      </p>
    </div>
  );
}

function Spec({ k, v, note }: { k: string; v: string; note?: string }) {
  return (
    <div className="spec">
      <div className="spec-k">{k}</div>
      <div className="spec-v">{v}</div>
      {note ? <div className="spec-note">{note}</div> : null}
    </div>
  );
}
