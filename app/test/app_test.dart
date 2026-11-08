import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/format.dart';
import 'package:whisker_local/main.dart';
import 'package:whisker_local/widgets/charts.dart';

Map<String, Object?> robotJson({
  String status = 'ready',
  String label = 'Ready',
  bool cleaning = false,
  bool locked = false,
}) => {
  'status': status,
  'status_label': label,
  'is_cleaning': cleaning,
  'cat_present': false,
  'drawer_percent': 35,
  'drawer_is_estimate': false,
  'drawer_full': false,
  'litter_percent': 62,
  'clean_cycles': 1284,
  'wifi_rssi': -54,
  'esp_firmware': '1.1.75',
  'pic_firmware': '10500',
  'last_visit_at': '2026-11-08T12:00:00.000+00:00',
  'last_visit_duration_s': 35,
  'last_weight_lb': 9.42,
  'settings': {
    'night_light_mode': 'auto',
    'night_light_brightness': 50,
    'clean_cycle_wait_minutes': 7,
    'keypad_lockout': locked,
    'sleep_enabled': false,
    'sleep_minute': 1320,
    'wake_minute': 360,
  },
};

Map<String, Object?> statusJson({
  bool online = true,
  Map<String, Object?>? robot,
  List<Map<String, Object?>> alerts = const [],
}) => {
  'serial': 'LR4C000001',
  'online': online,
  'broker_connected': true,
  'last_seen': '2026-11-08T12:00:00.000+00:00',
  'robot': robot,
  'alerts': alerts,
};

class FakeServer {
  FakeServer(this.status);

  Map<String, Object?> status;
  final List<String> posts = [];

  http.Client get client => MockClient((request) async {
    if (request.headers['Authorization'] != 'Bearer key') {
      return http.Response(jsonEncode({'detail': 'Wrong access key'}), 401);
    }
    if (request.url.path == '/api/status') {
      return http.Response(jsonEncode(status), 200);
    }
    if (request.method == 'POST') {
      posts.add(request.url.path);
      return http.Response('{"sent":true}', 202);
    }
    return http.Response('[]', 200);
  });
}

Future<AppController> pumpApp(WidgetTester tester, FakeServer server) async {
  tester.view.physicalSize = const Size(400, 900);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  SharedPreferences.setMockInitialValues({
    'server_url': 'http://10.0.0.100:8420',
    'access_key': 'key',
  });
  final controller = AppController(httpClient: server.client);
  addTearDown(controller.dispose);
  await tester.runAsync(controller.start);
  await tester.pumpWidget(LitterBoxApp(controller: controller));
  await untilTrue(tester, () => controller.status != null);
  return controller;
}

/// HTTP in these tests completes on real time, which the test clock does not advance.
Future<void> untilTrue(WidgetTester tester, bool Function() done) async {
  await tester.runAsync(() async {
    for (var attempt = 0; attempt < 200 && !done(); attempt++) {
      await Future<void>.delayed(const Duration(milliseconds: 5));
    }
  });
  await tester.pump();
}

void main() {
  group('format', () {
    final now = DateTime(2026, 11, 8, 12);

    test('relative time', () {
      expect(
        relativeTime(now.subtract(const Duration(seconds: 20)), now),
        'Just now',
      );
      expect(
        relativeTime(now.subtract(const Duration(minutes: 12)), now),
        '12 min ago',
      );
      expect(
        relativeTime(now.subtract(const Duration(hours: 3)), now),
        '3 h ago',
      );
      expect(relativeTime(DateTime(2026, 11, 3), now), 'Nov 3');
    });

    test('durations', () {
      expect(durationText(35), '35 s');
      expect(durationText(120), '2 min');
      expect(durationText(125), '2 min 5 s');
    });

    test('day headings', () {
      expect(dayHeading(DateTime(2026, 11, 8, 1), now), 'Today');
      expect(dayHeading(DateTime(2026, 11, 7, 23), now), 'Yesterday');
      expect(dayHeading(DateTime(2026, 11, 3), now), 'Tue, Nov 3');
    });
  });

  group('charts', () {
    test('ticks are round and cover the range', () {
      final ticks = niceTicks(8.2, 11.7);
      expect(ticks.first, lessThanOrEqualTo(8.2));
      expect(ticks.last, greaterThanOrEqualTo(11.7));
      expect(ticks, everyElement(predicate<double>((tick) => tick % 1 == 0)));
    });

    test('a flat range still produces a usable axis', () {
      expect(niceTicks(0, 0).length, greaterThanOrEqualTo(2));
    });

    test('visits land on their own calendar day, with gaps kept', () {
      final now = DateTime(2026, 11, 8, 12);
      HistoryEvent visit(DateTime at) => HistoryEvent(
        cursor: at.toIso8601String(),
        at: at,
        kind: 'visit',
        detail: const {},
      );
      final counts = visitsPerDay(
        [
          visit(DateTime(2026, 11, 8, 1)),
          visit(DateTime(2026, 11, 8, 11)),
          visit(DateTime(2026, 11, 6, 23)),
        ],
        now,
        4,
      );
      expect(counts, [0, 1, 0, 2]);
    });
  });

  test('server addresses are normalized', () {
    expect(
      AppController.normalizeUrl(' 10.0.0.100:8420/ '),
      'http://10.0.0.100:8420',
    );
    expect(AppController.normalizeUrl('http://box.lan'), 'http://box.lan');
  });

  group('home', () {
    testWidgets('shows what the robot reports', (tester) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      expect(find.text('Ready'), findsOneWidget);
      expect(find.text('Litter 62%'), findsOneWidget);
      expect(find.text('35%'), findsOneWidget);
      expect(find.text('9.4 lb'), findsOneWidget);
    });

    testWidgets('clean asks first and only then sends', (tester) async {
      final server = FakeServer(statusJson(robot: robotJson()));
      await pumpApp(tester, server);

      await tester.tap(find.widgetWithText(FilledButton, 'Clean now'));
      await tester.pumpAndSettle();
      expect(server.posts, isEmpty);

      await tester.tap(find.widgetWithText(TextButton, 'Clean now'));
      await tester.pump();
      await untilTrue(tester, () => server.posts.isNotEmpty);
      expect(server.posts, ['/api/actions/clean']);
      await untilTrue(
        tester,
        () => find.byType(SnackBar).evaluate().isNotEmpty,
      );
      await tester.pumpAndSettle(const Duration(seconds: 5));
    });

    testWidgets('declining the dialog sends nothing', (tester) async {
      final server = FakeServer(statusJson(robot: robotJson()));
      await pumpApp(tester, server);

      await tester.tap(find.widgetWithText(OutlinedButton, 'Reset'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Not now'));
      await tester.pumpAndSettle();
      expect(server.posts, isEmpty);
    });

    testWidgets('actions are disabled while the robot is offline', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(online: false, robot: robotJson())),
      );
      final clean = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Clean now'),
      );
      expect(clean.onPressed, isNull);
    });

    testWidgets('control lock disables actions and says why', (tester) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson(locked: true))),
      );
      final reset = tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Reset'),
      );
      expect(reset.onPressed, isNull);
      expect(find.textContaining('Control lock is on'), findsOneWidget);
    });

    testWidgets('alerts are shown', (tester) async {
      await pumpApp(
        tester,
        FakeServer(
          statusJson(
            robot: robotJson(),
            alerts: [
              {
                'key': 'drawer_full',
                'urgent': false,
                'message': 'Waste drawer full.',
              },
            ],
          ),
        ),
      );
      expect(find.text('Waste drawer full.'), findsOneWidget);
    });

    testWidgets('explains a robot that has never connected', (tester) async {
      await pumpApp(tester, FakeServer(statusJson(online: false)));
      expect(find.text('Waiting for the robot'), findsOneWidget);
    });

    testWidgets('a key that stops working returns to the connect screen', (
      tester,
    ) async {
      final server = FakeServer(statusJson(robot: robotJson()));
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://10.0.0.100:8420',
        'access_key': 'old-key',
      });
      final controller = AppController(httpClient: server.client);
      addTearDown(controller.dispose);
      await tester.runAsync(controller.start);
      await tester.pumpWidget(LitterBoxApp(controller: controller));
      await untilTrue(
        tester,
        () => controller.phase == AppPhase.needsConnection,
      );
      expect(find.text('Connect'), findsOneWidget);
      expect(find.textContaining('no longer works'), findsOneWidget);
    });
  });
}
