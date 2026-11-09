import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/main.dart';
import 'package:whisker_local/screens/connect_screen.dart';
import 'package:whisker_local/screens/settings_screen.dart';
import 'package:whisker_local/shell.dart';
import 'package:whisker_local/theme.dart';

import 'support/fixtures.dart';

Map<String, Object?> alert(String key) => {
  'key': key,
  'urgent': false,
  'message': 'Something needs a look.',
};

Future<void> firstStatus(AppController controller) async {
  while (controller.status == null) {
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  group('navigation', () {
    testWidgets('a phone gets a bottom bar and a top bar', (tester) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      for (final tab in AppTab.values) {
        expect(
          find.descendant(
            of: find.byKey(ValueKey('tab-${tab.name}')),
            matching: find.text(tab.label),
          ),
          findsOneWidget,
        );
      }
      expect(find.byKey(const ValueKey('rail-alerts')), findsNothing);
      expect(find.bySemanticsLabel('Alerts'), findsOneWidget);
      expect(find.bySemanticsLabel('Settings'), findsOneWidget);
    });

    testWidgets('the title follows the tab and only Home has the gear', (
      tester,
    ) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      expect(find.text('Litter box'), findsWidgets);

      await tester.tap(find.byKey(const ValueKey('tab-insights')));
      await tester.pump();
      expect(find.text('Insights'), findsWidgets);
      expect(find.bySemanticsLabel('Settings'), findsNothing);

      await tester.tap(find.byKey(const ValueKey('tab-cats')));
      await tester.pump();
      expect(find.text('Cats'), findsWidgets);
    });

    testWidgets('the gear on Home opens Settings and Back returns', (
      tester,
    ) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      await tester.tap(find.bySemanticsLabel('Settings'));
      await tester.pumpAndSettle();
      expect(find.byType(SettingsScreen), findsOneWidget);
      expect(find.text('Connection'), findsOneWidget);

      await tester.tap(find.bySemanticsLabel('Back'));
      await tester.pumpAndSettle();
      expect(find.byType(SettingsScreen), findsNothing);
      final scope = tester.widget<ShellScope>(find.byType(ShellScope));
      expect(scope.current, AppTab.home);
    });

    testWidgets('the rail has a Settings item beside Alerts', (tester) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson())),
        size: const Size(1280, 900),
      );
      await tester.tap(find.byKey(const ValueKey('rail-settings')));
      await tester.pumpAndSettle();
      expect(find.byType(SettingsScreen), findsOneWidget);
    });

    testWidgets('799 px is still a phone and 800 px gets the rail', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson())),
        size: const Size(799, 900),
      );
      expect(find.byKey(const ValueKey('rail-alerts')), findsNothing);
      expect(find.byKey(const ValueKey('rail-settings')), findsNothing);

      tester.view.physicalSize = const Size(800, 900);
      await tester.pump();
      expect(find.byKey(const ValueKey('rail-alerts')), findsOneWidget);
      expect(find.byKey(const ValueKey('rail-settings')), findsOneWidget);
    });

    testWidgets('the rail switches tabs', (tester) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson())),
        size: const Size(1280, 900),
      );
      await tester.tap(find.byKey(const ValueKey('tab-robot')));
      await tester.pump();
      expect(
        tester.widget<ShellScope>(find.byType(ShellScope)).current,
        AppTab.robot,
      );
    });
  });

  group('sample data banner', () {
    testWidgets('says so plainly when the server runs on sample data', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson(), demo: true)),
      );
      expect(find.text('Sample data until the robot connects'), findsOneWidget);
    });

    testWidgets('is absent for a real robot', (tester) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      expect(find.textContaining('Sample data'), findsNothing);
    });

    testWidgets('shows on a wide window too', (tester) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson(), demo: true)),
        size: const Size(1280, 900),
      );
      expect(find.textContaining('Sample data'), findsOneWidget);
    });
  });

  group('unread alerts', () {
    testWidgets('the bell shows a dot until the alerts are opened', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson(), alerts: [alert('bonnet')])),
      );
      expect(find.byKey(const ValueKey('unread-dot')), findsOneWidget);
      expect(find.bySemanticsLabel('Alerts, 1 new'), findsOneWidget);

      await tester.tap(find.bySemanticsLabel('Alerts, 1 new'));
      await tester.pumpAndSettle();
      await tester.tap(find.bySemanticsLabel('Back'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('unread-dot')), findsNothing);
    });

    testWidgets('the rail shows the dot too', (tester) async {
      await pumpApp(
        tester,
        FakeServer(statusJson(robot: robotJson(), alerts: [alert('bonnet')])),
        size: const Size(1280, 900),
      );
      expect(find.byKey(const ValueKey('unread-dot')), findsOneWidget);
      expect(find.text('Alerts (1 new)'), findsOneWidget);
    });

    testWidgets('no alerts, no dot', (tester) async {
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      expect(find.byKey(const ValueKey('unread-dot')), findsNothing);
    });

    test('an alert that clears and comes back is unread again', () async {
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://box',
        'access_key': 'key',
      });
      final server = FakeServer(statusJson(alerts: [alert('bonnet')]));
      final controller = AppController(httpClient: server.client);
      addTearDown(controller.dispose);
      await controller.start();
      await firstStatus(controller);
      expect(controller.hasUnreadAlerts, isTrue);

      await controller.markAlertsSeen();
      expect(controller.hasUnreadAlerts, isFalse);

      server.status = statusJson();
      await controller.poll();
      server.status = statusJson(alerts: [alert('bonnet')]);
      await controller.poll();
      expect(controller.hasUnreadAlerts, isTrue);
    });

    test('what was seen survives a restart', () async {
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://box',
        'access_key': 'key',
      });
      final server = FakeServer(statusJson(alerts: [alert('bonnet')]));
      final first = AppController(httpClient: server.client);
      addTearDown(first.dispose);
      await first.start();
      await firstStatus(first);
      await first.markAlertsSeen();

      final second = AppController(httpClient: server.client);
      addTearDown(second.dispose);
      await second.start();
      await firstStatus(second);
      expect(second.hasUnreadAlerts, isFalse);
    });
  });

  testWidgets('forgetting the key from Settings lands on the connect screen', (
    tester,
  ) async {
    final controller = await pumpApp(
      tester,
      FakeServer(statusJson(robot: robotJson())),
    );
    await tester.tap(find.bySemanticsLabel('Settings'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('forget-key')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Forget key'));
    await untilTrue(tester, () => controller.phase == AppPhase.needsConnection);
    await tester.pumpAndSettle();
    expect(find.byType(ConnectScreen), findsOneWidget);
    expect(find.byType(SettingsScreen), findsNothing);
  });

  group('theme', () {
    testWidgets('follows the system setting', (tester) async {
      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
      addTearDown(tester.platformDispatcher.clearAllTestValues);
      await pumpApp(tester, FakeServer(statusJson(robot: robotJson())));
      final context = tester.element(find.byType(Shell));
      expect(context.palette.isDark, isTrue);
      expect(Theme.of(context).scaffoldBackgroundColor, darkPalette.bg);

      tester.platformDispatcher.platformBrightnessTestValue = Brightness.light;
      await tester.pump();
      await tester.pump(const Duration(seconds: 1));
      expect(tester.element(find.byType(Shell)).palette.isDark, isFalse);
    });
  });

  testWidgets('a key that stops working returns to the connect screen', (
    tester,
  ) async {
    final server = FakeServer(statusJson(robot: robotJson()));
    SharedPreferences.setMockInitialValues({
      'server_url': 'http://192.168.1.50:8420',
      'access_key': 'old-key',
    });
    final controller = AppController(httpClient: server.client);
    addTearDown(controller.dispose);
    await tester.runAsync(controller.start);
    await tester.pumpWidget(LitterBoxApp(controller: controller));
    await untilTrue(tester, () => controller.phase == AppPhase.needsConnection);
    expect(find.byType(ConnectScreen), findsOneWidget);
    expect(find.textContaining('rejected'), findsOneWidget);
  });
}
