import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:intl/intl.dart';
import 'package:http/testing.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/screens/robot_parts.dart';
import 'package:whisker_local/screens/robot_screen.dart';

import '../support/fixtures.dart';
import 'screen_support.dart';

Map<Object?, Object?> settingsOf(FakeServer server) =>
    (server.status['robot']! as Map)['settings']! as Map;

/// A server that applies settings patches like the real one.
FakeServer robotServer({Map<String, Object?>? robot, bool online = true}) {
  final server = FakeServer(
    jsonDecode(
      jsonEncode(statusJson(robot: robot ?? robotJson(), online: online)),
    ) as Map<String, Object?>,
  );
  server.routes['GET /api/maintenance'] = (_) =>
      jsonResponse(maintenanceJson());
  server.routes['PATCH /api/settings'] = (request) {
    settingsOf(server).addAll(jsonDecode(request.body) as Map<String, Object?>);
    return jsonResponse(server.status);
  };
  return server;
}

Future<AppController> pumpRobot(
  WidgetTester tester,
  FakeServer server, {
  Size size = const Size(400, 2200),
  Brightness mode = Brightness.light,
}) async {
  final controller = await connectedController(tester, server.client);
  await pumpScreen(
    tester,
    controller,
    RobotScreen(controller: controller),
    size: size,
    mode: mode,
  );
  await settle(tester);
  return controller;
}

Map<String, Object?> lastBody(FakeServer server, String method) =>
    jsonDecode(server.requests.lastWhere((r) => r.method == method).body)
        as Map<String, Object?>;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('dueText', () {
    MaintenanceItem item(int? days, {bool tracked = true}) =>
        MaintenanceItem.fromJson({
          'kind': 'filter',
          'label': 'Carbon filter',
          'interval_days': 30,
          'last_done': tracked ? '2026-10-21' : null,
          'due': null,
          'days_until_due': days,
          'is_due': days != null && days <= 0,
        });

    test('counts forward and back in plain words', () {
      expect(dueText(item(12)), 'Due in 12 days');
      expect(dueText(item(1)), 'Due tomorrow');
      expect(dueText(item(0)), 'Due today');
      expect(dueText(item(-1)), 'Overdue by 1 day');
      expect(dueText(item(-3)), 'Overdue by 3 days');
    });

    test('a job nobody logged is not tracked', () {
      expect(dueText(item(null, tracked: false)), 'Not tracked yet');
    });
  });

  group('Robot screen', () {
    testWidgets('shows the controls with the robot\'s values', (tester) async {
      await pumpRobot(tester, robotServer());
      expect(find.text('Clean now'), findsOneWidget);
      expect(find.text('Starts a cycle'), findsOneWidget);
      expect(find.text('Clears a full drawer'), findsOneWidget);
      expect(find.text('Panel lock'), findsOneWidget);
      expect(find.text('Auto'), findsOneWidget);
      expect(find.text('Night light brightness'), findsOneWidget);
      expect(find.text('50%'), findsWidgets);
      expect(find.text('Wait before cleaning'), findsOneWidget);
      expect(find.text('Carbon filter'), findsWidgets);
      expect(find.text('Due in 12 days'), findsWidgets);
      expect(find.text('Overdue by 3 days'), findsOneWidget);
      expect(find.text('LR4C000001'), findsOneWidget);
      expect(find.text('1.1.75'), findsOneWidget);
      expect(find.text('Strong'), findsOneWidget);
      expect(find.text('Forget access key'), findsNothing);
      expect(find.text('Import Whisker history'), findsNothing);
      expect(find.text('Notifications'), findsNothing);
    });

    testWidgets('Sleep mode shows the hours when it is on', (tester) async {
      final robot = robotJson();
      (robot['settings']! as Map)['sleep_enabled'] = true;
      await pumpRobot(tester, robotServer(robot: robot));
      String hour(int h) => DateFormat.j().format(DateTime(2000, 1, 1, h));
      expect(find.text('${hour(22)} to ${hour(6)}'), findsOneWidget);
    });

    testWidgets('a full drawer reads Drawer full, not Ready', (tester) async {
      final robot = robotJson()
        ..['drawer_full'] = true
        ..['drawer_percent'] = 100;
      await pumpRobot(tester, robotServer(robot: robot));
      expect(find.text('Drawer full'), findsOneWidget);
      expect(find.text('Ready'), findsNothing);
    });

    testWidgets('the sleep schedule shows both times', (tester) async {
      await pumpRobot(tester, robotServer());
      expect(find.text('Sleep schedule'), findsOneWidget);
      String hour(int h) => DateFormat.j().format(DateTime(2000, 1, 1, h));
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('sleep-time-sleep_minute')),
          matching: find.text(hour(22)),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('sleep-time-wake_minute')),
          matching: find.text(hour(6)),
        ),
        findsOneWidget,
      );
    });

    testWidgets('the wake time is changed from the time picker', (
      tester,
    ) async {
      final server = robotServer();
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('sleep-time-wake_minute')));
      await tester.pumpAndSettle();
      await tester.tap(find.byIcon(Icons.keyboard_outlined));
      await tester.pumpAndSettle();
      final fields = find.byType(TextField);
      await tester.enterText(fields.at(0), '7');
      await tester.enterText(fields.at(1), '30');
      await tester.tap(find.text('OK'));
      await settle(tester);
      expect(lastBody(server, 'PATCH'), {'wake_minute': 7 * 60 + 30});
      expect(
        find.text(DateFormat.jm().format(DateTime(2000, 1, 1, 7, 30))),
        findsWidgets,
      );
    });

    testWidgets('cancelling the picker changes nothing', (tester) async {
      final server = robotServer();
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('sleep-time-sleep_minute')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await settle(tester);
      expect(server.requests.where((r) => r.method == 'PATCH'), isEmpty);
    });

    testWidgets('the sleep times cannot be changed while offline', (
      tester,
    ) async {
      final server = robotServer(online: false);
      await pumpRobot(tester, server);
      await tester.tap(
        find.byKey(const ValueKey('sleep-time-sleep_minute')),
        warnIfMissed: false,
      );
      await tester.pumpAndSettle();
      expect(find.byType(TimePickerDialog), findsNothing);
    });

    testWidgets('Panel lock sends the new value', (tester) async {
      final server = robotServer();
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('tile-lock')));
      await settle(tester);
      expect(lastBody(server, 'PATCH'), {'keypad_lockout': true});
      expect(find.text('On'), findsOneWidget);
    });

    testWidgets('Night light goes from Auto to Off', (tester) async {
      final server = robotServer();
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('tile-night')));
      await settle(tester);
      expect(lastBody(server, 'PATCH'), {'night_light_mode': 'off'});
    });

    testWidgets('a refused change says why under the controls and reverts', (
      tester,
    ) async {
      final server = robotServer();
      server.routes['PATCH /api/settings'] = (_) =>
          failure('The robot did not confirm the change.', 504);
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('tile-lock')));
      await settle(tester);
      expect(
        find.text('The robot did not confirm the change.'),
        findsOneWidget,
      );
      expect(find.text('Off'), findsWidgets);
    });

    testWidgets('a second tap is ignored while the first is saving', (
      tester,
    ) async {
      final release = Completer<void>();
      var patches = 0;
      final server = robotServer();
      final client = MockClient((request) async {
        if (request.method == 'PATCH') {
          patches++;
          await release.future;
        }
        return server.client
            .send(
              http.Request(request.method, request.url)
                ..headers.addAll(request.headers)
                ..body = request.body,
            )
            .then(http.Response.fromStream);
      });
      final controller = await connectedController(tester, client);
      await pumpScreen(
        tester,
        controller,
        RobotScreen(controller: controller),
        size: const Size(400, 2200),
      );
      await settle(tester);
      await tester.tap(find.byKey(const ValueKey('tile-lock')));
      await tester.pump();
      await tester.tap(find.byKey(const ValueKey('tile-lock')));
      await tester.pump();
      release.complete();
      await settle(tester);
      expect(patches, 1);
    });

    testWidgets('a wait time is chosen from the five pills', (tester) async {
      final server = robotServer();
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('wait-15')));
      await settle(tester);
      expect(lastBody(server, 'PATCH'), {'clean_cycle_wait_minutes': 15});
    });

    testWidgets('dragging the brightness slider saves once on release', (
      tester,
    ) async {
      final server = robotServer();
      await pumpRobot(tester, server);
      await tester.drag(
        find.byKey(const ValueKey('brightness')),
        const Offset(120, 0),
      );
      await settle(tester);
      final patches = server.requests.where((r) => r.method == 'PATCH');
      expect(patches, hasLength(1));
      final saved = lastBody(server, 'PATCH')['night_light_brightness']! as int;
      expect(saved, greaterThan(50));
      expect(saved % 5, 0);
    });

    testWidgets('Clean now asks first and sends only after the answer', (
      tester,
    ) async {
      final server = robotServer();
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('tile-clean')));
      await tester.pumpAndSettle();
      expect(find.text('Start a clean cycle?'), findsOneWidget);
      expect(server.posts, isEmpty);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(server.posts, isEmpty);

      await tester.tap(find.byKey(const ValueKey('tile-clean')));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(TextButton, 'Clean now'));
      await settle(tester);
      expect(server.posts, ['/api/actions/clean']);
    });

    testWidgets('Reset asks first and a refusal shows under the tiles', (
      tester,
    ) async {
      final server = robotServer();
      server.routes['POST /api/actions/reset'] = (_) =>
          failure('The control lock is on.', 409);
      await pumpRobot(tester, server);
      await tester.tap(find.byKey(const ValueKey('tile-reset')));
      await tester.pumpAndSettle();
      expect(find.text('Press Reset?'), findsOneWidget);
      await tester.tap(find.widgetWithText(TextButton, 'Reset'));
      await settle(tester);
      expect(
        find.text('Reset failed: The control lock is on.'),
        findsOneWidget,
      );
    });

    testWidgets('an offline robot leaves the controls off', (tester) async {
      final server = robotServer(online: false);
      await pumpRobot(tester, server);
      expect(find.textContaining('Robot offline'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('tile-lock')));
      await tester.tap(find.byKey(const ValueKey('wait-15')));
      await tester.tap(find.byKey(const ValueKey('tile-clean')));
      await tester.pumpAndSettle();
      expect(server.requests.where((r) => r.method == 'PATCH'), isEmpty);
      expect(find.text('Start a clean cycle?'), findsNothing);
    });

    testWidgets('a robot that never connected shows the controls off', (
      tester,
    ) async {
      final server = robotServer();
      server.status = statusJson(online: false)..['last_seen'] = null;
      await pumpRobot(tester, server);
      expect(find.text('Waiting for the robot'), findsOneWidget);
      expect(find.text('Unknown'), findsWidgets);
      await tester.tap(find.byKey(const ValueKey('tile-lock')));
      await tester.pump();
      expect(server.requests.where((r) => r.method == 'PATCH'), isEmpty);
    });

    testWidgets('renders in the dark theme at wide width', (tester) async {
      await pumpRobot(
        tester,
        robotServer(),
        size: const Size(1280, 1400),
        mode: Brightness.dark,
      );
      expect(find.text('Robot'), findsOneWidget);
      expect(find.text('Upkeep'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  group('Upkeep', () {
    testWidgets('Done logs the job and reloads the due dates', (tester) async {
      final server = robotServer();
      Map<String, Object?>? sent;
      server.routes['POST /api/maintenance'] = (request) {
        sent = jsonDecode(request.body) as Map<String, Object?>;
        server.routes['GET /api/maintenance'] = (_) =>
            jsonResponse(maintenanceJson(filterDays: 30));
        return jsonResponse({
          'id': 1,
          'kind': 'filter',
          'label': 'Carbon filter',
          'done_on': '2026-11-08',
          'recorded_at': '2026-11-08T12:00:00.000+00:00',
        }, 201);
      };
      await pumpRobot(tester, server);
      await tester.ensureVisible(find.byKey(const ValueKey('done-filter')));
      await tester.tap(find.byKey(const ValueKey('done-filter')));
      await settle(tester);
      expect(sent, {'kind': 'filter'});
      expect(find.text('Due in 30 days'), findsWidgets);
    });

    testWidgets('a failed Done explains itself in the card', (tester) async {
      final server = robotServer();
      server.routes['POST /api/maintenance'] = (_) =>
          failure('Disk full.', 500);
      await pumpRobot(tester, server);
      await tester.ensureVisible(find.byKey(const ValueKey('done-filter')));
      await tester.tap(find.byKey(const ValueKey('done-filter')));
      await settle(tester);
      expect(
        find.text('Carbon filter not marked done: Disk full.'),
        findsOneWidget,
      );
    });

    testWidgets('an upkeep list that fails to load can be retried', (
      tester,
    ) async {
      final server = robotServer();
      server.routes['GET /api/maintenance'] = (_) =>
          failure('Database is busy.', 503);
      await pumpRobot(tester, server);
      expect(find.textContaining('Upkeep did not load'), findsOneWidget);
      expect(find.text('Not available'), findsOneWidget);

      server.routes['GET /api/maintenance'] = (_) =>
          jsonResponse(maintenanceJson());
      await tester.tap(find.text('Try again'));
      await settle(tester);
      expect(find.textContaining('Upkeep did not load'), findsNothing);
      expect(find.text('Overdue by 3 days'), findsOneWidget);
    });

    testWidgets('a job nobody logged says it is not tracked', (tester) async {
      final server = robotServer();
      server.routes['GET /api/maintenance'] = (_) =>
          jsonResponse(maintenanceJson(filterDays: null));
      await pumpRobot(tester, server);
      expect(find.text('Not tracked yet'), findsWidgets);
    });

    testWidgets('a slow answer that arrives after leaving does not crash', (
      tester,
    ) async {
      final release = Completer<void>();
      final server = robotServer();
      final client = MockClient((request) async {
        if (request.url.path == '/api/maintenance') await release.future;
        return server.client
            .send(
              http.Request(request.method, request.url)
                ..headers.addAll(request.headers)
                ..body = request.body,
            )
            .then(http.Response.fromStream);
      });
      final controller = await connectedController(tester, client);
      await pumpScreen(
        tester,
        controller,
        RobotScreen(controller: controller),
        size: const Size(400, 2200),
      );
      await tester.pumpWidget(const SizedBox());
      release.complete();
      await settle(tester);
      expect(tester.takeException(), isNull);
    });
  });

  group('Litter level', () {
    testWidgets('saving the full level asks first, then calls the server', (
      tester,
    ) async {
      final server = robotServer();
      server.routes['POST /api/actions/calibrate-litter'] = (request) {
        final robot = server.status['robot']! as Map;
        robot['litter_calibrated'] = true;
        robot['litter_calibration'] = {'full_mm': 40.0, 'empty_mm': null};
        return jsonResponse(server.status);
      };
      await pumpRobot(tester, server);
      expect(find.text('Not saved'), findsNWidgets(2));
      await tester.ensureVisible(find.byKey(const ValueKey('calibrate-full')));
      await tester.tap(find.byKey(const ValueKey('calibrate-full')));
      await tester.pumpAndSettle();
      expect(find.text('Save the full level?'), findsOneWidget);
      await tester.tap(find.text('Save full level'));
      await settle(tester);
      expect(
        jsonDecode(
          server.requests
              .lastWhere((r) => r.url.path.endsWith('calibrate-litter'))
              .body,
        ),
        {'point': 'full'},
      );
      expect(find.text('Saved'), findsOneWidget);
      expect(find.byKey(const ValueKey('calibrate-clear')), findsOneWidget);
    });

    testWidgets('a reading that cannot be used is explained in the card', (
      tester,
    ) async {
      final server = robotServer();
      server.routes['POST /api/actions/calibrate-litter'] = (_) =>
          failure('The robot cannot read the litter right now.', 409);
      await pumpRobot(tester, server);
      await tester.ensureVisible(find.byKey(const ValueKey('calibrate-empty')));
      await tester.tap(find.byKey(const ValueKey('calibrate-empty')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save empty level'));
      await settle(tester);
      expect(
        find.text('The robot cannot read the litter right now.'),
        findsOneWidget,
      );
    });

    testWidgets('clearing the levels asks first', (tester) async {
      final robot = {
        ...robotJson(),
        'litter_calibrated': true,
        'litter_calibration': {'full_mm': 40.0, 'empty_mm': 180.0},
      };
      final server = robotServer(robot: robot);
      server.routes['DELETE /api/actions/calibrate-litter'] = (_) {
        (server.status['robot']!
            as Map<String, Object?>)['litter_calibration'] = {
          'full_mm': null,
          'empty_mm': null,
        };
        return jsonResponse(server.status);
      };
      await pumpRobot(tester, server);
      await tester.ensureVisible(find.byKey(const ValueKey('calibrate-clear')));
      await tester.tap(find.byKey(const ValueKey('calibrate-clear')));
      await tester.pumpAndSettle();
      expect(find.text('Clear the litter levels?'), findsOneWidget);
      await tester.tap(find.text('Clear levels').last);
      await settle(tester);
      expect(server.requests.where((r) => r.method == 'DELETE'), hasLength(1));
      expect(find.byKey(const ValueKey('calibrate-clear')), findsNothing);
    });
  });

  group('Litter hopper', () {
    Map<String, Object?> withHopper(Map<String, Object?> hopper) => {
      ...robotJson(),
      'hopper': hopper,
    };

    testWidgets('is absent for a robot without one', (tester) async {
      await pumpRobot(tester, robotServer());
      expect(find.text('Litter hopper'), findsNothing);
    });

    testWidgets('shows status, rough level and the panel instruction', (
      tester,
    ) async {
      final server = robotServer(
        robot: withHopper({
          'status': 'ok',
          'level_percent': 40,
          'level_source': 'estimate',
          'connected': true,
          'empty': false,
          'last_dispensed': null,
        }),
      );
      await pumpRobot(tester, server);
      expect(find.text('Litter hopper'), findsOneWidget);
      expect(find.text('Working'), findsOneWidget);
      expect(find.text('About 40 % (rough estimate)'), findsOneWidget);
      expect(find.text('Not seen yet'), findsOneWidget);
      expect(find.textContaining('Cycle and Connect'), findsOneWidget);
    });

    test('rows say so when the level and the state are not known', () {
      final rows = hopperRows(
        const Hopper(status: 'unknown', connected: false, empty: false),
        DateTime(2026, 11, 8),
      );
      expect(rows[0].$2, 'Unknown');
      expect(rows[1].$2, 'Unknown until the next dispense');
    });

    test('an empty hopper reads as out of litter', () {
      final rows = hopperRows(
        const Hopper(
          status: 'empty',
          connected: true,
          empty: true,
          levelPercent: 4,
          levelSource: 'measured',
        ),
        DateTime(2026, 11, 8),
      );
      expect(rows[0].$2, 'Out of litter');
      expect(rows[1].$2, '4 %');
    });
  });
}
