import 'package:intl/intl.dart';

String relativeTime(DateTime at, DateTime now) {
  final age = now.difference(at);
  if (age.inMinutes < 1) return 'Just now';
  if (age.inMinutes < 60) return '${age.inMinutes} min ago';
  if (age.inHours < 24) return '${age.inHours} h ago';
  return DateFormat.MMMd().format(at);
}

/// "just now", "12 min ago", "3 hr ago" or "2 days ago", for reading after a
/// verb ("cleaned 12 min ago").
String agoText(DateTime at, DateTime now) {
  final age = now.difference(at);
  if (age.inMinutes < 1) return 'just now';
  if (age.inMinutes < 60) return '${age.inMinutes} min ago';
  if (age.inHours < 24) return '${age.inHours} hr ago';
  final days = age.inDays;
  return days == 1 ? '1 day ago' : '$days days ago';
}

/// A time of day such as "3:42 PM", with a plain space before the AM or PM.
String _clock(DateTime at) =>
    DateFormat.jm().format(at).replaceAll('\u202f', ' ');

/// The one way a moment is written: "today at 8:19 PM", "yesterday at 8:19 PM",
/// "11/8 at 8:19 PM", and with the year when it is not this one.
String whenText(DateTime at, DateTime now) {
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(at.year, at.month, at.day);
  final daysAgo = today.difference(day).inHours / 24;
  final clock = _clock(at);
  if (daysAgo.abs() < 0.5) return 'today at $clock';
  if (daysAgo > 0.5 && daysAgo < 1.5) return 'yesterday at $clock';
  final date = at.year == now.year
      ? DateFormat('M/d').format(at)
      : DateFormat('M/d/yyyy').format(at);
  return '$date at $clock';
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

const _commandTitles = {
  'cleanCycle': 'Clean started',
  'emptyCycle': 'Empty cycle started',
  'panelReset': 'Reset pressed',
  'powerToggle': 'Power switched',
  'wifiToggle': 'Wi-Fi switched',
  'requestState': 'Robot state requested',
  'reportSchedule': 'Robot schedule requested',
  'reportWifiEvent': 'Wi-Fi report requested',
  'reportToF': 'Litter sensor reading requested',
  'reportVersion': 'Firmware version requested',
  'setNightLightMode': 'Night light mode changed',
  'setNightLightBrightness': 'Night light brightness changed',
  'setCleanCycleWait': 'Clean wait time changed',
  'setKeypadLockout': 'Keypad lock changed',
  'setPanelBrightness': 'Panel brightness changed',
  'setPanelSleepMode': 'Panel sleep changed',
  'setPanelSleepTime': 'Panel sleep time changed',
  'setPanelWakeTime': 'Panel wake time changed',
  'setWeekdaySleepEnabled': 'Weekday sleep changed',
  'setWeekdaySleepTime': 'Panel sleep time changed',
  'setWeekdayWakeTime': 'Panel wake time changed',
};

/// The Activity title for a command the app sent. Only names the app knows
/// get their own wording, so an identifier from a newer server never shows.
String commandTitle(Object? label) {
  if (label is! String) return 'Robot command sent';
  final name = label.split('[').first;
  return _commandTitles[name] ??
      (name.startsWith('read ') ? 'Robot setting read' : 'Robot command sent');
}
