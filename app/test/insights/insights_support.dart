import 'dart:convert';

import 'package:http/http.dart' as http;

/// A reply of `GET /api/cats/{id}/insights` for the week of 2026-11-01.
Map<String, Object?> catInsightsJson({
  int catId = 1,
  String period = 'week',
  int offset = 0,
  String rangeLabel = 'November 1 to November 7',
  bool hasEarlier = true,
  List<double?>? weight,
  List<double?>? visits,
  List<double?>? minutes,
  List<Map<String, Object?>>? timeOfVisit,
  List<String>? summary,
  int days = 7,
}) {
  final points = [
    for (var i = 0; i < days; i++)
      DateTime(2026, 11, 1 + i).toIso8601String().substring(0, 10),
  ];
  List<double?> pad(List<double?>? given, double? fallback) =>
      given ?? List<double?>.filled(days, fallback);
  Map<String, Object?> metric(List<double?> values) {
    final present = values.whereType<double>();
    return {
      'series': values,
      'average': present.isEmpty
          ? null
          : present.reduce((a, b) => a + b) / present.length,
    };
  }

  return {
    'cat_id': catId,
    'period': period,
    'offset': offset,
    'timezone': 'UTC',
    'start': points.first,
    'end': points.last,
    'range_label': rangeLabel,
    'has_earlier': hasEarlier,
    'points': points,
    'weight': metric(pad(weight, 10.2)),
    'visits': metric(pad(visits, 2)),
    'minutes': metric(pad(minutes, 3)),
    'time_of_visit':
        timeOfVisit ??
        [
          {'day': 2, 'hour': 5.8, 'duration_s': 300},
          {'day': 3, 'hour': 19.2, 'duration_s': 60},
        ],
    'overview': {
      'yesterday_weight_lb': 10.2,
      'yesterday_visits': 2,
      'yesterday_minutes': 3.0,
      'week_visits': 11,
      'common_visit_hour': 19,
    },
    'summary':
        summary ??
        ['2 visits yesterday, about average.', 'Weight is in range.'],
  };
}

http.Response jsonResponse(Object? body, [int status = 200]) =>
    http.Response(jsonEncode(body), status);
