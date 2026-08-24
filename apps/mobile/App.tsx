import { StatusBar } from 'expo-status-bar';
import { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Linking,
  Modal,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import MapView, { Marker, Region } from 'react-native-maps';
import { BottomNav } from './src/BottomNav';
import { PubDetailScreen } from './src/PubDetailScreen';
import { formatPubAddress } from './src/pubDetails';
import { PassportScreen, ProfileScreen, SearchScreen } from './src/Screens';
import { cardShadow, colors } from './src/theme';
import type { MapFilter, Pub, Tab } from './src/types';

declare const process: { env: { EXPO_PUBLIC_API_URL?: string } };

const LONDON: Region = {
  latitude: 51.5074,
  longitude: -0.1278,
  latitudeDelta: 0.16,
  longitudeDelta: 0.16,
};

const rawApiUrl = process.env.EXPO_PUBLIC_API_URL;
const API_URL = rawApiUrl?.endsWith('/') ? rawApiUrl.slice(0, -1) : rawApiUrl;
const filters: MapFilter[] = ['Pins', 'Visited', 'Saved'];

export default function App() {
  const [pubs, setPubs] = useState<Pub[]>([]);
  const [selectedPub, setSelectedPub] = useState<Pub | null>(null);
  const [tab, setTab] = useState<Tab>('map');
  const [mapFilter, setMapFilter] = useState<MapFilter>('Pins');
  const [visitedIds, setVisitedIds] = useState<Set<string>>(new Set());
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!API_URL) {
      setError('Add EXPO_PUBLIC_API_URL to mobile/.env to load pubs.');
      setLoading(false);
      return;
    }

    fetch(`${API_URL}/pubs?limit=2000`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        return response.json() as Promise<{ pubs: Pub[] }>;
      })
      .then((data) => setPubs(data.pubs))
      .catch((reason: Error) => setError(`Unable to reach the API at ${API_URL}. Check that the API is running and that this device can reach your computer.`))
      .finally(() => setLoading(false));
  }, []);

  const visiblePubs = useMemo(() => {
    if (mapFilter === 'Visited') return pubs.filter((pub) => visitedIds.has(pub.id));
    if (mapFilter === 'Saved') return pubs.filter((pub) => savedIds.has(pub.id));
    return pubs;
  }, [mapFilter, pubs, savedIds, visitedIds]);

  function toggleInSet(id: string, update: (value: Set<string>) => void, current: Set<string>) {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    update(next);
  }

  function changeTab(nextTab: Tab) {
    setSelectedPub(null);
    setTab(nextTab);
  }

  function mergePub(updated: Partial<Pub> & { id: string }) {
    setPubs((current) => current.map((pub) => pub.id === updated.id ? { ...pub, ...updated } : pub));
    setSelectedPub((current) => current?.id === updated.id ? { ...current, ...updated } : current);
  }

  function openPub(pub: Pub) {
    setSelectedPub(pub);
    if (!API_URL) return;
    const hoursCheckedAt = pub.hoursLastChecked ? Date.parse(pub.hoursLastChecked) : 0;
    const hoursAreStale = !hoursCheckedAt || Date.now() - hoursCheckedAt > 30 * 24 * 60 * 60 * 1000;
    const refreshes: Promise<void>[] = [];
    if (hoursAreStale && pub.hoursSource !== 'manual' && pub.website) {
      refreshes.push(fetch(`${API_URL}/pubs/${encodeURIComponent(pub.id)}/refresh-hours`, { method: 'POST' })
        .then((response) => response.ok ? response.json() : null)
        .then((data: { openingHours?: string | null; hoursSource?: Pub['hoursSource']; hoursLastChecked?: string | null; hoursConfidence?: string | null; phone?: string | null; address?: string | null } | null) => {
          if (data) mergePub({ id: pub.id, openingHours: data.openingHours, hoursSource: data.hoursSource, hoursLastChecked: data.hoursLastChecked, hoursConfidence: data.hoursConfidence, phone: data.phone ?? pub.phone, address: data.address ?? pub.address });
        }).catch(() => undefined));
    }
    const addressCheckedAt = pub.addressLastChecked ? Date.parse(pub.addressLastChecked) : 0;
    const addressNeedsRefresh = !formatPubAddress(pub) && (!addressCheckedAt || Date.now() - addressCheckedAt > 180 * 24 * 60 * 60 * 1000);
    if (addressNeedsRefresh && pub.latitude && pub.longitude) {
      refreshes.push(fetch(`${API_URL}/pubs/${encodeURIComponent(pub.id)}/refresh-address`, { method: 'POST' })
        .then((response) => response.ok ? response.json() : null)
        .then((data: { address?: string | null; addressSource?: Pub['addressSource']; addressLastChecked?: string | null } | null) => {
          if (data) mergePub({ id: pub.id, address: data.address ?? pub.address, addressSource: data.addressSource, addressLastChecked: data.addressLastChecked });
        }).catch(() => undefined));
    }
    void Promise.all(refreshes);
  }

  return (
    <View style={styles.root}>
      <StatusBar style="dark" />
      {selectedPub ? (
        <PubDetailScreen
          pub={selectedPub}
          saved={savedIds.has(selectedPub.id)}
          visited={visitedIds.has(selectedPub.id)}
          onBack={() => setSelectedPub(null)}
          onToggleSaved={() => toggleInSet(selectedPub.id, setSavedIds, savedIds)}
          onToggleVisited={() => toggleInSet(selectedPub.id, setVisitedIds, visitedIds)}
        />
      ) : (
        <>
          {tab === 'map' && (
            <MapScreen
              error={error}
              filter={mapFilter}
              loading={loading}
              onFilterChange={setMapFilter}
              onOpenSearch={() => changeTab('search')}
              onSelect={openPub}
              pubs={visiblePubs}
              savedIds={savedIds}
              visitedIds={visitedIds}
            />
          )}
          {tab === 'search' && (
            <SearchScreen pubs={pubs} visitedIds={visitedIds} savedIds={savedIds} recommendationApiUrl={API_URL} onSelect={openPub} />
          )}
          {tab === 'passport' && (
            <PassportScreen pubs={pubs} visitedIds={visitedIds} savedIds={savedIds} onSelect={openPub} />
          )}
          {tab === 'profile' && (
            <ProfileScreen pubs={pubs} visitedCount={visitedIds.size} savedCount={savedIds.size} />
          )}
          <BottomNav active={tab} onChange={changeTab} />
        </>
      )}
    </View>
  );
}

function MapScreen({ pubs, loading, error, filter, visitedIds, savedIds, onFilterChange, onOpenSearch, onSelect }: {
  pubs: Pub[];
  loading: boolean;
  error: string | null;
  filter: MapFilter;
  visitedIds: Set<string>;
  savedIds: Set<string>;
  onFilterChange: (filter: MapFilter) => void;
  onOpenSearch: () => void;
  onSelect: (pub: Pub) => void;
}) {
  return (
    <View style={styles.mapRoot}>
      <MapView style={StyleSheet.absoluteFill} initialRegion={LONDON}>
        {pubs.map((pub) => (
          <Marker
            key={pub.id}
            coordinate={{ latitude: pub.latitude, longitude: pub.longitude }}
            onPress={() => onSelect(pub)}
            pinColor={visitedIds.has(pub.id) ? colors.primary : savedIds.has(pub.id) ? colors.accent : colors.danger}
            title={pub.name}
          />
        ))}
      </MapView>
      <SafeAreaView pointerEvents="box-none" style={styles.mapOverlay}>
        <Pressable accessibilityRole="button" onPress={onOpenSearch} style={styles.mapSearch}>
          <Text style={styles.mapSearchIcon}>⌕</Text>
          <Text style={styles.mapSearchText}>Search pubs or areas…</Text>
          <View style={styles.filterBadge}><Text style={styles.filterBadgeText}>≡</Text></View>
        </Pressable>
        <View style={styles.mapFilters}>
          {filters.map((item) => (
            <Pressable key={item} onPress={() => onFilterChange(item)} style={[styles.mapFilter, item === filter && styles.mapFilterActive]}>
              <Text style={[styles.mapFilterText, item === filter && styles.mapFilterTextActive]}>{item}</Text>
            </Pressable>
          ))}
        </View>
        <View style={styles.mapCount}>
          {loading ? <ActivityIndicator color={colors.primary} /> : <Text style={styles.mapCountText}>{pubs.length} {filter.toLowerCase()} pubs</Text>}
        </View>
        {error && <Text style={styles.error}>{error}</Text>}
      </SafeAreaView>
    </View>
  );
}

function PubDetails({ pub, saved, visited, onClose, onToggleSaved, onToggleVisited }: {
  pub: Pub | null;
  saved: boolean;
  visited: boolean;
  onClose: () => void;
  onToggleSaved: () => void;
  onToggleVisited: () => void;
}) {
  if (!pub) return null;
  const directionsUrl = `https://www.google.com/maps/dir/?api=1&destination=${pub.latitude},${pub.longitude}`;
  return (
    <Modal animationType="slide" onRequestClose={onClose} transparent visible>
      <Pressable accessibilityLabel="Close pub details" onPress={onClose} style={styles.backdrop} />
      <SafeAreaView style={styles.sheet}>
        <View style={styles.grabber} />
        <ScrollView contentContainerStyle={styles.sheetContent}>
          <View style={styles.hero}>
            <Text style={styles.heroLetter}>{pub.name.slice(0, 1).toUpperCase()}</Text>
            <Pressable accessibilityLabel="Close" onPress={onClose} style={styles.closeButton}><Text style={styles.closeText}>×</Text></Pressable>
            {visited && <Text style={styles.visitedBadge}>✓ VISITED</Text>}
          </View>
          <Text style={styles.detailTitle}>{pub.name}</Text>
          <Text style={styles.detailAddress}>⌖ {pub.address ?? 'Address not listed'}</Text>
          <View style={styles.detailTags}>
            {pub.website && <Text style={styles.detailTag}>Website</Text>}
            {pub.phone && <Text style={styles.detailTag}>Phone</Text>}
            <Text style={styles.detailTag}>London pub</Text>
          </View>
          {(pub.phone || pub.website) && (
            <View style={styles.contactCard}>
              {pub.phone && <Pressable onPress={() => Linking.openURL(`tel:${pub.phone}`)}><Text style={styles.contactLink}>Call {pub.phone}</Text></Pressable>}
              {pub.website && <Pressable onPress={() => Linking.openURL(pub.website!)}><Text numberOfLines={1} style={styles.contactLink}>Open website</Text></Pressable>}
            </View>
          )}
          <View style={styles.actionRow}>
            <Pressable onPress={() => Linking.openURL(directionsUrl)} style={styles.primaryAction}><Text style={styles.primaryActionText}>⌖  Directions</Text></Pressable>
            <Pressable accessibilityLabel={saved ? 'Remove from saved pubs' : 'Save pub'} onPress={onToggleSaved} style={[styles.iconAction, saved && styles.iconActionActive]}><Text style={[styles.heart, saved && styles.heartActive]}>{saved ? '♥' : '♡'}</Text></Pressable>
          </View>
          <Pressable onPress={onToggleVisited} style={[styles.visitedAction, visited && styles.visitedActionActive]}>
            <Text style={[styles.visitedActionText, visited && styles.visitedActionTextActive]}>{visited ? '✓ Marked as visited' : 'Mark as visited'}</Text>
          </Pressable>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  mapRoot: { flex: 1, backgroundColor: colors.background },
  mapOverlay: { ...StyleSheet.absoluteFillObject, paddingHorizontal: 16 },
  mapSearch: { flexDirection: 'row', alignItems: 'center', marginTop: 8, paddingHorizontal: 16, paddingVertical: 12, borderRadius: 99, borderWidth: 1, borderColor: colors.border, backgroundColor: 'rgba(251,248,240,0.97)', ...cardShadow },
  mapSearchIcon: { color: colors.muted, fontSize: 21 },
  mapSearchText: { flex: 1, color: colors.muted, fontSize: 14, marginLeft: 10 },
  filterBadge: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center', borderRadius: 99, backgroundColor: colors.primarySoft },
  filterBadgeText: { color: colors.primary, fontWeight: '800', fontSize: 18 },
  mapFilters: { alignSelf: 'center', flexDirection: 'row', marginTop: 12, padding: 4, borderRadius: 99, borderWidth: 1, borderColor: colors.border, backgroundColor: 'rgba(251,248,240,0.97)', ...cardShadow },
  mapFilter: { paddingHorizontal: 16, paddingVertical: 7, borderRadius: 99 },
  mapFilterActive: { backgroundColor: colors.primary },
  mapFilterText: { color: colors.muted, fontSize: 12, fontWeight: '600' },
  mapFilterTextActive: { color: colors.primaryForeground },
  mapCount: { alignSelf: 'flex-start', minHeight: 35, justifyContent: 'center', marginTop: 12, paddingHorizontal: 12, borderRadius: 99, backgroundColor: 'rgba(251,248,240,0.94)', ...cardShadow },
  mapCountText: { color: colors.foreground, fontSize: 12, fontWeight: '700' },
  error: { marginTop: 10, padding: 11, overflow: 'hidden', borderRadius: 12, color: '#721C24', backgroundColor: '#F8D7DA' },
  backdrop: { flex: 1, backgroundColor: 'rgba(42,38,32,0.24)' },
  sheet: { maxHeight: '78%', borderTopLeftRadius: 30, borderTopRightRadius: 30, backgroundColor: colors.card, overflow: 'hidden' },
  grabber: { alignSelf: 'center', width: 42, height: 5, borderRadius: 99, backgroundColor: colors.border, marginTop: 10 },
  sheetContent: { padding: 20, paddingBottom: 30 },
  hero: { height: 145, borderRadius: 20, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.primary },
  heroLetter: { color: colors.primaryForeground, fontSize: 64, fontWeight: '700', opacity: 0.9 },
  closeButton: { position: 'absolute', top: 11, right: 11, width: 34, height: 34, borderRadius: 99, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(251,248,240,0.92)' },
  closeText: { color: colors.foreground, fontSize: 25, lineHeight: 28 },
  visitedBadge: { position: 'absolute', left: 12, top: 12, color: colors.primary, backgroundColor: colors.primaryForeground, overflow: 'hidden', borderRadius: 99, paddingHorizontal: 9, paddingVertical: 5, fontSize: 10, fontWeight: '800' },
  detailTitle: { color: colors.foreground, fontSize: 27, lineHeight: 33, fontWeight: '700', marginTop: 16 },
  detailAddress: { color: colors.muted, fontSize: 14, lineHeight: 20, marginTop: 6 },
  detailTags: { flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginTop: 15 },
  detailTag: { color: colors.foreground, fontSize: 11, fontWeight: '600', overflow: 'hidden', borderRadius: 99, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, paddingHorizontal: 10, paddingVertical: 5 },
  contactCard: { gap: 10, marginTop: 16, padding: 15, borderRadius: 16, backgroundColor: colors.background },
  contactLink: { color: colors.primary, fontSize: 14, fontWeight: '700' },
  actionRow: { flexDirection: 'row', gap: 10, marginTop: 18 },
  primaryAction: { flex: 1, alignItems: 'center', justifyContent: 'center', minHeight: 49, borderRadius: 99, backgroundColor: colors.primary },
  primaryActionText: { color: colors.primaryForeground, fontSize: 14, fontWeight: '800' },
  iconAction: { width: 49, height: 49, borderRadius: 99, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  iconActionActive: { borderColor: colors.accent, backgroundColor: '#F5E6CD' },
  heart: { color: colors.foreground, fontSize: 24 },
  heartActive: { color: colors.accent },
  visitedAction: { alignItems: 'center', marginTop: 10, paddingVertical: 13, borderRadius: 99, borderWidth: 1, borderColor: colors.primary },
  visitedActionActive: { backgroundColor: colors.primarySoft },
  visitedActionText: { color: colors.primary, fontSize: 13, fontWeight: '800' },
  visitedActionTextActive: { color: colors.primary },
});
