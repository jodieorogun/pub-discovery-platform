import { db } from './db.js';

type OsmElement = {
  type: 'node' | 'way' | 'relation';
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  tags?: Record<string, string>;
};

const LONDON_BOUNDS = '51.28,-0.51,51.70,0.33';
const query = `[out:json][timeout:120];
  nwr[amenity=pub](${LONDON_BOUNDS});
  out center tags;`;

const response = await fetch('https://overpass-api.de/api/interpreter', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/x-www-form-urlencoded',
    Accept: 'application/json',
    'User-Agent': 'third-space/0.1 (https://github.com/jodieorogun/third-space)',
  },
  body: new URLSearchParams({ data: query }),
});

if (!response.ok) {
  throw new Error(`OpenStreetMap import failed: ${response.status} ${response.statusText}`);
}

const data = (await response.json()) as { elements: OsmElement[] };
let imported = 0;

for (const element of data.elements) {
  const latitude = element.lat ?? element.center?.lat;
  const longitude = element.lon ?? element.center?.lon;
  const tags = element.tags ?? {};

  if (!latitude || !longitude || !tags.name) continue;

  const address = [
    tags['addr:housenumber'],
    tags['addr:street'],
    tags['addr:city'],
    tags['addr:postcode'],
  ]
    .filter(Boolean)
    .join(', ') || null;

  await db.query(
    `INSERT INTO pubs
      (id, osm_type, osm_id, name, latitude, longitude, address, website, phone, tags, opening_hours, hours_source, address_source)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
     ON CONFLICT (id) DO UPDATE SET
       name = EXCLUDED.name,
       latitude = EXCLUDED.latitude,
       longitude = EXCLUDED.longitude,
       address = EXCLUDED.address,
       website = EXCLUDED.website,
       phone = EXCLUDED.phone,
       tags = EXCLUDED.tags,
       opening_hours = CASE WHEN pubs.hours_source IN ('manual', 'official') THEN pubs.opening_hours ELSE EXCLUDED.opening_hours END,
       hours_source = CASE WHEN pubs.hours_source IN ('manual', 'official') THEN pubs.hours_source ELSE EXCLUDED.hours_source END,
       address = CASE WHEN pubs.address_source IN ('manual', 'reverseGeocoded', 'officialWebsite') THEN pubs.address ELSE EXCLUDED.address END,
       address_source = CASE WHEN pubs.address_source IN ('manual', 'reverseGeocoded', 'officialWebsite') THEN pubs.address_source ELSE EXCLUDED.address_source END,
       updated_at = NOW()`,
    [
      `${element.type}/${element.id}`,
      element.type,
      element.id,
      tags.name,
      latitude,
      longitude,
      address,
      tags.website ?? tags['contact:website'] ?? null,
      tags.phone ?? tags['contact:phone'] ?? null,
      tags,
      tags['opening_hours'] ?? null,
      tags['opening_hours'] ? 'osm' : 'none',
      'openStreetMap',
    ],
  );
  imported += 1;
}

console.log(`Imported ${imported} named London pubs from OpenStreetMap.`);
await db.end();
