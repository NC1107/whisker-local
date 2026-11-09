import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/screens/settings_screen.dart';

import '../support/fixtures.dart';
import 'screen_support.dart';

Future<AppController> pumpSettings(
  WidgetTester tester,
  FakeServer server, {
  Future<String> Function()? version,
  Size size = const Size(400, 1600),
}) async {
  final controller = await connectedController(tester, server.client);
  await pumpScreen(
    tester,
    controller,
    SettingsScreen(
      controller: controller,
      version: version ?? () async => '2.3.4',
    ),
    size: size,
  );
  await settle(tester);
  return controller;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('holds the app level cards, not the robot ones', (tester) async {
    await pumpSettings(tester, FakeServer(statusJson(robot: robotJson())));
    expect(find.text('Import Whisker history'), findsOneWidget);
    expect(find.text('Connection'), findsOneWidget);
    expect(find.text('http://box'), findsOneWidget);
    expect(find.text('2.3.4'), findsOneWidget);
    expect(find.text('Return to Whisker'), findsOneWidget);
    expect(find.text('Sign out'), findsOneWidget);
    expect(find.text('Night light brightness'), findsNothing);
  });

  testWidgets('shows a dash when the version cannot be read', (tester) async {
    await pumpSettings(
      tester,
      FakeServer(statusJson(robot: robotJson())),
      version: () async => throw Exception('no platform'),
    );
    expect(find.text('-'), findsOneWidget);
  });

  testWidgets('lays out in two columns on a wide window', (tester) async {
    await pumpSettings(
      tester,
      FakeServer(statusJson(robot: robotJson())),
      size: const Size(1280, 900),
    );
    expect(find.text('Settings'), findsWidgets);
    expect(find.text('Connection'), findsOneWidget);
  });

  group('Sign out', () {
    testWidgets('asks first and signs this device out', (tester) async {
      final controller = await pumpSettings(
        tester,
        FakeServer(statusJson(robot: robotJson())),
      );
      await tester.ensureVisible(find.byKey(const ValueKey('forget-key')));
      await tester.tap(find.byKey(const ValueKey('forget-key')));
      await tester.pumpAndSettle();
      expect(find.text('Sign out?'), findsWidgets);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(controller.phase, AppPhase.ready);

      await tester.tap(find.byKey(const ValueKey('forget-key')));
      await tester.pumpAndSettle();
      await tester.tap(
        find.descendant(
          of: find.byType(AlertDialog),
          matching: find.text('Sign out'),
        ),
      );
      await settle(tester);
      expect(controller.phase, AppPhase.needsConnection);
    });
  });
}
