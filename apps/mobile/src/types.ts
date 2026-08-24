export type Pub = {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  address: string | null;
  website: string | null;
  phone: string | null;
  tags: Record<string, string>;
  image?: string | null;
  wikimediaCommons?: string | null;
  wikimedia?: string | null;
  wikidata?: string | null;
  mapillary?: string | null;
  operator?: string | null;
  cuisine?: string | null;
  openingHours?: string | null;
  hoursSource?: 'manual' | 'official' | 'osm' | 'none' | null;
  hoursLastChecked?: string | null;
  hoursConfidence?: string | null;
  addressSource?: 'manual' | 'openStreetMap' | 'reverseGeocoded' | 'officialWebsite' | null;
  addressLastChecked?: string | null;
};

export type Tab = 'map' | 'search' | 'passport' | 'profile';
export type MapFilter = 'Pins' | 'Visited' | 'Saved';

export type Recommendation = {
  venueId: string;
  name: string;
  latitude: number;
  longitude: number;
  address: string | null;
  website: string | null;
  phone: string | null;
  score: number;
  reasons: string[];
  evidence: string[];
};
