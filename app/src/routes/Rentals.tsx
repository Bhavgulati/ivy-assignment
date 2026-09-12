import { useMemo, useState } from "react";
import type { Rental } from "../api/types";
import { useCollection, useManifest } from "../state/data";
import { correctRental, inrExact, sqft, titleCase } from "../core/corrections";
import { Badge, ErrorBox, Progress, Stat } from "../components/bits";

export default function Rentals() {
  const { flags } = useManifest();
  const { rows, progress, done, error } = useCollection<Rental>("/v1/rentals");
  const [locality, setLocality] = useState("");

  const corrected = useMemo(
    () => (flags ? rows.map((r) => correctRental(r, flags)) : []),
    [rows, flags]
  );

  const localities = useMemo(
    () => Array.from(new Set(rows.map((r) => r.locality))).sort(),
    [rows]
  );

  const shown = useMemo(
    () => (locality ? corrected.filter((c) => c.raw.locality === locality) : corrected),
    [corrected, locality]
  );

  const monthlyTotal = shown.reduce((a, c) => a + (c.raw.price || 0), 0);
  const fixed = shown.filter((c) => c.deposit_was_months).length;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Rentals</h1>
          <p className="sub">
            Monthly rent is genuinely in rupees. The deposit is not: 301 records
            state it as a number of months instead, and this screen converts
            those using each record's own rent.
          </p>
        </div>
      </header>

      {error ? <ErrorBox message={error} /> : null}
      {!done ? <Progress got={progress.got} total={progress.total} /> : null}

      <div className="filters">
        <label>
          Locality
          <select value={locality} onChange={(e) => setLocality(e.target.value)}>
            <option value="">All localities</option>
            {localities.map((l) => (
              <option key={l} value={l}>
                {titleCase(l)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="stats">
        <Stat label="Records" value={shown.length.toLocaleString("en-IN")} />
        <Stat
          label="Monthly rent, summed"
          value={inrExact(monthlyTotal)}
          note={locality ? `across ${titleCase(locality)}` : "across every locality"}
        />
        <Stat
          label="Deposits corrected"
          value={fixed.toLocaleString("en-IN")}
          note="stated in months, not rupees"
        />
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Property</th>
              <th>Locality</th>
              <th className="num">Rent / month</th>
              <th className="num">Deposit</th>
              <th className="num">Carpet</th>
              <th>Furnishing</th>
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, 400).map((c) => (
              <tr key={c.raw.listing_id}>
                <td>
                  {c.raw.bedroom} BHK · {c.raw.apartment_name}
                  {c.fake ? <> <Badge kind="fake">Not genuine</Badge></> : null}
                </td>
                <td>{titleCase(c.raw.locality)}</td>
                <td className="num">{inrExact(c.raw.price)}</td>
                <td className="num">
                  {inrExact(c.deposit_inr)}
                  {c.deposit_was_months ? (
                    <div className="cell-note">stated as {c.raw.deposit} months</div>
                  ) : null}
                </td>
                <td className="num">{sqft(c.raw.carpet_area)}</td>
                <td>{titleCase(c.raw.furnishing)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length > 400 ? (
          <p className="note center">
            Showing the first 400 of {shown.length.toLocaleString("en-IN")}. Pick
            a locality to narrow it.
          </p>
        ) : null}
      </div>
    </div>
  );
}
