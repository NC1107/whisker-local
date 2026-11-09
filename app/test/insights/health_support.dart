import 'package:http/http.dart' as http;

import '../support/fixtures.dart';
import 'insights_support.dart';

String _day(DateTime d) => d.toIso8601String().substring(0, 10);

Map<String, Object?> weightChartJson(
  http.Request request, {
  List<Map<String, Object?>>? readings,
  Map<String, Object?>? latest,
  Map<String, Object?>? status,
  List<Map<String, Object?>> bands = const [],
}) {
  final start = request.url.queryParameters['start']!;
  final next = _day(DateTime.parse(start).add(const Duration(days: 1)));
  return {
    'cat_id': 1,
    'timezone': 'UTC',
    'start': start,
    'end': request.url.queryParameters['end'],
    'anchor': {
      'weight_lb': 10.0,
      'spread_lb': 0.3,
      'set_at': '2026-10-01T00:00:00.000+00:00',
      'source': 'auto',
    },
    'readings':
        readings ??
        [
          {
            'date': start,
            'hour': 8.5,
            'lb': 10.1,
            'hollow': false,
            'why': null,
          },
          {
            'date': start,
            'hour': 19.0,
            'lb': 9.9,
            'hollow': false,
            'why': null,
          },
          {
            'date': next,
            'hour': 7.0,
            'lb': 12.4,
            'hollow': true,
            'why': 'far_from_usual',
          },
        ],
    'line': [
      {'date': start, 'median': 10.0, 'low': 9.7, 'high': 10.3},
      {'date': next, 'median': 10.05, 'low': 9.75, 'high': 10.35},
    ],
    'bands': bands,
    'latest':
        latest ??
        {
          'date': start,
          'stage': 'change',
          'median_lb': 10.0,
          'readings': 12,
          'change_pct': -6.0,
          'range_pct': [-9.0, -3.0],
          'noise_pct': 3.1,
        },
    'status': status ?? {'line': true, 'text': null},
    'slope': null,
  };
}

Map<String, Object?> rhythmJson(
  http.Request request, {
  bool heatmap = false,
  int visits = 12,
}) {
  final start = DateTime.parse(request.url.queryParameters['start']!);
  return {
    'cat_id': null,
    'start': request.url.queryParameters['start'],
    'end': request.url.queryParameters['end'],
    'actogram': [
      for (var i = 0; i < 5; i++)
        {
          'date': _day(start.add(Duration(days: i))),
          'state': i == 3 ? 'none' : 'full',
          'unreliable': false,
          'visits': i == 3
              ? <Object?>[]
              : [
                  {'hour': 7.5, 'minutes': 2.0, 'cat_id': 1, 'sure': true},
                  {
                    'hour': 19.0,
                    'minutes': null,
                    'cat_id': null,
                    'sure': false,
                  },
                ],
        },
    ],
    'profile': {
      'recent_days': 28,
      'recent_visits': visits,
      'has_usual': true,
      'blocks': [
        for (var b = 0; b < 8; b++)
          {
            'label':
                '${(b * 3).toString().padLeft(2, '0')}:00 to ${(b * 3 + 3).toString().padLeft(2, '0')}:00',
            'recent': b == 2 ? 6 : 1,
            'share': b == 2 ? 0.4 : 0.09,
            'usual_share': 0.125,
          },
      ],
    },
    'heatmap': heatmap
        ? {
            'ready': true,
            'need_days': 0,
            'rows': [
              for (var d = 0; d < 7; d++) [0.2, 1.1, 0.7, 2.0],
            ],
          }
        : {'ready': false, 'need_days': 30},
  };
}

Map<String, Object?> worthJson({
  List<Map<String, Object?>> notes = const [],
  List<Map<String, Object?>>? waiting,
}) => {
  'timezone': 'UTC',
  'through': '2026-11-07',
  'notes': notes,
  'waiting':
      waiting ??
      [
        {
          'kind': 'cat_rate',
          'cat_id': 1,
          'ready': false,
          'text': "Checking Miso's visits needs about 3 more weeks of data.",
        },
        {
          'kind': 'silence',
          'cat_id': null,
          'ready': true,
          'text': 'Checking for a long quiet spell is ready.',
        },
      ],
};

Map<String, Object?> qualityJson(http.Request request) {
  final start = DateTime.parse(request.url.queryParameters['start']!);
  final end = DateTime.parse(request.url.queryParameters['end']!);
  final count = end.difference(start).inDays + 1;
  return {
    'timezone': 'UTC',
    'start': request.url.queryParameters['start'],
    'end': request.url.queryParameters['end'],
    'earliest': _day(start.subtract(const Duration(days: 400))),
    'days': [
      for (var i = 0; i < count; i++)
        {
          'date': _day(start.add(Duration(days: i))),
          'state': i % 9 == 4 ? 'none' : (i % 9 == 5 ? 'partial' : 'full'),
          'source': 'online_record',
          'heard_share': 1.0,
          'events': 3,
          'unreliable': i % 9 == 6 ? {'id': 7, 'note': 'moved the box'} : null,
          'scale_disturbed': i % 9 == 7,
          'visits': i % 9 == 4 ? 0 : 4,
          'named': i % 9 == 4 ? 0 : 3,
          'unnamed': i % 9 == 4 ? 0 : 1,
        },
    ],
    'gaps': [
      {
        'start': _day(start.add(const Duration(days: 4))),
        'end': _day(start.add(const Duration(days: 4))),
        'kind': 'no_data',
      },
    ],
    'summary': {'visits': 100, 'named': 75, 'unnamed': 25},
    'unreliable_ranges': [
      {
        'id': 7,
        'start_date': _day(start.add(const Duration(days: 6))),
        'end_date': _day(start.add(const Duration(days: 6))),
        'note': 'moved the box',
        'created_at': '2026-11-08T00:00:00.000+00:00',
      },
    ],
  };
}

/// Answers the requests the health cards make, so a screen test can ignore them.
void addHealthRoutes(
  FakeServer server, {
  Map<String, Object?>? worth,
  bool heatmap = false,
}) {
  server.routes['GET /api/worth-a-look'] = (_) =>
      jsonResponse(worth ?? worthJson());
  server.routes['GET /api/cats/1/weight-chart'] = (request) =>
      jsonResponse(weightChartJson(request));
  server.routes['GET /api/rhythm'] = (request) =>
      jsonResponse(rhythmJson(request, heatmap: heatmap));
  server.routes['GET /api/data-quality'] = (request) =>
      jsonResponse(qualityJson(request));
}
