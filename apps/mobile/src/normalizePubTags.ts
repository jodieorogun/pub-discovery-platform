import type { Pub } from './types';

export type NormalizedPubTags = {
  hasFood: boolean | null;
  hasOutdoorSeating: boolean | null;
  hasLiveSport: boolean | null;
  hasWifi: boolean | null;
  isOpenLate: boolean | null;
  hasVeganOptions: boolean | null;
  hasVegetarianOptions: boolean | null;
  hasGlutenFreeOptions: boolean | null;
  cuisines: string[];
  postcode: string | null;
  street: string | null;
  city: string | null;
  area: string | null;
};

const days = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'] as const;

function yesNoUnknown(value: string | undefined): boolean | null {
  if (value === undefined) return null;
  const normalized = value.trim().toLowerCase();
  if (['yes', 'true', 'designated'].includes(normalized)) return true;
  if (['no', 'false'].includes(normalized)) return false;
  return null;
}

function wifiValue(value: string | undefined): boolean | null {
  if (value === undefined) return null;
  const normalized = value.trim().toLowerCase();
  if (['wlan', 'yes', 'free'].includes(normalized)) return true;
  if (['no', 'none'].includes(normalized)) return false;
  return null;
}

function ruleIncludesWeekend(dayExpression: string): boolean {
  const tokens = dayExpression.match(/(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?/g) ?? [];

  return tokens.some((token) => {
    const [start, end] = token.split('-');
    if (!end) return start === 'Fr' || start === 'Sa';
    const startIndex = days.indexOf(start as (typeof days)[number]);
    const endIndex = days.indexOf(end as (typeof days)[number]);
    if (startIndex < 0 || endIndex < 0) return false;
    const included = startIndex <= endIndex
      ? days.slice(startIndex, endIndex + 1)
      : [...days.slice(startIndex), ...days.slice(0, endIndex + 1)];
    return included.includes('Fr') || included.includes('Sa');
  });
}

function closesAtMidnightOrLater(closingTime: string): boolean {
  const [hour, minute] = closingTime.split(':').map(Number);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return false;
  return hour >= 24 || hour <= 6;
}

export function determineIsOpenLate(openingHours: string | undefined): boolean | null {
  if (openingHours === undefined) return null;
  if (openingHours.trim().toLowerCase() === '24/7') return true;

  let foundWeekendHours = false;
  for (const rule of openingHours.split(';')) {
    const dayExpression = rule.match(/^(.*?)\s+(?=\d{1,2}:\d{2})/)?.[1];
    if (!dayExpression || !ruleIncludesWeekend(dayExpression)) continue;

    const ranges = [...rule.matchAll(/\d{1,2}:\d{2}\s*-\s*(\d{1,2}:\d{2})/g)];
    if (ranges.length === 0) continue;
    foundWeekendHours = true;
    if (ranges.some((range) => closesAtMidnightOrLater(range[1]))) return true;
  }

  return foundWeekendHours ? false : null;
}

export function normalizePubTags(tags: Record<string, string> = {}): NormalizedPubTags {
  const cuisines = (tags.cuisine ?? '')
    .split(';')
    .map((cuisine) => cuisine.trim().toLowerCase())
    .filter(Boolean);

  return {
    hasFood: yesNoUnknown(tags.food),
    hasOutdoorSeating: yesNoUnknown(tags.outdoor_seating),
    hasLiveSport: yesNoUnknown(tags.live_sport),
    hasWifi: wifiValue(tags.internet_access),
    isOpenLate: determineIsOpenLate(tags.opening_hours),
    hasVeganOptions: yesNoUnknown(tags['diet:vegan']),
    hasVegetarianOptions: yesNoUnknown(tags['diet:vegetarian']),
    hasGlutenFreeOptions: yesNoUnknown(tags['diet:gluten_free']),
    cuisines,
    postcode: tags['addr:postcode'] ?? null,
    street: tags['addr:street'] ?? null,
    city: tags['addr:city'] ?? null,
    area: tags.area
      ?? tags['addr:suburb']
      ?? tags['addr:district']
      ?? tags['addr:neighbourhood']
      ?? tags['addr:place']
      ?? null,
  };
}

function normalizeSearchText(value: string): string {
  return value
    .toLocaleLowerCase()
    .replace(/\bburgers\b/g, 'burger')
    .replace(/\s+/g, ' ')
    .trim();
}

export function createPubSearchIndex(pub: Pub, normalizedTags: NormalizedPubTags): string {
  return normalizeSearchText([
    pub.name,
    pub.address,
    normalizedTags.postcode,
    normalizedTags.street,
    normalizedTags.city,
    normalizedTags.area,
    ...normalizedTags.cuisines,
  ].filter(Boolean).join(' '));
}

export function normalizePubSearchQuery(query: string): string {
  return normalizeSearchText(query);
}
