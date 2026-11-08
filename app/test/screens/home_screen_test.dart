import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/intl.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/screens/robot_parts.dart';
import 'package:whisker_local/screens/who_was_this_sheet.dart';
import 'package:whisker_local/shell.dart';
import 'package:whisker_local/widgets/common/common.dart';

import '../support/fixtures.dart';
import 'screen_support.dart';

Map<String, Object?> fullRobot() => {
  ...robotJson(label: 'Ready'),
  'drawer_percent': 100,
  'drawer_full': true,
  'drawer_full_lockout': true,
};

AppTab currentTab(WidgetTester tester) =>
    tester.widget<ShellScope>(find.byType(ShellScope)).current;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('connectionNotice', () {
    final now = DateTime.utc(2026, 11, 8, 12, 20);

    ServerStatus status({
      bool online = true,
      bool broker = true,
      String? seen = '2026-11-08T12:00:00.000+00:00',
    }) => ServerStatus.fromJson(
      statusJson(online: online, robot: robotJson())
        ..['broker_connected'] = broker
        ..['last_seen'] = seen,
    );

    test('says nothing while the robot is live', () {
      expect(connectionNotice(status(), unreachable: false, now: now), isNull);
      expect(connectionNotice(null, unreachable: false, now: now), isNull);
    });

    test('says how long ago an offline robot was heard', () {
      expect(
        connectionNotice(status(online: false), unreachable: false, now: now),
        'Robot offline. Last heard from 20 min ago.',
      );
    });

    test('names the day when it was heard more than a day ago', () {
      expect(
        connectionNotice(
          status(online: false, seen: '2026-11-05T12:00:00.000+00:00'),
          unreachable: false,
          now: now,
        ),
        startsWith('Robot offline. Last heard on Nov'),
      );
    });

    test('a robot that never connected says so', () {
      expect(
        connectionNotice(
          status(online: false, seen: null),
          unreachable: false,
          now: now,
        ),
        'Robot offline. It has not connected yet.',
      );
    });

    test('an unreachable server comes before everything else', () {
      expect(
        connectionNotice(
          status(online: false, broker: false),
          unreachable: true,
          now: now,
        ),
        "Can't reach the server. Showing the last known state.",
      );
    });

    test('a lost broker explains why the robot is quiet', () {
      expect(
        connectionNotice(status(broker: false), unreachable: false, now: now),
        contains('broker'),
      );
    });
  });

  group('RobotCondition', () {
    ServerStatus of(Map<String, Object?> json) => ServerStatus.fromJson(json);

    test('waits for a robot that has not sent a state', () {
      final condition = RobotCondition.of(of(statusJson(online: false)));
      expect(condition.label, 'Waiting for the robot');
      expect(condition.tone, StatusTone.offline);
    });

    test('offline wins over the last state', () {
      final condition = RobotCondition.of(
        of(statusJson(online: false, robot: robotJson())),
      );
      expect(condition.label, 'Offline');
    });

    test('a drawer that stopped the robot is a warning', () {
      final condition = RobotCondition.of(of(statusJson(robot: fullRobot())));
      expect(condition.label, 'Drawer full');
      expect(condition.tone, StatusTone.warning);
    });

    test('a full drawer without a lockout is still Drawer full', () {
      final condition = RobotCondition.of(
        of(
          statusJson(
            robot: {...robotJson(), 'drawer_full': true, 'drawer_percent': 100},
          ),
        ),
      );
      expect(condition.label, 'Drawer full');
      expect(condition.tone, StatusTone.warning);
    });

    test('a removed bonnet is a warning in the robot words', () {
      final condition = RobotCondition.of(
        of(
          statusJson(
            robot: robotJson(status: 'bonnet_removed', label: 'Bonnet removed'),
          ),
        ),
      );
      expect(condition.label, 'Bonnet removed');
      expect(condition.tone, StatusTone.warning);
    });
  });

  group('Home on a phone', () {
    testWidgets('shows the robot, its gauges and each cat', (tester) async {
      await pumpApp(
        tester,
        FakeServer(
          statusJson(
            robot: robotJson(),
            cats: [
              catJson(),
              catJson(
                id: 2,
                name: 'Pip',
                colorIndex: 1,
                summary: {'latest_weight_lb': 9.0},
              ),
            ],
          ),
        ),
        size: const Size(400, 1400),
      );
      expect(find.text('Litter box'), findsWidgets);
      expect(find.text('Ready'), findsOneWidget);
      expect(find.text('35%'), findsOneWidget);
      expect(find.text('Optimal'), findsOneWidget);
      expect(find.text('Miso'), findsOneWidget);
      expect(find.text('10.1 lb'), findsOneWidget);
      expect(find.text('Pip'), findsOneWidget);
      expect(find.text('9.0 lb'), findsOneWidget);
      expect(
        find.textContaining(RegExp(r'Last visit \d+/\d+ at')),
        findsNWidgets(2),
      );
      expect(find.textContaining('Tell '), findsOneWidget);
      expect(find.byKey(const ValueKey('connection-strip')), findsNothing);
      expect(find.byKey(const ValueKey('drawer-full')), findsNothing);
    });

    testWidgets('a robot card tap opens the Robot tab', (tester) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      await tester.tap(find.byKey(const ValueKey('robot-card')));
      await tester.pump();
      expect(currentTab(tester), AppTab.robot);
    });

    testWidgets('a full drawer gets a notice that leads to Reset', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: fullRobot())),
        size: const Size(400, 1400),
      );
      expect(find.text('Drawer full'), findsOneWidget);
      expect(find.text('Full'), findsOneWidget);
      expect(
        find.textContaining('The drawer is full, so cleaning has stopped.'),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('drawer-full')));
      await tester.pump();
      expect(currentTab(tester), AppTab.robot);
    });

    testWidgets('a drawer that is only near full has no notice', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(
          statusJson(
            robot: {...robotJson(), 'drawer_full': true, 'drawer_percent': 92},
          ),
        ),
      );
      expect(find.byKey(const ValueKey('drawer-full')), findsNothing);
      expect(find.text('Full'), findsOneWidget);
      expect(find.text('Drawer full'), findsOneWidget);
      expect(find.text('Ready'), findsNothing);
    });

    testWidgets('an offline robot does not show the lockout as current', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(online: false, robot: fullRobot())),
      );
      expect(find.byKey(const ValueKey('drawer-full')), findsNothing);
      expect(find.text('Offline'), findsOneWidget);
    });

    testWidgets('an offline robot shows the strip and the Offline state', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(online: false, robot: robotJson())),
      );
      expect(find.byKey(const ValueKey('connection-strip')), findsOneWidget);
      expect(find.textContaining('Robot offline. Last heard'), findsOneWidget);
      expect(find.text('Offline'), findsOneWidget);
    });

    testWidgets('a robot that never connected says it is waiting', (
      tester,
    ) async {
      final json = statusJson(online: false)..['last_seen'] = null;
      await pumpApp(tester, FakeServer(json));
      expect(find.text('Waiting for the robot'), findsOneWidget);
      expect(find.text('Unknown'), findsNWidgets(2));
      expect(
        find.text('Robot offline. It has not connected yet.'),
        findsOneWidget,
      );
    });

    testWidgets('without cats it invites you to add one', (tester) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      expect(find.byKey(const ValueKey('no-cats')), findsOneWidget);
      expect(find.text('No cats yet'), findsOneWidget);
      await tester.tap(find.text('Add a cat'));
      await tester.pump();
      expect(currentTab(tester), AppTab.cats);
    });

    testWidgets('a cat without visits says so instead of a weight', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(
          statusJson(
            robot: robotJson(),
            cats: [
              catJson(
                summary: {'latest_weight_lb': null, 'last_visit_at': null},
              ),
            ],
          ),
        ),
      );
      expect(find.text('No weight yet'), findsOneWidget);
      expect(find.text('No visits yet'), findsOneWidget);
    });

    testWidgets('a visit that needs a cat opens a picker and labels it', (
      tester,
    ) async {
      final server = FakeServer(
        statusJson(
            robot: robotJson(),
            cats: [
              catJson(),
              catJson(id: 2, name: 'Pip', colorIndex: 1),
            ],
            visitsNeedingCat: 2,
          )
          ..['latest_visit_needing_cat'] = {
            'id': 9,
            'at': '2026-11-08T15:42:00.000+00:00',
            'weight_lb': 9.6,
            'duration_s': 35,
            'closest_cat_id': 2,
          },
      );
      Map<String, Object?>? sent;
      server.routes['POST /api/visits/9/label'] = (request) {
        sent = jsonDecode(request.body) as Map<String, Object?>;
        server.status = statusJson(
          robot: robotJson(),
          cats: [
            catJson(),
            catJson(id: 2, name: 'Pip', colorIndex: 1),
          ],
        );
        return jsonResponse({
          'id': 9,
          'cursor': 'c',
          'at': '2026-11-08T15:42:00.000+00:00',
          'kind': 'visit',
          'detail': {
            'started': '2026-11-08T15:41:00.000+00:00',
            'duration_s': 35,
            'weight_lb': 9.6,
          },
          'label': 'cat',
          'cat_id': 2,
          'label_source': 'user',
        });
      };
      await pumpApp(tester, server, size: const Size(400, 1400));
      expect(find.textContaining('2 visits need a cat.'), findsOneWidget);
      expect(find.textContaining('9.6 lb at '), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('visits-need-cat')));
      await tester.pumpAndSettle();
      expect(find.text('Who was this?'), findsOneWidget);
      expect(find.text('Closest match'), findsOneWidget);
      expect(find.text('1 more after this one'), findsOneWidget);
      expect(find.text('Leaves it out of the charts'), findsOneWidget);
      expect(find.text('Cancel'), findsOneWidget);

      await tester.tap(
        find.descendant(
          of: find.byType(WhoWasThisSheet),
          matching: find.text('Pip'),
        ),
      );
      await settle(tester);
      await tester.pumpAndSettle();
      expect(sent, {'label': 'cat', 'cat_id': 2});
      expect(find.text('Who was this?'), findsNothing);
      await untilTrue(tester, () => true);
      expect(find.byKey(const ValueKey('visits-need-cat')), findsNothing);
    });

    testWidgets('a failed label stays in the picker with the reason', (
      tester,
    ) async {
      final server = FakeServer(
        statusJson(robot: robotJson(), cats: [catJson()], visitsNeedingCat: 1)
          ..['latest_visit_needing_cat'] = {
            'id': 9,
            'at': '2026-11-08T15:42:00.000+00:00',
            'weight_lb': 9.6,
            'duration_s': 35,
            'closest_cat_id': 1,
          },
      );
      server.routes['POST /api/visits/9/label'] = (_) =>
          failure('That cat is archived.', 409);
      await pumpApp(tester, server, size: const Size(400, 1400));
      await tester.tap(find.byKey(const ValueKey('visits-need-cat')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Not a cat'));
      await settle(tester);
      expect(find.text('That cat is archived.'), findsOneWidget);
      expect(find.text('Who was this?'), findsOneWidget);
    });

    testWidgets('with no cats the visits notice leads to the Cats tab', (
      tester,
    ) async {
      final server = FakeServer(
        statusJson(robot: robotJson(), visitsNeedingCat: 1),
      );
      await pumpApp(tester, server, size: const Size(400, 1400));
      expect(find.text('Add a cat'), findsNWidgets(2));
      await tester.tap(find.byKey(const ValueKey('visits-need-cat')));
      await tester.pump();
      expect(currentTab(tester), AppTab.cats);
    });

    testWidgets('the low litter threshold comes from the alert settings', (
      tester,
    ) async {
      final server = FakeServer(
        statusJson(robot: {...robotJson(), 'litter_percent': 40}),
      );
      server.routes['GET /api/alert-settings'] = (_) => jsonResponse({
        'settings': {
          'drawer_level_percent': 80,
          'litter_low_percent': 50,
          'no_visit_hours': 24,
          'filter_days': 30,
          'deep_clean_days': 90,
          'globe_clean_days': 60,
        },
        'defaults': <String, Object?>{},
      });
      await pumpApp(tester, server);
      await settle(tester);
      expect(find.text('Low'), findsOneWidget);
    });

    testWidgets('renders in the dark theme without trouble', (tester) async {
      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: fullRobot(), cats: [catJson()])),
        size: const Size(400, 1400),
      );
      expect(find.text('Drawer full'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  group('Home on a wide window', () {
    const wide = Size(1280, 900);

    testWidgets(
      'has no greeting and shows each cat with yesterday\'s numbers',
      (tester) async {
        await pumpApp(
          tester,
          FakeServer(statusJson(robot: robotJson(), cats: [catJson()])),
          size: wide,
        );
        expect(find.textContaining('Good '), findsNothing);
        expect(find.text('Robot'), findsWidgets);
        expect(find.text('Yesterday lb'), findsOneWidget);
        expect(find.text('10.2'), findsOneWidget);
        expect(
          find.text(DateFormat.j().format(DateTime(2000, 1, 1, 19))),
          findsOneWidget,
        );
        expect(find.text('Peak time'), findsOneWidget);
      },
    );

    testWidgets('two cats share the row instead of leaving a gap', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(
          statusJson(
            robot: robotJson(),
            cats: [
              catJson(),
              catJson(id: 2, name: 'Pip', colorIndex: 1),
            ],
          ),
        ),
        size: wide,
      );
      final first = tester.getRect(find.byKey(const ValueKey('cat-1')));
      final second = tester.getRect(find.byKey(const ValueKey('cat-2')));
      expect(first.width, greaterThan(450));
      expect(second.left, greaterThan(first.right));
      expect(second.width, closeTo(first.width, 1));
      expect(first.top, second.top);
    });

    testWidgets('Clean now asks first and does nothing on Cancel', (
      tester,
    ) async {
      final server = FakeServer(statusJson(robot: robotJson()));
      await pumpApp(tester, server, size: wide);
      await tester.tap(find.byKey(const ValueKey('clean-now')));
      await tester.pumpAndSettle();
      expect(find.text('Start a clean cycle?'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(server.posts, isEmpty);
    });

    testWidgets('Clean now sends the press once confirmed', (tester) async {
      final server = FakeServer(statusJson(robot: robotJson()));
      await pumpApp(tester, server, size: wide);
      await tester.tap(find.byKey(const ValueKey('clean-now')));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(TextButton, 'Clean now'));
      await settle(tester);
      expect(server.posts, ['/api/actions/clean']);
    });

    testWidgets('a refused press explains itself next to the buttons', (
      tester,
    ) async {
      final server = FakeServer(statusJson(robot: robotJson()));
      server.routes['POST /api/actions/reset'] = (_) =>
          failure('The control lock is on.', 409);
      await pumpApp(tester, server, size: wide);
      await tester.tap(find.byKey(const ValueKey('reset')));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(TextButton, 'Reset'));
      await settle(tester);
      expect(
        find.text('Reset did not go through. The control lock is on.'),
        findsOneWidget,
      );
    });

    testWidgets('the buttons stay off while the robot is offline', (
      tester,
    ) async {
      final server = FakeServer(statusJson(online: false, robot: robotJson()));
      await pumpApp(tester, server, size: wide);
      await tester.tap(find.byKey(const ValueKey('clean-now')));
      await tester.pumpAndSettle();
      expect(find.text('Start a clean cycle?'), findsNothing);
    });

    testWidgets('a cycle in progress turns the button into a label', (
      tester,
    ) async {
      final server = FakeServer(
        statusJson(
          robot: robotJson(
            status: 'clean_cycle',
            label: 'Cleaning',
            cleaning: true,
          ),
        ),
      );
      await pumpApp(tester, server, size: wide);
      expect(find.text('Cleaning'), findsWidgets);
      await tester.tap(find.byKey(const ValueKey('clean-now')));
      await tester.pumpAndSettle();
      expect(find.text('Start a clean cycle?'), findsNothing);
    });
  });
}
