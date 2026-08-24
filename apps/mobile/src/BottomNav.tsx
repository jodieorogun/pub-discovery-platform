import { Pressable, StyleSheet, Text, View } from 'react-native';
import { cardShadow, colors } from './theme';
import type { Tab } from './types';

const items: { id: Tab; label: string; icon: string }[] = [
  { id: 'map', label: 'Map', icon: '⌖' },
  { id: 'search', label: 'Search', icon: '⌕' },
  { id: 'passport', label: 'Passport', icon: '▣' },
  { id: 'profile', label: 'Profile', icon: '●' },
];

export function BottomNav({ active, onChange }: { active: Tab; onChange: (tab: Tab) => void }) {
  return (
    <View pointerEvents="box-none" style={styles.wrap}>
      <View style={styles.bar}>
        {items.map((item) => {
          const selected = item.id === active;
          return (
            <Pressable
              accessibilityLabel={item.label}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              key={item.id}
              onPress={() => onChange(item.id)}
              style={styles.item}
            >
              <View style={[styles.iconWrap, selected && styles.iconWrapActive]}>
                <Text style={[styles.icon, selected && styles.active]}>{item.icon}</Text>
              </View>
              <Text style={[styles.label, selected && styles.active]}>{item.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'absolute', left: 0, right: 0, bottom: 0, padding: 16 },
  bar: {
    flexDirection: 'row',
    borderRadius: 26,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: 'rgba(251, 248, 240, 0.97)',
    padding: 8,
    ...cardShadow,
  },
  item: { flex: 1, alignItems: 'center', paddingVertical: 3 },
  iconWrap: { height: 34, width: 48, borderRadius: 99, alignItems: 'center', justifyContent: 'center' },
  iconWrapActive: { backgroundColor: colors.primarySoft },
  icon: { color: colors.muted, fontSize: 19, fontWeight: '700' },
  label: { color: colors.muted, fontSize: 11, fontWeight: '600', marginTop: 3 },
  active: { color: colors.primary },
});
