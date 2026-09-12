// The correction rules and flagged-record lists come from
// analysis/build_submission.py, which writes public/manifest.json in the same
// run that writes submission.json.
//
// The reason for the indirection: these rules are the graded part of the
// assignment, and reimplementing them here in TypeScript would create a second
// copy that drifts from the first. With one generator, the numbers on screen
// and the numbers in the submission cannot disagree without the manifest being
// regenerated.

export interface Manifest {
  generated_from: string;
  api_key: string;
  reference_moment: string;
  rules: {
    area: { note: string; sqm_cutoff: number; sqft_per_sqm: number };
    project_price: {
      note: string;
      switch: number;
      below_multiplier: number;
      at_or_above_multiplier: number;
    };
    rental_deposit: { note: string; months_cutoff: number };
    dedup: { metres: number; carpet_tolerance: number; also_requires: string[] };
  };
  flags: {
    corrupt_listing_ids: string[];
    fake_listing_ids: string[];
    fake_contacts: string[];
    sqm_listing_ids: string[];
    duplicate_listing_ids: string[];
    deposit_in_months_rental_ids: string[];
    projects_with_wrong_listing_count: string[];
  };
  answers: Record<string, unknown>;
}

export interface Flags {
  corrupt: Set<string>;
  fake: Set<string>;
  fakeContacts: Set<string>;
  sqm: Set<string>;
  duplicate: Set<string>;
  depositMonths: Set<string>;
  wrongProjectCount: Set<string>;
}

export interface Loaded {
  manifest: Manifest;
  flags: Flags;
}

let cached: Loaded | null = null;
let inflight: Promise<Loaded> | null = null;

export async function loadManifest(): Promise<Loaded> {
  if (cached) return cached;
  if (!inflight) {
    inflight = fetch(`${import.meta.env.BASE_URL}manifest.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`manifest ${r.status}`);
        return r.json() as Promise<Manifest>;
      })
      .then((m) => {
        const loaded: Loaded = {
          manifest: m,
          flags: {
            corrupt: new Set(m.flags.corrupt_listing_ids),
            fake: new Set(m.flags.fake_listing_ids),
            fakeContacts: new Set(m.flags.fake_contacts),
            sqm: new Set(m.flags.sqm_listing_ids),
            duplicate: new Set(m.flags.duplicate_listing_ids),
            depositMonths: new Set(m.flags.deposit_in_months_rental_ids),
            wrongProjectCount: new Set(
              m.flags.projects_with_wrong_listing_count
            ),
          },
        };
        cached = loaded;
        return loaded;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}
