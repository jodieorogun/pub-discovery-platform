import { db } from './db.js';

type HoursSource = 'manual' | 'official' | 'osm' | 'none';

type JsonLdOpeningSpecification = {
  dayOfWeek?: string | string[];
  opens?: string;
  closes?: string;
};

type WebsitePubData = {
  openingHours: string | null;
  phone: string | null;
  address: string | null;
};

type RefreshResult = WebsitePubData & {
  updated: boolean;
  reason?: string;
  hoursLastChecked?: string | null;
  hoursConfidence?: string | null;
  hoursSource?: HoursSource;
};

type BookingLinkResult = {
  bookingUrl: string | null;
  discovered: boolean;
  reason?: string;
};

const dayCodes: Record<string, string> = {
  monday: 'Mo',
  tuesday: 'Tu',
  wednesday: 'We',
  thursday: 'Th',
  friday: 'Fr',
  saturday: 'Sa',
  sunday: 'Su',
};
const orderedDayCodes = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

function normaliseUrl(value: string): string {
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

function stripMarkup(value: string): string {
  return value.replace(/<[^>]*>/g, ' ').replace(/&amp;/gi, '&').replace(/&quot;/gi, '"').replace(/&#39;/gi, "'").replace(/\s+/g, ' ').trim();
}

const socialHosts = new Set([
  'facebook.com',
  'instagram.com',
  'linkedin.com',
  'tiktok.com',
  'twitter.com',
  'x.com',
  'youtube.com',
]);

const bookingProviderHosts = new Set([
  'bookatable.co.uk',
  'designmynight.com',
  'opentable.com',
  'opentable.co.uk',
  'quandoo.co.uk',
  'quandoo.com',
  'resdiary.com',
  'sevenrooms.com',
  'thefork.co.uk',
  'thefork.com',
]);

function hostMatches(hostname: string, hosts: Set<string>): boolean {
  const host = hostname.toLowerCase().replace(/^www\./, '');
  return [...hosts].some((knownHost) => host === knownHost || host.endsWith(`.${knownHost}`));
}

function isSocialUrl(value: URL): boolean {
  return hostMatches(value.hostname, socialHosts);
}

function isBookingProviderUrl(value: URL): boolean {
  return hostMatches(value.hostname, bookingProviderHosts);
}

function bookingLinkFromHtml(html: string, baseUrl: string): string | null {
  for (const match of html.matchAll(/<a\b([^>]*?)href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi)) {
    const href = match[2].trim();
    const text = stripMarkup(match[3]);
    const signal = `${href} ${text}`.toLowerCase();
    try {
      const candidate = new URL(href, baseUrl);
      if (!['http:', 'https:'].includes(candidate.protocol) || isSocialUrl(candidate)) continue;
      const hasBookingSignal = /book|reserv|reserve|table/.test(signal);
      if ((!hasBookingSignal && !isBookingProviderUrl(candidate)) || /privacy|cookie|unsubscribe|gift card/.test(signal)) continue;
      return candidate.href;
    } catch {
      // Ignore malformed links and keep looking for a usable booking link.
    }
  }
  return null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function dayCode(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const name = value.replace(/^https?:\/\/schema\.org\//i, '').toLowerCase();
  return dayCodes[name] ?? (Object.values(dayCodes).includes(value) ? value : null);
}

function validHours(value: string | null): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (trimmed.toLowerCase() === '24/7') return '24/7';
  const ranges = trimmed.split(';').map((part) => part.trim()).filter(Boolean);
  const timeIsValid = (time: string) => {
    const [hour, minute] = time.split(':').map(Number);
    return Number.isInteger(hour) && Number.isInteger(minute) && hour >= 0 && hour <= 24 && minute >= 0 && minute < 60 && (hour < 24 || minute === 0);
  };
  if (ranges.length === 0 || ranges.some((part) => {
    const match = part.match(/^(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?(?:,(?:Mo|Tu|We|Th|Fr|Sa|Su))*\s+(.+)$/);
    if (!match) return true;
    const times = [...match[1].matchAll(/(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})/g)];
    return times.length === 0 || times.some((time) => !timeIsValid(time[1]) || !timeIsValid(time[2]));
  })) return null;
  return trimmed;
}

function openingHoursValue(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (Array.isArray(value) && value.every((item) => typeof item === 'string')) return value.join('; ');
  return null;
}

function openingHoursFromSpecifications(value: unknown): string | null {
  const specifications = Array.isArray(value) ? value : [value];
  const byDay = new Map<string, string[]>();
  for (const specification of specifications) {
    if (!specification || typeof specification !== 'object') continue;
    const item = specification as JsonLdOpeningSpecification;
    const days = Array.isArray(item.dayOfWeek) ? item.dayOfWeek : [item.dayOfWeek];
    const opens = asString(item.opens);
    const closes = asString(item.closes);
    if (!opens || !closes) continue;
    const range = `${opens}-${closes}`;
    for (const day of days) {
      const code = dayCode(day);
      if (!code) continue;
      const current = byDay.get(code) ?? [];
      current.push(range);
      byDay.set(code, current);
    }
  }
  if (byDay.size === 0) return null;
  return [...byDay.entries()]
    .sort(([left], [right]) => orderedDayCodes.indexOf(left) - orderedDayCodes.indexOf(right))
    .map(([day, ranges]) => `${day} ${ranges.join(',')}`)
    .join('; ');
}

function addressFromJsonLd(value: unknown): string | null {
  if (typeof value === 'string') return asString(value);
  if (!value || typeof value !== 'object') return null;
  const address = value as Record<string, unknown>;
  return [address.streetAddress, address.addressLocality, address.postalCode]
    .map(asString)
    .filter(Boolean)
    .join(', ') || null;
}

function collectJsonLd(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.flatMap(collectJsonLd);
  if (!value || typeof value !== 'object') return [];
  const record = value as Record<string, unknown>;
  return [record, ...collectJsonLd(record['@graph'])];
}

function normaliseVenueName(value: string): string {
  return value.toLowerCase().replace(/\bthe\b/g, '').replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
}

function candidateBelongsToVenue(candidate: Record<string, unknown>, expectedName?: string): boolean {
  const type = Array.isArray(candidate['@type']) ? candidate['@type'].join(' ') : asString(candidate['@type']);
  const venueType = Boolean(type && /bar|pub|restaurant|cafe|localbusiness|foodestablishment/i.test(type));
  if (!expectedName) return venueType || !type;
  const candidateName = asString(candidate.name);
  if (candidateName && normaliseVenueName(candidateName) === normaliseVenueName(expectedName)) return true;
  return venueType && !candidateName;
}

export function extractWebsitePubData(html: string, expectedName?: string): WebsitePubData {
  const candidates: Record<string, unknown>[] = [];
  for (const match of html.matchAll(/<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)) {
    try {
      candidates.push(...collectJsonLd(JSON.parse(match[1].trim())));
    } catch {
      // Ignore malformed JSON-LD blocks and continue with the other blocks.
    }
  }

  const venueCandidates = candidates.filter((candidate) => candidateBelongsToVenue(candidate, expectedName));
  const usableCandidates = venueCandidates.length > 0 ? venueCandidates : [];
  let openingHours: string | null = null;
  let phone: string | null = null;
  let address: string | null = null;
  for (const candidate of usableCandidates) {
    openingHours ??= validHours(openingHoursValue(candidate.openingHours))
      ?? validHours(openingHoursFromSpecifications(candidate.openingHoursSpecification));
    phone ??= asString(candidate.telephone);
    address ??= addressFromJsonLd(candidate.address);
  }
  return { openingHours, phone, address };
}

export async function findPubBookingUrl(pubId: string, fallbackWebsite?: string): Promise<BookingLinkResult> {
  const result = await db.query<{ website: string | null }>('SELECT website FROM pubs WHERE id = $1', [pubId]);
  const pub = result.rows[0];
  const websiteValue = pub?.website || fallbackWebsite || null;
  if (!websiteValue) return { bookingUrl: null, discovered: false, reason: pub ? 'no official website' : `Pub not found: ${pubId}` };

  const website = normaliseUrl(websiteValue);
  try {
    const officialUrl = new URL(website);
    if (isSocialUrl(officialUrl)) return { bookingUrl: null, discovered: false, reason: 'official website is a social profile' };
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(website, { signal: controller.signal, headers: { Accept: 'text/html,application/xhtml+xml', 'User-Agent': 'pub-discovery-booking/0.1' } });
    clearTimeout(timeout);
    if (!response.ok) throw new Error(`website returned ${response.status}`);
    const bookingUrl = bookingLinkFromHtml(await response.text(), website);
    return bookingUrl
      ? { bookingUrl, discovered: true }
      : { bookingUrl: null, discovered: false, reason: 'no dedicated booking link found' };
  } catch (error) {
    console.error('findPubBookingUrl failed', { pubId, website, error: error instanceof Error ? error.message : error });
    return { bookingUrl: null, discovered: false, reason: 'official website could not be checked' };
  }
}

export async function refreshPubOpeningHours(pubId: string): Promise<RefreshResult> {
  const result = await db.query<{
    name: string;
    website: string | null;
    phone: string | null;
    address: string | null;
    opening_hours: string | null;
    hours_source: HoursSource;
    hours_last_checked: string | null;
    hours_confidence: string | null;
    latitude: number;
    longitude: number;
  }>(`SELECT name, website, phone, address, opening_hours, hours_source, hours_last_checked, hours_confidence, latitude, longitude FROM pubs WHERE id = $1`, [pubId]);
  const pub = result.rows[0];
  if (!pub) throw new Error(`Pub not found: ${pubId}`);
  if (pub.hours_source === 'manual') return { openingHours: pub.opening_hours, phone: pub.phone, address: pub.address, hoursSource: 'manual', hoursLastChecked: pub.hours_last_checked, hoursConfidence: pub.hours_confidence, updated: false, reason: 'manual correction preserved' };
  if (!pub.website) {
    await db.query(`UPDATE pubs SET hours_last_checked = NOW() WHERE id = $1`, [pubId]);
    return { openingHours: pub.opening_hours, phone: pub.phone, address: pub.address, hoursSource: pub.hours_source, hoursLastChecked: new Date().toISOString(), hoursConfidence: pub.hours_confidence, updated: false, reason: 'no official website' };
  }

  const website = normaliseUrl(pub.website);
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(website, { signal: controller.signal, headers: { Accept: 'text/html,application/xhtml+xml', 'User-Agent': 'third-space-hours/0.1' } });
    clearTimeout(timeout);
    if (!response.ok) throw new Error(`website returned ${response.status}`);
    const html = await response.text();
    const extracted = extractWebsitePubData(html, pub.name);
    if (!extracted.openingHours) {
      await db.query(`UPDATE pubs SET hours_last_checked = NOW(), phone = COALESCE(phone, $1), address = COALESCE(address, $2), address_source = CASE WHEN address IS NULL AND $2 IS NOT NULL THEN 'officialWebsite' ELSE address_source END, address_last_checked = CASE WHEN $2 IS NOT NULL THEN NOW() ELSE address_last_checked END WHERE id = $3`, [extracted.phone, extracted.address, pubId]);
      return { ...extracted, openingHours: pub.opening_hours, hoursSource: pub.hours_source, hoursLastChecked: new Date().toISOString(), hoursConfidence: pub.hours_confidence, updated: false, reason: 'no reliable JSON-LD hours found' };
    }
    await db.query(
      `UPDATE pubs
       SET opening_hours = $1,
           hours_source = 'official',
           hours_last_checked = NOW(),
           hours_confidence = 'high',
           phone = COALESCE(phone, $2),
           address = COALESCE(address, $3),
           address_source = CASE WHEN address IS NULL AND $3 IS NOT NULL THEN 'officialWebsite' ELSE address_source END,
           address_last_checked = CASE WHEN $3 IS NOT NULL THEN NOW() ELSE address_last_checked END
       WHERE id = $4 AND hours_source <> 'manual'`,
      [extracted.openingHours, extracted.phone, extracted.address, pubId],
    );
    return { ...extracted, hoursSource: 'official', hoursLastChecked: new Date().toISOString(), hoursConfidence: 'high', updated: true };
  } catch (error) {
    console.error('refreshPubOpeningHours failed', { pubId, website, error: error instanceof Error ? error.message : error });
    await db.query(`UPDATE pubs SET hours_last_checked = NOW() WHERE id = $1`, [pubId]);
    return { openingHours: pub.opening_hours, phone: pub.phone, address: pub.address, hoursSource: pub.hours_source, hoursLastChecked: new Date().toISOString(), hoursConfidence: pub.hours_confidence, updated: false, reason: 'website request or extraction failed' };
  }
}
