import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import type { Listing } from "../api/types";
import { useManifest } from "../state/data";
import { correctListing, inr, sqft, titleCase } from "../core/corrections";
import { useSaved } from "../state/saved";
import { useAuth } from "../state/auth";
import { Empty, Spinner } from "../components/bits";

export default function Saved() {
  const { ids, remove } = useSaved();
  const { session } = useAuth();
  const { manifest, flags } = useManifest();
  const [rows, setRows] = useState<Listing[]>([]);
  const [busy, setBusy] = useState(false);

  // Saved ids are local; the records are fetched fresh so a price or status
  // change since saving is visible rather than cached from whenever it was
  // saved.
  useEffect(() => {
    let alive = true;
    if (ids.length === 0) {
      setRows([]);
      return;
    }
    setBusy(true);
    Promise.all(
      ids.map((id) =>
        api.get<Listing>(`/v1/listings/${encodeURIComponent(id)}`).catch(() => null)
      )
    )
      .then((rs) => alive && setRows(rs.filter((r): r is Listing => !!r)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [ids]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Saved listings</h1>
          <p className="sub">
            {ids.length} saved for {session?.email}. Kept per account, so these
            survive a reload and a re-login.
          </p>
        </div>
      </header>

      <p className="note">
        The documented <code>/v1/favourites</code> endpoint returns 404 for GET,
        POST and DELETE, so there is no server to save to and this list lives in
        this browser. The consequence worth knowing: the same account on another
        device starts empty.
      </p>

      {busy ? <Spinner label="Loading saved listings" /> : null}

      {!busy && ids.length === 0 ? (
        <Empty>
          Nothing saved yet. Star a listing from <Link to="/listings">the list</Link>.
        </Empty>
      ) : null}

      <div className="cards">
        {manifest && flags
          ? rows.map((r) => {
              const c = correctListing(r, manifest, flags);
              return (
                <article key={r.listing_id} className="card">
                  <div className="card-top">
                    <Link to={`/listings/${r.listing_id}`} className="card-title">
                      {r.bedroom > 0 ? `${r.bedroom} BHK ` : ""}
                      {titleCase(r.property_type)} in {titleCase(r.locality)}
                    </Link>
                    <button className="save on" onClick={() => remove(r.listing_id)}>
                      ✕
                    </button>
                  </div>
                  <div className="price">{inr(r.price)}</div>
                  <div className="meta">
                    {r.apartment_name} · {sqft(c.carpet_area)} carpet
                  </div>
                  {!r.is_live ? (
                    <div className="meta dim">This listing has since been withdrawn.</div>
                  ) : null}
                </article>
              );
            })
          : null}
      </div>
    </div>
  );
}
