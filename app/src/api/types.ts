// Field sets here are what the API actually returns, not what the reference
// documents. Differences worth remembering:
//   * `is_live` exists on listings and rentals, is in neither documented
//     object, and the collections are not filtered to live records.
//   * rentals spell it `super_builtup_area`; listings use `super_built_up_area`.
//   * the envelope is limit/offset/count/total/has_more, and `total`
//     under-reports what the endpoint will actually serve.

export interface Envelope<T> {
  limit: number;
  offset: number;
  count: number;
  total: number;
  has_more: boolean;
  results: T[];
}

export interface Listing {
  listing_id: string;
  listing_url: string;
  website: string;
  city_id: number;
  apartment_name: string;
  locality: string;
  property_type: string;
  bedroom: number;
  bathroom: number;
  balcony: number;
  floor: number;
  total_floors: number;
  furnishing: string;
  facing_direction: string;
  covered_parking: number;
  price: number;
  carpet_area: number;
  super_built_up_area: number;
  latitude: number;
  longitude: number;
  posted_by: string;
  posted_by_name: string;
  posted_by_contact: string;
  project_id: string | null;
  description: string;
  posted_at: string;
  is_verified: boolean;
  is_live: boolean;
}

export interface Rental {
  listing_id: string;
  listing_url: string;
  website: string;
  city_id: number;
  title: string;
  apartment_name: string;
  locality: string;
  property_type: string;
  bedroom: number;
  bathroom: number;
  floor: number;
  total_floors: number;
  furnishing: string;
  facing_direction: string;
  price: number;
  deposit: number;
  maintenance: number;
  carpet_area: number;
  super_builtup_area: number;
  latitude: number;
  longitude: number;
  posted_by: string;
  posted_by_name: string;
  posted_by_contact: string;
  description: string;
  posted_at: string;
  is_live: boolean;
}

export interface Project {
  project_id: string;
  project_url: string;
  city_id: number;
  apartment_name: string;
  developer_name: string;
  locality: string;
  project_status: string;
  total_units: number;
  total_towers: number;
  total_floors: number;
  launch_date: string;
  possession_date: string;
  rera_number: string;
  min_area_sqft: number;
  max_area_sqft: number;
  total_listings: number;
  price_min: number;
  price_max: number;
  amenities: string[];
  latitude: number;
  longitude: number;
}

export interface Session {
  access_token: string;
  refresh_token: string;
  email: string;
  obtained_at: number;
  expires_in: number;
}
