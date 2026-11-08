import 'package:flutter/material.dart';

/// A cat's color when it has no photo: the circle and the letter on it.
@immutable
class CatColor {
  const CatColor(this.background, this.foreground);

  final Color background;
  final Color foreground;
}

/// Every color of the design, in light and dark. Screens read these through
/// `context.palette` and never hard-code a color.
@immutable
class Palette extends ThemeExtension<Palette> {
  const Palette({
    required this.isDark,
    required this.bg,
    required this.card,
    required this.ink,
    required this.sub,
    required this.line,
    required this.lineSoft,
    required this.primary,
    required this.onPrimary,
    required this.soft,
    required this.selected,
    required this.track,
    required this.fill,
    required this.body,
    required this.bodyDark,
    required this.warn,
    required this.warnSoft,
    required this.danger,
    required this.chart,
    required this.avg,
    required this.shadow,
    required this.navShadow,
    required this.catColors,
  });

  final bool isDark;

  /// Page background.
  final Color bg;

  /// Cards, the top bar and the navigation.
  final Color card;
  final Color ink;

  /// Secondary text and inactive icons.
  final Color sub;

  /// Hairlines next to section titles and between rows.
  final Color line;

  /// The lighter divider inside charts.
  final Color lineSoft;
  final Color primary;
  final Color onPrimary;

  /// Tinted background for chips, banners and the selected rail item.
  final Color soft;

  /// The chosen segment inside a [soft] pill group.
  final Color selected;
  final Color track;
  final Color fill;
  final Color body;
  final Color bodyDark;
  final Color warn;
  final Color warnSoft;
  final Color danger;

  /// The data line of a chart.
  final Color chart;

  /// The average line of a chart.
  final Color avg;
  final List<BoxShadow> shadow;
  final List<BoxShadow> navShadow;
  final List<CatColor> catColors;

  /// Fill and text of a control that cannot be pressed. Opacity would wash
  /// them out below readable contrast, so they are fixed colors instead.
  Color get disabledFill => track;
  Color get disabledInk => sub;

  /// Placeholder text: lighter than [sub] so it does not read as an entered
  /// value, and still 3:1 against the field fills in both modes.
  Color get hintInk =>
      isDark ? const Color(0xFF72857D) : const Color(0xFF78887F);

  /// The color for the cat with this [colorIndex], wrapping around the palette
  /// because the server's index never wraps.
  CatColor catColor(int colorIndex) =>
      catColors[colorIndex.abs() % catColors.length];

  @override
  Palette copyWith({Color? primary}) => Palette(
    isDark: isDark,
    bg: bg,
    card: card,
    ink: ink,
    sub: sub,
    line: line,
    lineSoft: lineSoft,
    primary: primary ?? this.primary,
    onPrimary: onPrimary,
    soft: soft,
    selected: selected,
    track: track,
    fill: fill,
    body: body,
    bodyDark: bodyDark,
    warn: warn,
    warnSoft: warnSoft,
    danger: danger,
    chart: chart,
    avg: avg,
    shadow: shadow,
    navShadow: navShadow,
    catColors: catColors,
  );

  @override
  Palette lerp(Palette? other, double t) {
    if (other == null) return this;
    Color mix(Color a, Color b) => Color.lerp(a, b, t)!;
    return Palette(
      isDark: t < 0.5 ? isDark : other.isDark,
      bg: mix(bg, other.bg),
      card: mix(card, other.card),
      ink: mix(ink, other.ink),
      sub: mix(sub, other.sub),
      line: mix(line, other.line),
      lineSoft: mix(lineSoft, other.lineSoft),
      primary: mix(primary, other.primary),
      onPrimary: mix(onPrimary, other.onPrimary),
      soft: mix(soft, other.soft),
      selected: mix(selected, other.selected),
      track: mix(track, other.track),
      fill: mix(fill, other.fill),
      body: mix(body, other.body),
      bodyDark: mix(bodyDark, other.bodyDark),
      warn: mix(warn, other.warn),
      warnSoft: mix(warnSoft, other.warnSoft),
      danger: mix(danger, other.danger),
      chart: mix(chart, other.chart),
      avg: mix(avg, other.avg),
      shadow: t < 0.5 ? shadow : other.shadow,
      navShadow: t < 0.5 ? navShadow : other.navShadow,
      catColors: t < 0.5 ? catColors : other.catColors,
    );
  }
}

const lightPalette = Palette(
  isDark: false,
  bg: Color(0xFFF1F4F2),
  card: Color(0xFFFFFFFF),
  ink: Color(0xFF22332E),
  sub: Color(0xFF5F7069),
  line: Color(0xFFD5DDD8),
  lineSoft: Color(0xFFE4EAE6),
  primary: Color(0xFF1F6F5F),
  onPrimary: Color(0xFFFFFFFF),
  soft: Color(0xFFDDEEE8),
  selected: Color(0xFFFFFFFF),
  track: Color(0xFFD9E0DC),
  fill: Color(0xFF7CC4AC),
  body: Color(0xFFDDE5E0),
  bodyDark: Color(0xFF45564F),
  warn: Color(0xFF8F5200),
  warnSoft: Color(0xFFFBEFD9),
  danger: Color(0xFFC8372D),
  chart: Color(0xFF1F6F5F),
  avg: Color(0xFFC07A12),
  shadow: [
    BoxShadow(
      color: Color.fromRGBO(23, 50, 44, 0.07),
      blurRadius: 10,
      offset: Offset(0, 2),
    ),
  ],
  navShadow: [
    BoxShadow(
      color: Color.fromRGBO(23, 50, 44, 0.08),
      blurRadius: 14,
      offset: Offset(0, -2),
    ),
  ],
  catColors: [
    CatColor(Color(0xFF1F6F5F), Color(0xFFFFFFFF)),
    CatColor(Color(0xFF8F5200), Color(0xFFFFFFFF)),
    CatColor(Color(0xFF2F5F9E), Color(0xFFFFFFFF)),
    CatColor(Color(0xFF9B3D5A), Color(0xFFFFFFFF)),
    CatColor(Color(0xFF6B4C9A), Color(0xFFFFFFFF)),
    CatColor(Color(0xFF5C6B1F), Color(0xFFFFFFFF)),
  ],
);

const darkPalette = Palette(
  isDark: true,
  bg: Color(0xFF0F1A17),
  card: Color(0xFF18261F),
  ink: Color(0xFFE6F0EC),
  sub: Color(0xFFA3B6AF),
  line: Color(0xFF2A3A34),
  lineSoft: Color(0xFF2A3A34),
  primary: Color(0xFF7FE0C4),
  onPrimary: Color(0xFF06251D),
  soft: Color(0xFF1F3A32),
  selected: Color(0xFF2F5148),
  track: Color(0xFF2A3A34),
  fill: Color(0xFF4FA88F),
  body: Color(0xFF2F443C),
  bodyDark: Color(0xFF14201C),
  warn: Color(0xFFEDB35C),
  warnSoft: Color(0xFF3A2C12),
  danger: Color(0xFFFF8E85),
  chart: Color(0xFF6CCFB4),
  avg: Color(0xFFEDB35C),
  shadow: [],
  navShadow: [],
  catColors: [
    CatColor(Color(0xFF7FE0C4), Color(0xFF06251D)),
    CatColor(Color(0xFFEDB35C), Color(0xFF2B1B00)),
    CatColor(Color(0xFF8DB8F0), Color(0xFF0B2545)),
    CatColor(Color(0xFFF0A0B8), Color(0xFF3A0F1F)),
    CatColor(Color(0xFFC3A8F0), Color(0xFF22103F)),
    CatColor(Color(0xFFC5D36B), Color(0xFF232A00)),
  ],
);

const fontFamily = 'Atkinson';

ThemeData buildTheme(Brightness brightness) {
  final palette = brightness == Brightness.dark ? darkPalette : lightPalette;
  final scheme = ColorScheme(
    brightness: brightness,
    primary: palette.primary,
    onPrimary: palette.onPrimary,
    secondary: palette.primary,
    onSecondary: palette.onPrimary,
    secondaryContainer: palette.soft,
    onSecondaryContainer: palette.primary,
    error: palette.danger,
    onError: palette.isDark ? const Color(0xFF3A0B08) : Colors.white,
    surface: palette.bg,
    onSurface: palette.ink,
    onSurfaceVariant: palette.sub,
    outline: palette.line,
    outlineVariant: palette.line,
    surfaceContainerLowest: palette.card,
    surfaceContainerLow: palette.card,
    surfaceContainer: palette.card,
    surfaceContainerHigh: palette.card,
    surfaceContainerHighest: palette.soft,
  );
  const buttonShape = RoundedRectangleBorder(
    borderRadius: BorderRadius.all(Radius.circular(14)),
  );
  const buttonSize = Size(0, 48);
  const buttonText = TextStyle(
    fontFamily: fontFamily,
    fontSize: 16,
    fontWeight: FontWeight.w700,
  );
  final fieldBorder = OutlineInputBorder(
    borderRadius: BorderRadius.circular(14),
    borderSide: BorderSide(color: palette.line, width: 2),
  );
  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    fontFamily: fontFamily,
    scaffoldBackgroundColor: palette.bg,
    canvasColor: palette.bg,
    extensions: [palette],
    dividerTheme: DividerThemeData(color: palette.line, space: 1, thickness: 1),
    textTheme: ThemeData(
      brightness: brightness,
      fontFamily: fontFamily,
    ).textTheme.apply(bodyColor: palette.ink, displayColor: palette.ink),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        shape: buttonShape,
        minimumSize: buttonSize,
        textStyle: buttonText,
        disabledBackgroundColor: palette.disabledFill,
        disabledForegroundColor: palette.disabledInk,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        shape: buttonShape,
        minimumSize: buttonSize,
        textStyle: buttonText,
        foregroundColor: palette.ink,
        disabledForegroundColor: palette.disabledInk,
        side: BorderSide(color: palette.line, width: 2),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        shape: buttonShape,
        minimumSize: const Size(0, 44),
        textStyle: buttonText,
        disabledForegroundColor: palette.disabledInk,
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: fieldBorder,
      enabledBorder: fieldBorder,
      focusedBorder: fieldBorder.copyWith(
        borderSide: BorderSide(color: palette.primary, width: 2),
      ),
      errorBorder: fieldBorder.copyWith(
        borderSide: BorderSide(color: palette.danger, width: 2),
      ),
      focusedErrorBorder: fieldBorder.copyWith(
        borderSide: BorderSide(color: palette.danger, width: 2),
      ),
      labelStyle: TextStyle(color: palette.sub),
      hintStyle: TextStyle(color: palette.hintInk),
      filled: true,
      fillColor: palette.card,
    ),
    sliderTheme: SliderThemeData(
      activeTrackColor: palette.primary,
      thumbColor: palette.primary,
      inactiveTrackColor: palette.track,
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected)
            ? palette.onPrimary
            : palette.sub,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (states) => states.contains(WidgetState.selected)
            ? palette.primary
            : palette.track,
      ),
      trackOutlineColor: const WidgetStatePropertyAll(Colors.transparent),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: palette.ink,
      contentTextStyle: TextStyle(
        fontFamily: fontFamily,
        fontSize: 15,
        color: palette.bg,
      ),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(14)),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: palette.card,
      titleTextStyle: TextStyle(
        fontFamily: fontFamily,
        fontSize: 20,
        fontWeight: FontWeight.w700,
        color: palette.ink,
      ),
      surfaceTintColor: Colors.transparent,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(20)),
      ),
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: palette.card,
      surfaceTintColor: Colors.transparent,
      modalBackgroundColor: palette.card,
      showDragHandle: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
    ),
  );
}

extension PaletteOf on BuildContext {
  Palette get palette => Theme.of(this).extension<Palette>()!;
}
