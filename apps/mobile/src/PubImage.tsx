import { useEffect, useState } from 'react';
import { Image, StyleSheet, Text, View, type ImageStyle, type StyleProp, type ViewStyle } from 'react-native';
import { resolvePubImage, type PubImageResolution } from './pubDetails';
import { colors } from './theme';
import type { Pub } from './types';

export function PubImage({ pub, style, placeholderStyle }: {
  pub: Pub;
  style: StyleProp<ImageStyle>;
  placeholderStyle?: StyleProp<ViewStyle>;
}) {
  const [resolution, setResolution] = useState<PubImageResolution | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setFailed(false);
    resolvePubImage(pub).then((result) => {
      if (active) {
        setResolution(result);
        setLoading(false);
      }
    }).catch(() => {
      if (active) {
        setResolution(null);
        setLoading(false);
      }
    });
    return () => { active = false; };
  }, [pub]);

  if (loading) return <View style={[styles.placeholder, style, placeholderStyle]}><Text style={styles.placeholderText}>Loading photo…</Text></View>;
  if (!resolution || failed) return <View style={[styles.placeholder, style, placeholderStyle]}><Text style={styles.placeholderLetter}>{pub.name.slice(0, 1).toUpperCase()}</Text><Text style={styles.placeholderText}>No pub photo available</Text></View>;

  return (
    <Image
      accessibilityLabel={`${pub.name} pub photo`}
      onError={(event) => {
        if (pub.name.trim().toLowerCase() === 'king of prussia') console.log('pubImageFailed', { imageUrl: resolution.imageUrl, error: event.nativeEvent.error });
        setFailed(true);
      }}
      onLoad={() => {
        if (pub.name.trim().toLowerCase() === 'king of prussia') console.log('pubImageLoaded', resolution.imageUrl);
      }}
      resizeMode="cover"
      source={{ uri: resolution.imageUrl }}
      style={style}
    />
  );
}

const styles = StyleSheet.create({
  placeholder: { alignItems: 'center', justifyContent: 'center', overflow: 'hidden', backgroundColor: colors.primary },
  placeholderLetter: { color: colors.primaryForeground, fontSize: 22, fontWeight: '700' },
  placeholderText: { color: colors.primaryForeground, fontSize: 10, fontWeight: '700', marginTop: 3, textAlign: 'center' },
});
