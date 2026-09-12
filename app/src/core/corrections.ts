import type { Flags, Manifest } from "./manifest";
import type { Listing, Project, Rental } from "../api/types";

// Every number this app shows a user passes through here first.
//
// Three corrections, all measured rather than guessed, all documented in
// submission.json with the evidence that produced them:
//
//   area     333 listings state carpet_area and super_built_up_area in square
//            metres. The groups do not overlap - metres run 34 to 197, feet
//            start at 206 - and super/carpet stays near 1.34 in both, so these
//            are small units, not small flats.
//
//   project  price_min and price_max are Indian display units, where the unit
//   price    depends on the magnitude: below 10 means crores, 10 or above means
//            lakhs. Read as the documented rupees, price_min exceeds price_max
//            for 357 of 460 projects, which a minimum and a maximum cannot do.
//
//   deposit  301 rentals give deposit as a count of months instead of rupees.
//            Multiplying by that record's own rent lands them on the same
//            distribution as the rupee records.

export interface Corrected<T> {
  raw: T;
  corrupt: boolean;
  fake: boolean;
  duplicate: boolean;
  unitFixed: boolean;
}

export type CorrectedListing = Corrected<Listing> & {
  carpet_area: number;
  super_built_up_area: number;
  price_per_sqft: number | null;
};

export type CorrectedRental = Corrected<Rental> & {
  deposit_inr: number;
  deposit_was_months: boolean;
};

export type CorrectedProject = Corrected<Project> & {
  price_min_inr: number | null;
  price_max_inr: number | null;
  listing_count_wrong: boolean;
};

export function correctListing(
  r: Listing,
  m: Manifest,
  f: Flags
): CorrectedListing {
  const isSqm = f.sqm.has(r.listing_id);
  const k = isSqm ? m.rules.area.sqft_per_sqm : 1;
  const carpet = r.carpet_area * k;
  return {
    raw: r,
    carpet_area: carpet,
    super_built_up_area: r.super_built_up_area * k,
    price_per_sqft: carpet > 0 && r.price > 0 ? r.price / carpet : null,
    corrupt: f.corrupt.has(r.listing_id),
    fake: f.fake.has(r.listing_id) || f.fakeContacts.has(r.posted_by_contact),
    duplicate: f.duplicate.has(r.listing_id),
    unitFixed: isSqm,
  };
}

export function correctRental(r: Rental, f: Flags): CorrectedRental {
  const months = f.depositMonths.has(r.listing_id);
  return {
    raw: r,
    deposit_inr: months ? r.deposit * r.price : r.deposit,
    deposit_was_months: months,
    corrupt: false,
    fake: f.fakeContacts.has(r.posted_by_contact),
    duplicate: false,
    unitFixed: months,
  };
}

export function projectPriceToInr(v: number | null | undefined, m: Manifest) {
  if (typeof v !== "number" || !isFinite(v) || v <= 0) return null;
  const mult =
    v >= m.rules.project_price.switch
      ? m.rules.project_price.at_or_above_multiplier
      : m.rules.project_price.below_multiplier;
  return Math.round(v * mult);
}

export function correctProject(
  r: Project,
  m: Manifest,
  f: Flags
): CorrectedProject {
  return {
    raw: r,
    price_min_inr: projectPriceToInr(r.price_min, m),
    price_max_inr: projectPriceToInr(r.price_max, m),
    listing_count_wrong: f.wrongProjectCount.has(r.project_id),
    corrupt: false,
    fake: false,
    duplicate: false,
    unitFixed: true,
  };
}

// ---------------------------------------------------------------------------
// formatting
// ---------------------------------------------------------------------------
// Rupee amounts are shown the way the market states them, because a Chennai
// buyer reads "1.87 Cr" instantly and "18650000" not at all. This is a display
// choice on top of correct rupee values, which is the opposite of what the API
// does - it stores the display form and calls it rupees.

export function inr(v: number | null | undefined): string {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  const neg = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1e7) return `${neg}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${neg}₹${(a / 1e5).toFixed(2)} L`;
  return `${neg}₹${Math.round(a).toLocaleString("en-IN")}`;
}

export function inrExact(v: number | null | undefined): string {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

export function sqft(v: number | null | undefined): string {
  if (typeof v !== "number" || !isFinite(v) || v <= 0) return "—";
  return `${Math.round(v).toLocaleString("en-IN")} sqft`;
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

// posted_at on listings carries no timezone marker at all, and /health reports
// Asia/Kolkata with a +05:30 reference_date, so a naive stamp is IST. Rentals
// use a Z suffix instead - two conventions in one API. Reading the naive ones
// as UTC shifts everything by five and a half hours.
export function parseIst(s: string | null | undefined): Date | null {
  if (!s) return null;
  const hasZone = /[zZ]$|[+-]\d{2}:\d{2}$/.test(s);
  const d = new Date(hasZone ? s : `${s}+05:30`);
  return isNaN(d.getTime()) ? null : d;
}

export function postedAgo(s: string | null | undefined): string {
  const d = parseIst(s);
  if (!d) return "—";
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days < 0) return "future-dated";
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  if (days < 365) return `${Math.floor(days / 30)} months ago`;
  return `${Math.floor(days / 365)} years ago`;
}
