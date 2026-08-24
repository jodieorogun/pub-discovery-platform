import type { Pub } from './types';

export type PubImageSource = {
  uri: string;
  attribution?: string;
} | null;

export type PubImageResolution = {
  imageUrl: string;
  imageSource: 'osm' | 'wikimedia' | 'wikidata' | 'nearbyWikimedia';
  imageMatchConfidence: 'high' | 'medium';
  imageTitle?: string;
  attribution?: string;
};

// Temporary focused diagnostics for phone testing; remove after the image flow is confirmed.
const IMAGE_DEBUG_PUB = 'king of prussia';
const IMAGE_DEBUG_ENABLED = typeof __DEV__ !== 'undefined' && __DEV__;
const imageResolutionCache = new Map<string, Promise<PubImageResolution | null>>();

export type OpeningHoursRow = {
  day: string;
  hours: string;
};

export type OpeningHoursResult = {
  rows: OpeningHoursRow[];
  raw: string | null;
  parsed: boolean;
};

const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const dayCodes = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'] as const;

function tagValue(pub: Pub, ...keys: string[]): string | null {
  const rootValues: Record<string, string | null | undefined> = {
    image: pub.image,
    wikimedia_commons: pub.wikimediaCommons,
    wikimedia: pub.wikimedia,
    mapillary: pub.mapillary,
    operator: pub.operator,
    cuisine: pub.cuisine,
    opening_hours: pub.openingHours,
  };
  for (const key of keys) {
    const value = key === 'website'
      ? pub.website
      : key === 'phone'
        ? pub.phone
        : rootValues[key] ?? pub.tags?.[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

export function formatPubAddress(pub: Pub): string | null {
  const tags = pub.tags ?? {};
  const full = [tags['addr:full'], pub.address].find((value) => typeof value === 'string' && value.trim())?.trim();
  const street = [tags['addr:housenumber'], tags['addr:street']].filter(Boolean).join(' ').trim()
    || [tags['addr:housename'], tags['addr:street']].filter(Boolean).join(', ').trim()
    || tags['addr:street']
    || tags['addr:place'];
  const values = (full ? [full] : [street, tags['addr:suburb'], tags['addr:city'], tags['addr:postcode']])
    .map((value) => typeof value === 'string' ? value.trim() : '')
    .filter(Boolean);
  return [...new Set(values)].join(', ') || null;
}

export const normalizeAddress = formatPubAddress;

export function normalizePhone(pub: Pub): string | null {
  return tagValue(pub, 'phone', 'contact:phone');
}

export function normalizeWebsite(pub: Pub): string | null {
  const website = tagValue(pub, 'website', 'contact:website');
  if (!website) return null;
  return /^https?:\/\//i.test(website) ? website : `https://${website}`;
}

export function normalizeOperator(pub: Pub): string | null {
  return tagValue(pub, 'operator', 'brand');
}

export function normalizeCuisine(pub: Pub): string[] {
  const value = tagValue(pub, 'cuisine');
  return value
    ? value.split(/[;,]/).map((item) => item.trim()).filter(Boolean)
    : [];
}

function fileNameFromCommons(value: string): string | null {
  const urlFile = value.match(/(?:File:|file\/)([^#?]+)$/i)?.[1];
  const withoutPrefix = (urlFile ? decodeURIComponent(urlFile) : value.replace(/^File:/i, '')).replace(/_/g, ' ').trim();
  if (!withoutPrefix) return null;
  return withoutPrefix;
}

function shouldLogImage(pub: Pub): boolean {
  return IMAGE_DEBUG_ENABLED && pub.name.trim().toLowerCase() === IMAGE_DEBUG_PUB;
}

function logImageInput(pub: Pub) {
  if (shouldLogImage(pub)) {
    console.log('pubImageInput', {
      pubName: pub.name,
      image: pub.image ?? pub.tags?.image,
      wikimedia: pub.tags?.wikimedia ?? pub.tags?.wikimedia_commons ?? pub.wikimediaCommons,
      wikidata: pub.tags?.wikidata ?? pub.wikidata,
      website: pub.website ?? pub.tags?.website,
      latitude: pub.latitude,
      longitude: pub.longitude,
    });
  }
}

function wikimediaApiUrl(fileName: string): string {
  const params = new URLSearchParams({ action: 'query', titles: `File:${fileName}`, prop: 'imageinfo', iiprop: 'url', iiurlwidth: '800', format: 'json', origin: '*' });
  return `https://commons.wikimedia.org/w/api.php?${params.toString()}`;
}

async function resolveWikimediaFile(fileName: string, pub: Pub, imageSource: 'wikimedia' | 'wikidata' = 'wikimedia'): Promise<PubImageResolution | null> {
  const requestUrl = wikimediaApiUrl(fileName);
  if (shouldLogImage(pub)) console.log('wikimediaRequestUrl', requestUrl);
  try {
    const response = await fetch(requestUrl);
    if (shouldLogImage(pub)) console.log('wikimediaStatus', response.status);
    if (!response.ok) return null;
    const data = await response.json() as { query?: { pages?: Record<string, { imageinfo?: { thumburl?: string; url?: string }[] }> } };
    if (shouldLogImage(pub)) console.log('wikimediaResponse', data);
    const page = Object.values(data.query?.pages ?? {})[0];
    const info = page?.imageinfo?.[0];
    const imageUrl = info?.thumburl ?? info?.url;
    if (!imageUrl) return null;
    if (shouldLogImage(pub)) console.log('resolvedImageUrl', imageUrl);
    return { imageUrl, imageSource, imageMatchConfidence: 'high', imageTitle: fileName, attribution: `Wikimedia Commons: ${fileName}` };
  } catch (error) {
    if (shouldLogImage(pub)) console.log('wikimediaLookupFailed', error);
    return null;
  }
}

function wikidataId(value: string | null): string | null {
  if (!value) return null;
  const match = value.match(/(?:entity\/)?(Q\d+)/i);
  return match?.[1] ?? null;
}

async function resolveWikidataImage(pub: Pub): Promise<PubImageResolution | null> {
  const id = wikidataId(pub.tags?.wikidata ?? pub.wikidata ?? null);
  if (!id) return null;
  try {
    const response = await fetch(`https://www.wikidata.org/wiki/Special:EntityData/${id}.json`);
    if (shouldLogImage(pub)) console.log('wikidataStatus', response.status);
    if (!response.ok) return null;
    const data = await response.json() as { entities?: Record<string, { claims?: Record<string, { mainsnak?: { datavalue?: { value?: string } } }[]> }> };
    const fileName = data.entities?.[id]?.claims?.P18?.[0]?.mainsnak?.datavalue?.value;
    return fileName ? resolveWikimediaFile(fileName, pub, 'wikidata') : null;
  } catch (error) {
    if (shouldLogImage(pub)) console.log('wikidataLookupFailed', error);
    return null;
  }
}

function normaliseName(value: string): string {
  return value.toLowerCase().replace(/\bthe\b/g, '').replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
}

function candidateNameScore(pubName: string, candidate: string): number {
  const pubWords = normaliseName(pubName).split(' ').filter((word) => word.length > 2);
  const candidateName = normaliseName(candidate);
  if (pubWords.length === 0) return 0;
  return pubWords.filter((word) => candidateName.includes(word)).length / pubWords.length;
}

function distanceInMetres(pub: Pub, latitude?: number, longitude?: number): number | null {
  if (typeof latitude !== 'number' || typeof longitude !== 'number') return null;
  const latDistance = (latitude - pub.latitude) * 111_000;
  const lonDistance = (longitude - pub.longitude) * 111_000 * Math.cos(pub.latitude * Math.PI / 180);
  return Math.round(Math.sqrt((latDistance ** 2) + (lonDistance ** 2)));
}

function isLikelyBadImage(title: string, width?: number, height?: number): boolean {
  const lowered = title.toLowerCase();
  return Boolean((width && width < 500) || (height && height < 300) || (width && height && width < height) || /logo|icon|map|road|street|station|portrait|drawing|diagram|plan/.test(lowered));
}

async function resolveNearbyWikimediaImage(pub: Pub): Promise<PubImageResolution | null> {
  const params = new URLSearchParams({ action: 'query', generator: 'geosearch', ggsnamespace: '6', ggsradius: '1000', ggslimit: '20', prop: 'imageinfo|coordinates', iiprop: 'url|size|extmetadata', iiurlwidth: '800', format: 'json', origin: '*', ggscoord: `${pub.latitude}|${pub.longitude}` });
  const requestUrl = `https://commons.wikimedia.org/w/api.php?${params.toString()}`;
  if (shouldLogImage(pub)) console.log('wikimediaRequestUrl', requestUrl);
  try {
    const response = await fetch(requestUrl);
    if (shouldLogImage(pub)) console.log('wikimediaStatus', response.status);
    if (!response.ok) return null;
    const data = await response.json() as { query?: { pages?: Record<string, { title?: string; coordinates?: { lat?: number; lon?: number }[]; imageinfo?: { thumburl?: string; url?: string; width?: number; height?: number; extmetadata?: { ImageDescription?: { value?: string }; ObjectName?: { value?: string } } }[] }> } };
    if (shouldLogImage(pub)) console.log('wikimediaResponse', data);
    const pages = Object.values(data.query?.pages ?? {});
    const candidates = pages.map((page) => {
      const info = page.imageinfo?.[0];
      const title = page.title ?? '';
      const description = [info?.extmetadata?.ImageDescription?.value, info?.extmetadata?.ObjectName?.value].filter(Boolean).join(' ');
      const nameScore = Math.max(candidateNameScore(pub.name, title), candidateNameScore(pub.name, description));
      const distance = distanceInMetres(pub, page.coordinates?.[0]?.lat, page.coordinates?.[0]?.lon);
      const badImage = isLikelyBadImage(title, info?.width, info?.height);
      const confidence = nameScore >= 0.66 && (distance === null || distance <= 1000) && !badImage ? 'high' : 'medium';
      if (shouldLogImage(pub)) console.log('wikimediaCandidate', { title, nameScore, distance, badImage, confidence });
      return { page, info, title, nameScore, distance, badImage, confidence };
    });
    const matchingPage = candidates
      .filter((candidate) => candidate.confidence === 'high' && candidate.info?.thumburl)
      .sort((left, right) => (right.nameScore - left.nameScore) || ((left.distance ?? 100000) - (right.distance ?? 100000)))[0];
    if (shouldLogImage(pub) && !matchingPage) console.log('wikimediaSelectionRejected', { reason: 'no high-confidence candidate' });
    const info = matchingPage?.info;
    const imageUrl = info?.thumburl ?? info?.url;
    if (!imageUrl) return null;
    if (shouldLogImage(pub)) console.log('resolvedImageUrl', imageUrl);
    return { imageUrl, imageSource: 'nearbyWikimedia', imageMatchConfidence: 'high', imageTitle: matchingPage?.title, attribution: `Wikimedia Commons: ${matchingPage?.title ?? pub.name}` };
  } catch (error) {
    if (shouldLogImage(pub)) console.log('wikimediaNearbyLookupFailed', error);
    return null;
  }
}

export function getPubImageSource(pub: Pub): PubImageSource {
  logImageInput(pub);
  const commons = tagValue(pub, 'wikimedia_commons', 'wikimedia');
  const fileName = commons ? fileNameFromCommons(commons) : null;
  if (fileName) {
    return {
      uri: `https://commons.wikimedia.org/wiki/Special:FilePath/${encodeURIComponent(fileName)}?width=1200`,
      attribution: `Wikimedia Commons: ${fileName}`,
    };
  }

  const directImage = tagValue(pub, 'image');
  if (directImage && /^https?:\/\//i.test(directImage)) return { uri: directImage };

  // Mapillary references need an access token/API resolver, which this app does not have.
  return null;
}

export async function resolvePubImage(pub: Pub): Promise<PubImageResolution | null> {
  const cached = imageResolutionCache.get(pub.id);
  if (cached) return cached;
  const promise = resolvePubImageUncached(pub);
  imageResolutionCache.set(pub.id, promise);
  return promise;
}

async function resolvePubImageUncached(pub: Pub): Promise<PubImageResolution | null> {
  logImageInput(pub);
  const direct = tagValue(pub, 'image');
  if (direct && /^https?:\/\//i.test(direct)) return { imageUrl: direct, imageSource: 'osm', imageMatchConfidence: 'high', imageTitle: pub.name };
  const commons = tagValue(pub, 'wikimedia_commons', 'wikimedia');
  const fileName = commons ? fileNameFromCommons(commons) : null;
  if (fileName) return resolveWikimediaFile(fileName, pub);
  const wikidata = await resolveWikidataImage(pub);
  if (wikidata) return wikidata;
  return resolveNearbyWikimediaImage(pub);
}

export const resolvePubImageSource = resolvePubImage;

function displayTime(value: string): string {
  return value === '24:00' ? '00:00' : value;
}

function expandDays(expression: string): number[] | null {
  const tokens = expression.split(',').map((token) => token.trim()).filter(Boolean);
  if (tokens.length === 0) return null;
  const indexes: number[] = [];
  for (const token of tokens) {
    const range = token.match(/^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$/);
    if (!range) return null;
    const start = dayCodes.indexOf(range[1] as (typeof dayCodes)[number]);
    const end = range[2] ? dayCodes.indexOf(range[2] as (typeof dayCodes)[number]) : start;
    if (start < 0 || end < 0) return null;
    for (let index = start; index <= end; index += 1) indexes.push(index);
  }
  return [...new Set(indexes)];
}

function hoursFromRule(value: string): string | null {
  const ranges = [...value.matchAll(/(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})/g)];
  if (ranges.length === 0) return null;
  return ranges.map((range) => `${displayTime(range[1])}–${displayTime(range[2])}`).join(', ');
}

export function parseOpeningHours(value: string | null | undefined): OpeningHoursResult {
  const raw = value?.trim() || null;
  if (!raw) return { rows: [], raw: null, parsed: false };
  if (raw.toLowerCase() === '24/7') {
    return { rows: dayNames.map((day) => ({ day, hours: 'Open 24 hours' })), raw, parsed: true };
  }

  const rowHours = dayNames.map(() => [] as string[]);
  let parsedAny = false;
  for (const rule of raw.split(';').map((part) => part.trim()).filter(Boolean)) {
    const hours = hoursFromRule(rule);
    if (!hours) continue;
    const dayExpression = rule.slice(0, rule.search(/\d{1,2}:\d{2}/)).trim();
    const indexes = expandDays(dayExpression);
    if (!indexes) continue;
    parsedAny = true;
    indexes.forEach((index) => rowHours[index].push(hours));
  }

  if (!parsedAny || rowHours.some((hours) => hours.length === 0)) {
    return { rows: [], raw, parsed: false };
  }
  return {
    rows: rowHours.map((hours, index) => ({ day: dayNames[index], hours: hours.join(', ') })),
    raw,
    parsed: true,
  };
}

export function normalizeOpeningHours(pub: Pub): OpeningHoursResult {
  return parseOpeningHours(tagValue(pub, 'opening_hours'));
}
