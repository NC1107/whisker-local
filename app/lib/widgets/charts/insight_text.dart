import 'package:intl/intl.dart';

import '../../api.dart';

/// 7:00 and PM, for the hour 19. Null when there are no visits to find it from.
({String time, String period})? hourOfDay(int? hour) {
  if (hour == null) return null;
  return (
    time: '${hour % 12 == 0 ? 12 : hour % 12}:00',
    period: hour < 12 ? 'AM' : 'PM',
  );
}

/// 7:12 PM, for the decimal hour 19.2.
String clockText(double hour) {
  final minutes = (hour * 60).round() % (24 * 60);
  final h = minutes ~/ 60;
  return '${h % 12 == 0 ? 12 : h % 12}:${(minutes % 60).toString().padLeft(2, '0')} ${h < 12 ? 'AM' : 'PM'}';
}

/// 3 for 3.0 and 3.8 for 3.8; whole numbers from ten up.
String trimmed(double value) {
  if (value >= 10) return value.round().toString();
  final text = value.toStringAsFixed(1);
  return text.endsWith('.0') ? text.substring(0, text.length - 2) : text;
}

/// Minutes in the box as a tile value: a whole number, and "<1" for a few seconds.
String minutesText(double? minutes) {
  if (minutes == null) return '-';
  if (minutes > 0 && minutes < 0.5) return '<1';
  return minutes.round().toString();
}

String weightValue(double? pounds) =>
    pounds == null ? '-' : pounds.toStringAsFixed(1);

String visitsText(num count) =>
    '${trimmed(count.toDouble())} ${count == 1 ? 'visit' : 'visits'}';

/// The label under a point of a chart: a short weekday for a week, the day
/// of the month for a month and the month name for a year.
String pointTooltipDate(DateTime day, InsightPeriod period) => switch (period) {
  InsightPeriod.week => DateFormat('EEE, MMM d').format(day),
  InsightPeriod.month => DateFormat('MMM d').format(day),
  InsightPeriod.year => DateFormat('MMMM y').format(day),
};

String axisDate(DateTime day, InsightPeriod period) =>
    period == InsightPeriod.year
    ? DateFormat('MMM').format(day)
    : DateFormat('MMM d').format(day);

String weekday(DateTime day) => DateFormat('EEE').format(day);

String possessive(String name) => "$name's";
