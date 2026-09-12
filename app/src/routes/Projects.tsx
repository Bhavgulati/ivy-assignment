import { useMemo, useState } from "react";
import type { Project } from "../api/types";
import { useCollection, useManifest } from "../state/data";
import { correctProject, inr, inrExact, titleCase } from "../core/corrections";
import { Badge, ErrorBox, Progress, Stat } from "../components/bits";

type Sort = "price_max" | "price_min" | "units" | "name";

export default function Projects() {
  const { manifest, flags } = useManifest();
  const { rows, progress, done, error } = useCollection<Project>("/v1/projects");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<Sort>("price_max");

  const corrected = useMemo(
    () => (manifest && flags ? rows.map((r) => correctProject(r, manifest, flags)) : []),
    [rows, manifest, flags]
  );

  const shown = useMemo(() => {
    let out = status ? corrected.filter((c) => c.raw.project_status === status) : corrected;
    out = [...out].sort((a, b) => {
      if (sort === "name") return a.raw.apartment_name.localeCompare(b.raw.apartment_name);
      if (sort === "units") return b.raw.total_units - a.raw.total_units;
      if (sort === "price_min") return (a.price_min_inr ?? 0) - (b.price_min_inr ?? 0);
      return (b.price_max_inr ?? 0) - (a.price_max_inr ?? 0);
    });
    return out;
  }, [corrected, status, sort]);

  const wrongCount = corrected.filter((c) => c.listing_count_wrong).length;
  const costliest = shown.length && sort === "price_max" ? shown[0] : null;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Projects</h1>
          <p className="sub">
            Prices here are converted. The API stores them in Indian display
            units — a value below 10 means crores, 10 or above means lakhs —
            while the reference calls them rupees. Read literally, price_min
            exceeds price_max for 357 of 460 projects.
          </p>
        </div>
      </header>

      {error ? <ErrorBox message={error} /> : null}
      {!done ? <Progress got={progress.got} total={progress.total} /> : null}

      <div className="stats">
        <Stat label="Projects" value={shown.length.toLocaleString("en-IN")} />
        <Stat
          label="Costliest by maximum price"
          value={costliest ? inr(costliest.price_max_inr) : "—"}
          note={costliest ? costliest.raw.project_id : undefined}
        />
        <Stat
          label="Wrong listing counts"
          value={wrongCount.toLocaleString("en-IN")}
          note="total_listings disagrees with the live listings served"
        />
      </div>

      <div className="filters">
        <label>
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            {["ready to move", "under construction", "new launch"].map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Sort by
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="price_max">Highest maximum price</option>
            <option value="price_min">Lowest entry price</option>
            <option value="units">Most units</option>
            <option value="name">Name</option>
          </select>
        </label>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Project</th>
              <th>Locality</th>
              <th>Status</th>
              <th className="num">Price range</th>
              <th className="num">Units</th>
              <th className="num">Listings claimed</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.raw.project_id}>
                <td>
                  {c.raw.apartment_name}
                  <div className="cell-note">
                    {c.raw.developer_name} · {c.raw.project_id}
                  </div>
                </td>
                <td>{titleCase(c.raw.locality)}</td>
                <td>{titleCase(c.raw.project_status)}</td>
                <td className="num">
                  {inr(c.price_min_inr)} – {inr(c.price_max_inr)}
                  <div className="cell-note">
                    stored as {c.raw.price_min} / {c.raw.price_max}
                  </div>
                </td>
                <td className="num">{c.raw.total_units?.toLocaleString("en-IN")}</td>
                <td className="num">
                  {c.raw.total_listings}
                  {c.listing_count_wrong ? (
                    <> <Badge kind="off" title="Disagrees with the live listings the API serves for this project">wrong</Badge></>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="note">
        The costliest project shown here is{" "}
        {costliest ? `${costliest.raw.project_id} at ${inrExact(costliest.price_max_inr)}` : "—"}.
        Taking the stored numbers as rupees would pick a different project
        entirely, and taking every value as crores would overstate it by a
        factor of a hundred.
      </p>
    </div>
  );
}
