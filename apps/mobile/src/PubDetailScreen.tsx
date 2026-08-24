import { useMemo } from 'react';
import {
  Linking,
  Platform,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { normalizePubTags } from './normalizePubTags';
import {
  formatPubAddress,
  normalizeCuisine,
  normalizeOpeningHours,
  normalizeOperator,
  normalizePhone,
  normalizeWebsite,
} from './pubDetails';
import { PubImage } from './PubImage';
import { cardShadow, colors } from './theme';
import type { Pub } from './types';

const generatedTagLabels: { key: keyof ReturnType<typeof normalizePubTags>; label: string }[] = [
  { key: 'hasFood', label: 'Food' },
  { key: 'hasOutdoorSeating', label: 'Outdoor seating' },
  { key: 'hasLiveSport', label: 'Live sport' },
  { key: 'hasWifi', label: 'Wi-Fi' },
  { key: 'hasVeganOptions', label: 'Vegan options' },
  { key: 'hasVegetarianOptions', label: 'Vegetarian options' },
  { key: 'hasGlutenFreeOptions', label: 'Gluten-free options' },
];

function openExternalUrl(url: string) {
  Linking.openURL(url).catch(() => undefined);
}

function directionsUrl(pub: Pub): string {
  const destination = `${pub.latitude},${pub.longitude}`;
  if (Platform.OS === 'ios') return `http://maps.apple.com/?daddr=${destination}`;
  return `geo:${destination}?q=${destination}`;
}

function hoursSourceLabel(pub: Pub): string | null {
  if (pub.hoursSource === 'manual') return 'Hours from manual correction';
  if (pub.hoursSource === 'official') return 'Hours from official website';
  if (pub.hoursSource === 'osm' || (!pub.hoursSource && pub.tags?.opening_hours)) return 'Hours from OpenStreetMap';
  return null;
}

export function PubDetailScreen({ pub, saved, visited, onBack, onToggleSaved, onToggleVisited }: {
  pub: Pub;
  saved: boolean;
  visited: boolean;
  onBack: () => void;
  onToggleSaved: () => void;
  onToggleVisited: () => void;
}) {
  const normalizedTags = useMemo(() => normalizePubTags(pub.tags), [pub.tags]);
  const address = formatPubAddress(pub);
  const phone = normalizePhone(pub);
  const website = normalizeWebsite(pub);
  const operator = normalizeOperator(pub);
  const cuisine = normalizeCuisine(pub);
  const openingHours = normalizeOpeningHours(pub);
  const tags = generatedTagLabels.filter(({ key }) => normalizedTags[key] === true).map(({ label }) => label);
  const allTags = [...tags, ...cuisine.map((item) => item.replace(/\b\w/g, (letter) => letter.toUpperCase()))];

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content}>
        <Pressable accessibilityRole="button" accessibilityLabel="Back" onPress={onBack} style={styles.backButton}>
          <Text style={styles.backText}>‹  Back</Text>
        </Pressable>
        <PubImage pub={pub} style={styles.pubImage} />
        <Text style={styles.title}>{pub.name}</Text>
        <Text style={styles.address}>⌖ {address ?? 'Location available on map'}</Text>

        {allTags.length > 0 && (
          <View style={styles.tagRow}>
            {allTags.map((tag) => <Text key={tag} style={styles.tag}>{tag}</Text>)}
          </View>
        )}

        <View style={styles.actionRow}>
          <Pressable onPress={() => openExternalUrl(directionsUrl(pub))} style={styles.primaryAction}>
            <Text style={styles.primaryActionText}>⌖  Directions</Text>
          </Pressable>
          <Pressable accessibilityLabel={saved ? 'Remove from saved pubs' : 'Save pub'} onPress={onToggleSaved} style={[styles.iconAction, saved && styles.iconActionActive]}>
            <Text style={[styles.heart, saved && styles.heartActive]}>{saved ? '♥' : '♡'}</Text>
          </Pressable>
        </View>
        <Pressable onPress={onToggleVisited} style={[styles.visitedAction, visited && styles.visitedActionActive]}>
          <Text style={styles.visitedActionText}>{visited ? '✓  Marked as visited' : 'Mark as visited'}</Text>
        </Pressable>

        <DetailSection title="Opening hours">
          {hoursSourceLabel(pub) && <Text style={styles.sourceLabel}>{hoursSourceLabel(pub)}</Text>}
          {openingHours.parsed ? openingHours.rows.map((row) => (
            <View key={row.day} style={styles.hoursRow}><Text style={styles.day}>{row.day}</Text><Text style={styles.hours}>{row.hours}</Text></View>
          )) : <Text style={styles.body}>{openingHours.raw ?? 'Opening hours unavailable'}</Text>}
        </DetailSection>

        {(phone || website || operator || cuisine.length > 0) && <DetailSection title="Contact and details">
          {phone && <Pressable onPress={() => openExternalUrl(`tel:${phone}`)} style={styles.infoRow}><Text style={styles.infoLabel}>Phone</Text><Text style={styles.link}>{phone}</Text></Pressable>}
          {website && <Pressable onPress={() => openExternalUrl(website)} style={styles.infoRow}><Text style={styles.infoLabel}>Website</Text><Text numberOfLines={1} style={styles.link}>{website.replace(/^https?:\/\//i, '')}</Text></Pressable>}
          {operator && <View style={styles.infoRow}><Text style={styles.infoLabel}>Operator</Text><Text style={styles.body}>{operator}</Text></View>}
          {cuisine.length > 0 && <View style={styles.infoRow}><Text style={styles.infoLabel}>Cuisine</Text><Text style={styles.body}>{cuisine.join(', ')}</Text></View>}
        </DetailSection>}
      </ScrollView>
    </SafeAreaView>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text><View style={styles.card}>{children}</View></View>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 20, paddingBottom: 36 },
  backButton: { alignSelf: 'flex-start', paddingVertical: 5, marginBottom: 12 },
  backText: { color: colors.primary, fontSize: 15, fontWeight: '800' },
  pubImage: { width: '100%', height: 220, borderRadius: 16, backgroundColor: colors.primarySoft },
  title: { color: colors.foreground, fontSize: 30, lineHeight: 36, fontWeight: '700', marginTop: 18 },
  address: { color: colors.muted, fontSize: 14, lineHeight: 21, marginTop: 6 },
  tagRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginTop: 15 },
  tag: { color: colors.foreground, fontSize: 11, fontWeight: '600', overflow: 'hidden', borderRadius: 99, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, paddingHorizontal: 10, paddingVertical: 6 },
  actionRow: { flexDirection: 'row', gap: 10, marginTop: 20 },
  primaryAction: { flex: 1, alignItems: 'center', justifyContent: 'center', minHeight: 49, borderRadius: 99, backgroundColor: colors.primary, ...cardShadow },
  primaryActionText: { color: colors.primaryForeground, fontSize: 14, fontWeight: '800' },
  iconAction: { width: 49, height: 49, borderRadius: 99, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  iconActionActive: { borderColor: colors.accent, backgroundColor: '#F5E6CD' },
  heart: { color: colors.foreground, fontSize: 24 },
  heartActive: { color: colors.accent },
  visitedAction: { alignItems: 'center', marginTop: 10, paddingVertical: 13, borderRadius: 99, borderWidth: 1, borderColor: colors.primary },
  visitedActionActive: { backgroundColor: colors.primarySoft },
  visitedActionText: { color: colors.primary, fontSize: 13, fontWeight: '800' },
  section: { marginTop: 24 },
  sectionTitle: { color: colors.foreground, fontSize: 19, fontWeight: '700', marginBottom: 10 },
  sourceLabel: { color: colors.muted, fontSize: 11, fontWeight: '700', marginBottom: 7 },
  card: { padding: 15, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  hoursRow: { flexDirection: 'row', paddingVertical: 5 },
  day: { width: 104, color: colors.foreground, fontSize: 13, fontWeight: '700' },
  hours: { flex: 1, color: colors.muted, fontSize: 13 },
  body: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  infoRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 7 },
  infoLabel: { width: 74, color: colors.foreground, fontSize: 13, fontWeight: '700' },
  link: { flex: 1, color: colors.primary, fontSize: 14, fontWeight: '700' },
});
