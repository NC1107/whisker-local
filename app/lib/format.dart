import 'package:intl/intl.dart';

String relativeTime(DateTime at, DateTime now) {
  final age = now.difference(at);
  if (age.inMinutes < 1) return 'Just now';
  if (age.inMinutes < 60) return '${age.inMinutes} min ago';
  if (age.inHours < 24) return '${age.inHours} h ago';
  return DateFormat.MMMd().format(at);
}

String durationText(int seconds) {
  if (seconds < 60) return '$seconds s';
  final minutes = seconds ~/ 60;
  final rest = seconds % 60;
  return rest == 0 ? '$minutes min' : '$minutes min $rest s';
}

String weightText(double pounds) => '${pounds.toStringAsFixed(1)} lb';

String dayHeading(DateTime day, DateTime now) {
  final today = DateTime(now.year, now.month, now.day);
  final date = DateTime(day.year, day.month, day.day);
  final daysAgo = today.difference(date).inHours / 24;
  if (daysAgo < 0.5) return 'Today';
  if (daysAgo < 1.5) return 'Yesterday';
  return DateFormat.MMMEd().format(day);
}

String signalWord(int rssi) {
  if (rssi >= -60) return 'Strong';
  if (rssi >= -72) return 'Fair';
  return 'Weak';
}
