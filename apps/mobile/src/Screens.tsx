import { useMemo, useState } from 'react';
import {
  FlatList,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { cardShadow, colors } from './theme';
import { PubImage } from './PubImage';
import { formatPubAddress } from './pubDetails';
import {
  createPubSearchIndex,
  normalizePubSearchQuery,
  normalizePubTags,
  type NormalizedPubTags,
} from './normalizePubTags';
import type { Pub, Recommendation } from './types';

type SearchFilterKey =
  | 'visited'
  | 'saved'
  | 'hasFood'
  | 'hasOutdoorSeating'
  | 'hasLiveSport'
  | 'hasWifi'
  | 'isOpenLate'
  | 'hasVeganOptions'
  | 'hasVegetarianOptions'
  | 'hasGlutenFreeOptions';

const searchFilters: { key: SearchFilterKey; label: string }[] = [
  { key: 'visited', label: 'Visited' },
  { key: 'saved', label: 'Saved' },
  { key: 'hasFood', label: 'Food' },
  { key: 'hasOutdoorSeating', label: 'Outdoor seating' },
  { key: 'hasLiveSport', label: 'Live sport' },
  { key: 'hasWifi', label: 'Wi-Fi' },
  { key: 'isOpenLate', label: 'Open late' },
  { key: 'hasVeganOptions', label: 'Vegan options' },
  { key: 'hasVegetarianOptions', label: 'Vegetarian options' },
  { key: 'hasGlutenFreeOptions', label: 'Gluten-free options' },
];

function matchesSearchFilter(
  filter: SearchFilterKey,
  pub: Pub,
  normalizedTags: NormalizedPubTags,
  visitedIds: Set<string>,
  savedIds: Set<string>,
): boolean {
  if (filter === 'visited') return visitedIds.has(pub.id);
  if (filter === 'saved') return savedIds.has(pub.id);
  return normalizedTags[filter] === true;
}

function areaFor(pub: Pub) {
  const address = formatPubAddress(pub);
  if (!address) return pub.tags?.['addr:suburb'] ?? pub.tags?.['addr:city'] ?? 'London';
  const parts = address.split(',').map((part) => part.trim()).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 2] : parts[0];
}

function PubRow({ pub, onPress, visited, saved }: {
  pub: Pub;
  onPress: () => void;
  visited: boolean;
  saved: boolean;
}) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={styles.pubRow}>
      <PubImage pub={pub} style={styles.pubThumbnail} placeholderStyle={styles.pubMonogram} />
      <View style={styles.pubCopy}>
        <Text numberOfLines={1} style={styles.pubName}>{pub.name}</Text>
        <Text numberOfLines={1} style={styles.pubMeta}>⌖ {areaFor(pub)}</Text>
        <Text numberOfLines={1} style={styles.pubAddress}>{formatPubAddress(pub) ?? 'Location available on map'}</Text>
      </View>
      {(visited || saved) && (
        <Text style={styles.rowBadge}>{visited ? '✓' : '♥'}</Text>
      )}
    </Pressable>
  );
}

export function SearchScreen({ pubs, visitedIds, savedIds, recommendationApiUrl, onSelect }: {
  pubs: Pub[];
  visitedIds: Set<string>;
  savedIds: Set<string>;
  recommendationApiUrl?: string;
  onSelect: (pub: Pub) => void;
}) {
  const [query, setQuery] = useState('');
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [recommendationError, setRecommendationError] = useState<string | null>(null);
  const [selectedFilters, setSelectedFilters] = useState<Set<SearchFilterKey>>(new Set());
  const searchablePubs = useMemo(() => pubs.map((pub) => {
    const normalizedTags = normalizePubTags(pub.tags);
    return {
      pub,
      normalizedTags,
      searchIndex: createPubSearchIndex(pub, normalizedTags),
    };
  }), [pubs]);

  const results = useMemo(() => {
    const normalizedQuery = normalizePubSearchQuery(query);
    return searchablePubs
      .filter(({ pub, normalizedTags, searchIndex }) => {
        const matchesQuery = !normalizedQuery || searchIndex.includes(normalizedQuery);
        const matchesFilters = [...selectedFilters].every((filter) => (
          matchesSearchFilter(filter, pub, normalizedTags, visitedIds, savedIds)
        ));
        return matchesQuery && matchesFilters;
      })
      .map(({ pub }) => pub);
  }, [query, savedIds, searchablePubs, selectedFilters, visitedIds]);

  function toggleFilter(filter: SearchFilterKey) {
    setSelectedFilters((current) => {
      const next = new Set(current);
      if (next.has(filter)) next.delete(filter);
      else next.add(filter);
      return next;
    });
  }

  async function getRecommendations() {
    if (!query.trim() || !recommendationApiUrl) return;
    setRecommendationLoading(true);
    setRecommendationError(null);
    try {
      const response = await fetch(`${recommendationApiUrl}/recommendations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: query.trim(), limit: 5 }) });
      if (!response.ok) throw new Error(`Recommendations unavailable (${response.status})`);
      const data = await response.json() as { recommendations: Recommendation[] };
      setRecommendations(data.recommendations);
    } catch (reason) {
      setRecommendationError(`Unable to reach recommendations through ${recommendationApiUrl}. Check that the API and recommendation service are running.`);
      setRecommendations([]);
    } finally {
      setRecommendationLoading(false);
    }
  }

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.eyebrow}>DISCOVER LONDON</Text>
        <Text style={styles.title}>Search</Text>
        <Text style={styles.subtitle}>Find your next local across the city.</Text>
        <View style={styles.searchBox}>
          <Text style={styles.searchIcon}>⌕</Text>
          <TextInput
            accessibilityLabel="Search pubs"
            autoCorrect={false}
            onChangeText={setQuery}
            placeholder="Search pubs, areas, cuisines…"
            placeholderTextColor={colors.muted}
            style={styles.searchInput}
            value={query}
          />
          {query.length > 0 && <Pressable onPress={() => setQuery('')}><Text style={styles.clear}>×</Text></Pressable>}
        </View>
        {recommendationApiUrl && <Pressable disabled={recommendationLoading} onPress={getRecommendations} style={styles.recommendButton}><Text style={styles.recommendButtonText}>{recommendationLoading ? 'Finding your pubs…' : '✦ Recommend for me'}</Text></Pressable>}
        {recommendationError && <Text style={styles.recommendationError}>{recommendationError}</Text>}
        {recommendations.length > 0 && <View style={styles.recommendationCard}>
          <Text style={styles.recommendationTitle}>AI picks for you</Text>
          {recommendations.map((recommendation) => {
            const pub = pubs.find((candidate) => candidate.id === recommendation.venueId) ?? { id: recommendation.venueId, name: recommendation.name, latitude: recommendation.latitude, longitude: recommendation.longitude, address: recommendation.address, website: recommendation.website, phone: recommendation.phone, tags: {} };
            return <PubRow key={recommendation.venueId} pub={pub} onPress={() => onSelect(pub)} visited={visitedIds.has(pub.id)} saved={savedIds.has(pub.id)} />;
          })}
        </View>}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.filters}>
          {searchFilters.map(({ key, label }) => {
            const selected = selectedFilters.has(key);
            return (
              <Pressable
                accessibilityRole="checkbox"
                accessibilityState={{ checked: selected }}
                key={key}
                onPress={() => toggleFilter(key)}
                style={[styles.filter, selected && styles.filterActive]}
              >
                <Text style={[styles.filterText, selected && styles.filterTextActive]}>{label}</Text>
              </Pressable>
            );
          })}
        </ScrollView>
        {selectedFilters.size > 0 && (
          <View style={styles.clearFiltersRow}>
            <Text style={styles.selectedFilterCount}>{selectedFilters.size} selected</Text>
            <Pressable accessibilityRole="button" onPress={() => setSelectedFilters(new Set())}>
              <Text style={styles.clearAll}>Clear all</Text>
            </Pressable>
          </View>
        )}
      </View>
      <FlatList
        contentContainerStyle={styles.listContent}
        data={results}
        keyExtractor={(pub) => pub.id}
        ListHeaderComponent={<Text style={styles.resultCount}>{results.length} {results.length === 1 ? 'PUB' : 'PUBS'}</Text>}
        ListEmptyComponent={(
          <View style={styles.searchEmptyCard}>
            <Text style={styles.emptyTitle}>No pubs match those filters.</Text>
            <Text style={styles.empty}>Try removing a filter.</Text>
          </View>
        )}
        renderItem={({ item }) => (
          <PubRow pub={item} onPress={() => onSelect(item)} visited={visitedIds.has(item.id)} saved={savedIds.has(item.id)} />
        )}
      />
    </SafeAreaView>
  );
}

export function PassportScreen({ pubs, visitedIds, savedIds, onSelect }: {
  pubs: Pub[];
  visitedIds: Set<string>;
  savedIds: Set<string>;
  onSelect: (pub: Pub) => void;
}) {
  const visited = pubs.filter((pub) => visitedIds.has(pub.id));
  const percentage = pubs.length ? Math.round((visited.length / pubs.length) * 100) : 0;
  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        <Text style={styles.eyebrow}>YOUR PASSPORT</Text>
        <Text style={styles.title}>Every pint tells a story</Text>
        <View style={styles.progressCard}>
          <View>
            <Text style={styles.progressNumber}>{visited.length}</Text>
            <Text style={styles.progressCaption}>of {pubs.length} loaded pubs visited</Text>
          </View>
          <View style={styles.percentCircle}><Text style={styles.percentText}>{percentage}%</Text></View>
          <View style={styles.progressTrack}><View style={[styles.progressFill, { width: `${percentage}%` }]} /></View>
        </View>
        <View style={styles.statsRow}>
          <Stat value={`${visited.length}`} label="VISITED" />
          <Stat value={`${savedIds.size}`} label="SAVED" />
          <Stat value={`${pubs.length}`} label="DISCOVERED" />
        </View>
        <Text style={styles.sectionTitle}>Stamps collected</Text>
        {visited.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyTitle}>Your passport is ready</Text>
            <Text style={styles.empty}>Open a pub and mark it visited to collect your first stamp.</Text>
          </View>
        ) : visited.map((pub) => (
          <PubRow key={pub.id} pub={pub} onPress={() => onSelect(pub)} visited saved={savedIds.has(pub.id)} />
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return <View style={styles.stat}><Text style={styles.statValue}>{value}</Text><Text style={styles.statLabel}>{label}</Text></View>;
}

export function ProfileScreen({ pubs, visitedCount, savedCount }: { pubs: Pub[]; visitedCount: number; savedCount: number }) {
  const menu = [
    ['♥', 'Saved pubs', `${savedCount}`],
    ['✓', 'Visited pubs', `${visitedCount}`],
    ['⌖', 'Pubs loaded', `${pubs.length}`],
    ['◐', 'Appearance', 'Cream'],
  ];
  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        <Text style={styles.title}>Profile</Text>
        <View style={styles.identityCard}>
          <View style={styles.avatar}><Text style={styles.avatarText}>TS</Text></View>
          <View><Text style={styles.identityName}>Your pub profile</Text><Text style={styles.pubMeta}>⌖ London</Text><Text style={styles.level}>LOCAL EXPLORER</Text></View>
        </View>
        <View style={styles.statsRow}>
          <Stat value={`${visitedCount}`} label="VISITED" />
          <Stat value={`${savedCount}`} label="SAVED" />
          <Stat value={`${pubs.length}`} label="NEARBY" />
        </View>
        <View style={styles.menu}>
          {menu.map(([icon, label, value], index) => (
            <View key={label} style={[styles.menuRow, index > 0 && styles.menuBorder]}>
              <View style={styles.menuIcon}><Text style={styles.menuIconText}>{icon}</Text></View>
              <Text style={styles.menuLabel}>{label}</Text><Text style={styles.menuValue}>{value}  ›</Text>
            </View>
          ))}
        </View>
        <Text style={styles.quote}>“A pub is the heart of a village.”</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: { paddingHorizontal: 20, paddingTop: 12 },
  scrollContent: { paddingHorizontal: 20, paddingTop: 20, paddingBottom: 126 },
  eyebrow: { color: colors.primary, fontSize: 11, fontWeight: '800', letterSpacing: 2 },
  title: { color: colors.foreground, fontSize: 32, fontWeight: '700', marginTop: 4 },
  subtitle: { color: colors.muted, fontSize: 14, marginTop: 3 },
  searchBox: { flexDirection: 'row', alignItems: 'center', marginTop: 18, borderRadius: 99, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, paddingHorizontal: 15, ...cardShadow },
  recommendButton: { alignSelf: 'flex-start', marginTop: 10, paddingHorizontal: 14, paddingVertical: 9, borderRadius: 99, backgroundColor: colors.primary },
  recommendButtonText: { color: colors.primaryForeground, fontSize: 12, fontWeight: '800' },
  recommendationError: { marginTop: 8, color: colors.danger, fontSize: 12 },
  recommendationCard: { marginTop: 14, padding: 12, borderRadius: 16, backgroundColor: colors.card, ...cardShadow },
  recommendationTitle: { marginBottom: 4, color: colors.primary, fontSize: 13, fontWeight: '800' },
  searchIcon: { color: colors.muted, fontSize: 21 },
  searchInput: { flex: 1, color: colors.foreground, fontSize: 14, paddingHorizontal: 10, paddingVertical: 13 },
  clear: { color: colors.muted, fontSize: 24, paddingHorizontal: 4 },
  filters: { marginTop: 12 },
  filter: { borderRadius: 99, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, marginRight: 8, paddingHorizontal: 14, paddingVertical: 7 },
  filterActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  filterText: { color: colors.muted, fontSize: 12, fontWeight: '600' },
  filterTextActive: { color: colors.primaryForeground },
  clearFiltersRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingTop: 9 },
  selectedFilterCount: { color: colors.muted, fontSize: 11, fontWeight: '700' },
  clearAll: { color: colors.primary, fontSize: 12, fontWeight: '800' },
  listContent: { paddingHorizontal: 20, paddingTop: 14, paddingBottom: 126 },
  resultCount: { color: colors.muted, fontSize: 11, fontWeight: '800', letterSpacing: 1, marginBottom: 10 },
  pubRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 18, padding: 10, marginBottom: 10 },
  pubMonogram: { width: 58, height: 58, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.primary },
  pubThumbnail: { width: 58, height: 58, borderRadius: 14 },
  pubCopy: { flex: 1, paddingHorizontal: 12 },
  pubName: { color: colors.foreground, fontSize: 17, fontWeight: '700' },
  pubMeta: { color: colors.muted, fontSize: 12, marginTop: 3 },
  pubAddress: { color: colors.foreground, opacity: 0.72, fontSize: 12, marginTop: 4 },
  rowBadge: { color: colors.primary, backgroundColor: colors.primarySoft, borderRadius: 99, overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 5, fontWeight: '800' },
  empty: { color: colors.muted, fontSize: 14, lineHeight: 21, textAlign: 'center' },
  searchEmptyCard: { borderRadius: 18, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.border, backgroundColor: colors.card, padding: 28 },
  progressCard: { marginTop: 22, padding: 20, borderRadius: 26, backgroundColor: colors.primary, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  progressNumber: { color: colors.primaryForeground, fontSize: 48, fontWeight: '700' },
  progressCaption: { color: '#C7D0C9', fontSize: 13, marginTop: 2 },
  percentCircle: { width: 66, height: 66, alignItems: 'center', justifyContent: 'center', borderRadius: 99, borderWidth: 2, borderColor: colors.accent, backgroundColor: 'rgba(200,137,42,0.12)' },
  percentText: { color: colors.accent, fontSize: 19, fontWeight: '700' },
  progressTrack: { position: 'absolute', left: 20, right: 20, bottom: 16, height: 5, backgroundColor: 'rgba(244,238,225,0.18)', borderRadius: 99, overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: colors.accent, borderRadius: 99 },
  statsRow: { flexDirection: 'row', marginTop: 14, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, overflow: 'hidden' },
  stat: { flex: 1, alignItems: 'center', paddingVertical: 16 },
  statValue: { color: colors.foreground, fontSize: 20, fontWeight: '700' },
  statLabel: { color: colors.muted, fontSize: 10, fontWeight: '700', marginTop: 3 },
  sectionTitle: { color: colors.foreground, fontSize: 20, fontWeight: '700', marginTop: 26, marginBottom: 12 },
  emptyCard: { borderRadius: 18, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.border, backgroundColor: colors.card, padding: 28 },
  emptyTitle: { color: colors.foreground, fontSize: 17, fontWeight: '700', textAlign: 'center', marginBottom: 6 },
  identityCard: { flexDirection: 'row', alignItems: 'center', gap: 15, marginTop: 20, padding: 18, borderRadius: 24, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  avatar: { width: 64, height: 64, borderRadius: 99, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: colors.primaryForeground, fontSize: 22, fontWeight: '700' },
  identityName: { color: colors.foreground, fontSize: 20, fontWeight: '700' },
  level: { alignSelf: 'flex-start', color: colors.primary, fontSize: 10, fontWeight: '800', letterSpacing: 1, backgroundColor: colors.primarySoft, borderRadius: 99, overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 4, marginTop: 7 },
  menu: { marginTop: 14, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, overflow: 'hidden' },
  menuRow: { flexDirection: 'row', alignItems: 'center', padding: 13 },
  menuBorder: { borderTopWidth: 1, borderTopColor: colors.border },
  menuIcon: { width: 36, height: 36, borderRadius: 99, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  menuIconText: { color: colors.primary, fontWeight: '800' },
  menuLabel: { flex: 1, color: colors.foreground, fontSize: 14, fontWeight: '600', marginLeft: 11 },
  menuValue: { color: colors.muted, fontSize: 13 },
  quote: { color: colors.muted, fontSize: 14, fontStyle: 'italic', textAlign: 'center', marginTop: 28 },
});
