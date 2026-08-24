import { db } from './db.js';

await db.query(`
  CREATE TABLE IF NOT EXISTS pubs (
    id TEXT PRIMARY KEY,
    osm_type TEXT NOT NULL,
    osm_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    address TEXT,
    website TEXT,
    phone TEXT,
    tags JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (osm_type, osm_id)
  );

  CREATE INDEX IF NOT EXISTS pubs_location_idx ON pubs (latitude, longitude);

  ALTER TABLE pubs ADD COLUMN IF NOT EXISTS opening_hours TEXT;
  ALTER TABLE pubs ADD COLUMN IF NOT EXISTS hours_source TEXT NOT NULL DEFAULT 'none';
  ALTER TABLE pubs ADD COLUMN IF NOT EXISTS hours_last_checked TIMESTAMPTZ;
  ALTER TABLE pubs ADD COLUMN IF NOT EXISTS hours_confidence TEXT;
  ALTER TABLE pubs ADD COLUMN IF NOT EXISTS address_source TEXT NOT NULL DEFAULT 'openStreetMap';
  ALTER TABLE pubs ADD COLUMN IF NOT EXISTS address_last_checked TIMESTAMPTZ;

  UPDATE pubs
  SET opening_hours = COALESCE(opening_hours, tags->>'opening_hours'),
      hours_source = CASE
        WHEN hours_source = 'none' AND NULLIF(tags->>'opening_hours', '') IS NOT NULL THEN 'osm'
        ELSE hours_source
      END,
      address_source = CASE
        WHEN NULLIF(address, '') IS NULL THEN 'openStreetMap'
        ELSE address_source
      END
  WHERE opening_hours IS NULL OR hours_source = 'none';
`);

console.log('Database is ready.');
await db.end();
