import { db } from './db.js';

type AddressSource = 'manual' | 'openStreetMap' | 'reverseGeocoded' | 'officialWebsite';

let lastNominatimRequestAt = 0;

function waitForRateLimit(): Promise<void> {
  const delay = Math.max(0, 1100 - (Date.now() - lastNominatimRequestAt));
  return new Promise((resolve) => setTimeout(resolve, delay));
}

export async function refreshPubAddress(pubId: string) {
  const result = await db.query<{ latitude: number; longitude: number; address: string | null; address_source: AddressSource; address_last_checked: string | null }>(
    `SELECT latitude, longitude, address, address_source, address_last_checked FROM pubs WHERE id = $1`,
    [pubId],
  );
  const pub = result.rows[0];
  if (!pub) throw new Error(`Pub not found: ${pubId}`);
  if (pub.address || pub.address_source === 'manual' || pub.address_source === 'officialWebsite') {
    return { address: pub.address, addressSource: pub.address_source, addressLastChecked: pub.address_last_checked, updated: false, reason: 'existing trusted address preserved' };
  }

  await waitForRateLimit();
  lastNominatimRequestAt = Date.now();
  const params = new URLSearchParams({ format: 'jsonv2', lat: `${pub.latitude}`, lon: `${pub.longitude}`, zoom: '18', addressdetails: '1' });
  try {
    const response = await fetch(`https://nominatim.openstreetmap.org/reverse?${params.toString()}`, {
      headers: { Accept: 'application/json', 'User-Agent': 'third-space/0.1 (https://github.com/jodieorogun/third-space)' },
    });
    if (!response.ok) throw new Error(`Nominatim returned ${response.status}`);
    const data = await response.json() as { display_name?: string };
    const address = typeof data.display_name === 'string' ? data.display_name.trim() : '';
    if (!address) return { address: null, addressSource: pub.address_source, addressLastChecked: new Date().toISOString(), updated: false, reason: 'no reverse-geocoded address' };
    await db.query(`UPDATE pubs SET address = $1, address_source = 'reverseGeocoded', address_last_checked = NOW() WHERE id = $2 AND address IS NULL`, [address, pubId]);
    return { address, addressSource: 'reverseGeocoded' as const, addressLastChecked: new Date().toISOString(), updated: true };
  } catch (error) {
    console.error('refreshPubAddress failed', { pubId, error: error instanceof Error ? error.message : error });
    return { address: pub.address, addressSource: pub.address_source, addressLastChecked: new Date().toISOString(), updated: false, reason: 'reverse geocoding failed' };
  }
}
