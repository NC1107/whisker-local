import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/intl.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/screens/home_screen.dart';
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
        'Robot offline, seen 20 min ago',
      );
    });

    test('names the day when it was heard more than a day ago', () {
      expect(
        connectionNotice(
          status(online: false, seen: '2026-11-05T12:00:00.000+00:00'),
          unreachable: false,
          now: now,
        ),
        startsWith('Robot offline, seen Nov'),
      );
    });

    test('a robot that never connected says so', () {
      expect(
        connectionNotice(
          status(online: false, seen: null),
          unreachable: false,
          now: now,
        ),
        'Robot never connected',
      );
    });

    test('an unreachable server comes before everything else', () {
      expect(
        connectionNotice(
          status(online: false, broker: false),
          unreachable: true,
          now: now,
        ),
        "Can't reach the server, showing old data",
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

  group('the ready line with the last clean', () {
    final now = DateTime.utc(2026, 11, 8, 12);

    ServerStatus ready({String? cleaned, Map<String, Object?>? robot}) =>
        ServerStatus.fromJson(
          statusJson(robot: robot ?? robotJson())..['last_clean_at'] = cleaned,
        );

    test('says when the last clean finished', () {
      final status = ready(cleaned: '2026-11-08T11:48:00.000+00:00');
      expect(
        RobotCondition.of(status, now: now).label,
        'Ready, cleaned 12\u00a0min\u00a0ago',
      );
    });

    test('uses short wording from just now to days', () {
      String at(Duration age) => RobotCondition.of(
        ready(cleaned: now.subtract(age).toIso8601String()),
        now: now,
      ).label;
      expect(at(const Duration(seconds: 20)), 'Ready, cleaned just\u00a0now');
      expect(at(const Duration(hours: 3)), 'Ready, cleaned 3\u00a0hr\u00a0ago');
      expect(
        at(const Duration(days: 2)),
        'Ready, cleaned 2\u00a0days\u00a0ago',
      );
    });

    test('stays plain without a clean or without the field', () {
      expect(RobotCondition.of(ready(), now: now).label, 'Ready');
      final absent = statusJson(robot: robotJson())..remove('last_clean_at');
      expect(
        RobotCondition.of(ServerStatus.fromJson(absent), now: now).label,
        'Ready',
      );
    });

    test('only a ready robot gets it', () {
      final status = ready(
        cleaned: '2026-11-08T11:48:00.000+00:00',
        robot: robotJson(status: 'cleaning', label: 'Cleaning'),
      );
      expect(RobotCondition.of(status, now: now).label, 'Cleaning');
    });

    testWidgets('moves on with the screen clock and no reload', (tester) async {
      final server = FakeServer(
        statusJson(robot: robotJson())
          ..['last_clean_at'] = '2026-11-08T11:48:00.000+00:00',
      );
      final controller = await connectedController(tester, server.client);
      var clock = now;
      await pumpScreen(
        tester,
        controller,
        ShellScope(
          select: (_) {},
          current: AppTab.home,
          child: HomeScreen(controller: controller, clock: () => clock),
        ),
      );
      expect(find.text('Ready, cleaned 12\u00a0min\u00a0ago'), findsOneWidget);

      clock = now.add(const Duration(minutes: 50));
      await tester.runAsync(controller.poll);
      await tester.pump();
      expect(find.text('Ready, cleaned 62\u00a0min\u00a0ago'), findsNothing);
      expect(find.text('Ready, cleaned 1\u00a0hr\u00a0ago'), findsOneWidget);
    });
  });

  group('the robot card layout', () {
    Future<void> pumpHome(WidgetTester tester, Size size) async {
      final server = FakeServer(
        statusJson(robot: robotJson(), cats: [catJson()]),
      );
      await pumpApp(tester, server, size: size);
    }

    testWidgets('keeps the litter word clear of the edge at 320 wide', (
      tester,
    ) async {
      await pumpHome(tester, const Size(320, 900));
      final card = tester.getRect(find.byKey(const ValueKey('robot-card')));
      final word = tester.getRect(find.text('Optimal'));
      expect(card.right - word.right, greaterThanOrEqualTo(18));
    });

    testWidgets('a tablet puts the drawing beside the name', (tester) async {
      await pumpHome(tester, const Size(820, 1180));
      final glyph = tester.getRect(find.byType(RobotGlyph));
      final name = tester.getRect(
        find.descendant(
          of: find.byKey(const ValueKey('robot-card')),
          matching: find.text('Litter box'),
        ),
      );
      final waste = tester.getRect(find.text('Waste'));
      final clean = tester.getRect(find.text('Clean now'));
      expect(name.left, greaterThan(glyph.right));
      expect(name.center.dy, lessThan(glyph.bottom));
      expect(waste.top, greaterThan(glyph.bottom));
      expect((clean.center.dy - waste.center.dy).abs(), lessThan(60));
    });

    testWidgets('a desktop keeps one row', (tester) async {
      await pumpHome(tester, const Size(1280, 900));
      final glyph = tester.getRect(find.byType(RobotGlyph));
      final waste = tester.getRect(find.text('Waste'));
      expect(waste.top, lessThan(glyph.bottom));
    });
  });

  group('the visits prompt on a phone', () {
    testWidgets('reads in two lines with a centred, roomy Choose', (
      tester,
    ) async {
      final server = FakeServer(
        statusJson(robot: robotJson(), cats: [catJson()], visitsNeedingCat: 2)
          ..['latest_visit_needing_cat'] = {
            'id': 9,
            'at': '2026-11-08T15:42:00.000+00:00',
            'weight_lb': 12.0,
            'duration_s': 35,
            'closest_cat_id': 1,
          },
      );
      await pumpApp(tester, server, size: const Size(390, 844));
      final card = find.byKey(const ValueKey('visits-need-cat'));
      await tester.ensureVisible(card);
      await tester.pumpAndSettle();

      final lead = tester.getRect(find.text('2 unassigned'));
      final detail = tester.getRect(find.textContaining('12.0 lb'));
      final choose = tester.getRect(find.text('Choose'));
      final box = tester.getRect(card);
      expect(detail.top, greaterThanOrEqualTo(lead.bottom));
      expect(detail.height, lessThan(24));
      expect((choose.center.dy - box.center.dy).abs(), lessThan(1));
      final target = tester.getRect(
        find
            .ancestor(of: find.text('Choose'), matching: find.byType(Center))
            .first,
      );
      expect(target.height, greaterThanOrEqualTo(48));
    });

    testWidgets('leaves a gap above the bottom navigation', (tester) async {
      final server = FakeServer(
        statusJson(robot: robotJson(), cats: [catJson()], visitsNeedingCat: 1)
          ..['latest_visit_needing_cat'] = {
            'id': 9,
            'at': '2026-11-08T15:42:00.000+00:00',
            'weight_lb': 12.0,
            'duration_s': 35,
            'closest_cat_id': 1,
          },
      );
      await pumpApp(tester, server, size: const Size(390, 844));
      final card = find.byKey(const ValueKey('visits-need-cat'));
      await tester.ensureVisible(card);
      await tester.drag(find.byType(ListView).first, const Offset(0, -2000));
      await tester.pumpAndSettle();

      final nav = tester.getRect(find.byKey(const ValueKey('tab-home')));
      expect(nav.top - tester.getRect(card).bottom, greaterThanOrEqualTo(24));
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
      expect(find.textContaining('10.1 lb'), findsOneWidget);
      expect(find.text('Pip'), findsOneWidget);
      expect(find.textContaining('9.0 lb'), findsOneWidget);
      expect(
        find.textContaining(RegExp(r'Last visit .+ at')),
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
        find.textContaining('Empty the drawer, then press Reset'),
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
      expect(find.textContaining('Robot offline, seen'), findsOneWidget);
      expect(find.text('Offline'), findsOneWidget);
    });

    testWidgets('a robot that never connected says it is waiting', (
      tester,
    ) async {
      final json = statusJson(online: false)..['last_seen'] = null;
      await pumpApp(tester, FakeServer(json));
      expect(find.text('Waiting for the robot'), findsOneWidget);
      expect(find.text('Unknown'), findsNWidgets(2));
      expect(find.text('Robot never connected'), findsOneWidget);
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
      expect(find.textContaining('2 unassigned'), findsOneWidget);
      expect(find.textContaining('9.6 lb '), findsOneWidget);

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
        find.text('Reset failed: The control lock is on.'),
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

  group('no robot yet', () {
    testWidgets('Home asks to connect the robot and why controls are off', (
      tester,
    ) async {
      final json = statusJson(online: false)..['last_seen'] = null;
      await pumpApp(tester, FakeServer(json), size: const Size(1280, 900));
      expect(find.text('Connect your robot'), findsOneWidget);
      expect(
        find.text('Connect your robot to use the controls'),
        findsOneWidget,
      );
    });

    testWidgets(
      'a robot heard before a server restart is not asked to connect',
      (tester) async {
        final json = statusJson(online: false)
          ..['last_seen'] = '2026-11-08T12:00:00.000+00:00';
        await pumpApp(tester, FakeServer(json), size: const Size(1280, 900));
        expect(find.text('Connect your robot'), findsNothing);
        expect(
          find.text('Connect your robot to use the controls'),
          findsNothing,
        );
      },
    );

    testWidgets('a connected robot gets no prompt', (tester) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson())),
        size: const Size(1280, 900),
      );
      expect(find.text('Connect your robot'), findsNothing);
    });
  });
}
