import 'package:flutter/material.dart';

@immutable
class Palette extends ThemeExtension<Palette> {
  const Palette({
    required this.surface,
    required this.ink,
    required this.muted,
    required this.hairline,
    required this.globe,
    required this.litter,
    required this.waste,
    required this.chart,
    required this.good,
    required this.warn,
    required this.critical,
  });

  final Color surface;
  final Color ink;
  final Color muted;
  final Color hairline;
  final Color globe;
  final Color litter;
  final Color waste;
  final Color chart;
  final Color good;
  final Color warn;
  final Color critical;

  @override
  Palette copyWith({Color? surface, Color? ink, Color? muted}) => Palette(
        surface: surface ?? this.surface,
        ink: ink ?? this.ink,
        muted: muted ?? this.muted,
        hairline: hairline,
        globe: globe,
        litter: litter,
        waste: waste,
        chart: chart,
        good: good,
        warn: warn,
        critical: critical,
      );

  @override
  Palette lerp(Palette? other, double t) {
    if (other == null) return this;
    Color mix(Color a, Color b) => Color.lerp(a, b, t)!;
    return Palette(
      surface: mix(surface, other.surface),
      ink: mix(ink, other.ink),
      muted: mix(muted, other.muted),
      hairline: mix(hairline, other.hairline),
      globe: mix(globe, other.globe),
      litter: mix(litter, other.litter),
      waste: mix(waste, other.waste),
      chart: mix(chart, other.chart),
      good: mix(good, other.good),
      warn: mix(warn, other.warn),
      critical: mix(critical, other.critical),
    );
  }
}

const _light = Palette(
  surface: Color(0xFFEEF2F6),
  ink: Color(0xFF15202B),
  muted: Color(0xFF56636F),
  hairline: Color(0xFFD3DBE3),
  globe: Color(0xFFFFFFFF),
  litter: Color(0xFFC9AE7C),
  waste: Color(0xFF8A7350),
  chart: Color(0xFF1B5CAB),
  good: Color(0xFF1B7A4C),
  warn: Color(0xFF9A5800),
  critical: Color(0xFFB3261E),
);

const _dark = Palette(
  surface: Color(0xFF0F151B),
  ink: Color(0xFFE7EDF3),
  muted: Color(0xFF9AA9B7),
  hairline: Color(0xFF26313C),
  globe: Color(0xFF1A222B),
  litter: Color(0xFFBBA173),
  waste: Color(0xFF9C8660),
  chart: Color(0xFF4F93EA),
  good: Color(0xFF69CF9C),
  warn: Color(0xFFEDB35C),
  critical: Color(0xFFFF8E85),
);

ThemeData buildTheme(Brightness brightness) {
  final isDark = brightness == Brightness.dark;
  final palette = isDark ? _dark : _light;
  final action = isDark ? const Color(0xFF86B7F2) : const Color(0xFF1B5CAB);
  final onAction = isDark ? const Color(0xFF0B2545) : Colors.white;
  final scheme = ColorScheme(
    brightness: brightness,
    primary: action,
    onPrimary: onAction,
    secondary: action,
    onSecondary: onAction,
    secondaryContainer: action.withValues(alpha: isDark ? 0.24 : 0.14),
    onSecondaryContainer: palette.ink,
    error: palette.critical,
    onError: isDark ? const Color(0xFF3A0B08) : Colors.white,
    surface: palette.surface,
    onSurface: palette.ink,
    onSurfaceVariant: palette.muted,
    outline: palette.muted,
    outlineVariant: palette.hairline,
    surfaceContainerHigh: palette.globe,
    surfaceContainerHighest: palette.globe,
  );
  const buttonShape = RoundedRectangleBorder(
    borderRadius: BorderRadius.all(Radius.circular(12)),
  );
  const buttonSize = Size(0, 52);
  const buttonText = TextStyle(fontSize: 16, fontWeight: FontWeight.w700);
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    fontFamily: 'Atkinson',
    scaffoldBackgroundColor: palette.surface,
    extensions: [palette],
    dividerTheme: DividerThemeData(color: palette.hairline, space: 1, thickness: 1),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        shape: buttonShape,
        minimumSize: buttonSize,
        textStyle: buttonText,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        shape: buttonShape,
        minimumSize: buttonSize,
        textStyle: buttonText,
        side: BorderSide(color: palette.muted),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: palette.surface,
      surfaceTintColor: Colors.transparent,
      indicatorColor: scheme.secondaryContainer,
      height: 68,
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: palette.surface,
      indicatorColor: scheme.secondaryContainer,
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
    dialogTheme: DialogThemeData(
      backgroundColor: palette.globe,
      surfaceTintColor: Colors.transparent,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(20)),
      ),
    ),
  );
}

extension PaletteOf on BuildContext {
  Palette get palette => Theme.of(this).extension<Palette>()!;
}
