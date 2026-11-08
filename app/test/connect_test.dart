import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/main.dart';
import 'package:whisker_local/screens/connect_screen.dart';

import 'support/fixtures.dart';

/// A server that only answers on [scheme], and refuses the other one.
http.Client onlyOn(String scheme, {String key = 'key'}) =>
    MockClient((request) async {
      if (request.url.scheme != scheme) {
        throw const SocketException('refused');
      }
      if (request.headers['Authorization'] != 'Bearer $key') {
        return http.Response(jsonEncode({'detail': 'Wrong access key'}), 401);
      }
      return http.Response(jsonEncode(statusJson(robot: robotJson())), 200);
    });

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  group('server address', () {
    test('the web app talks to the origin it was served from', () {
      expect(
        AppController.serverUrlFor(
          web: true,
          base: Uri.parse('https://litter.example:8421/app/?x=1'),
          saved: 'http://old:8420',
        ),
        'https://litter.example:8421',
      );
      expect(
        AppController.serverUrlFor(
          web: true,
          base: Uri.parse('http://10.0.0.5:8420/'),
        ),
        'http://10.0.0.5:8420',
      );
    });

    test('the phone app uses what was saved', () {
      expect(
        AppController.serverUrlFor(
          web: false,
          base: Uri.parse('http://localhost/'),
          saved: 'https://box.lan',
        ),
        'https://box.lan',
      );
      expect(
        AppController.serverUrlFor(web: false, base: Uri.parse('http://x/')),
        '',
      );
    });

    test('a scheme that was typed is kept', () {
      expect(AppController.candidateUrls('https://box.lan/'), [
        'https://box.lan',
      ]);
      expect(AppController.candidateUrls('http://box.lan:8420'), [
        'http://box.lan:8420',
      ]);
    });

    test('a bare private address tries https and then http', () {
      expect(AppController.candidateUrls(' 192.168.1.50:8420/ '), [
        'https://192.168.1.50:8420',
        'http://192.168.1.50:8420',
      ]);
      for (final address in [
        'box.lan',
        'box.local:8420',
        'litterbox',
        '192.168.1.5',
        '172.20.0.9',
        'localhost:8420',
      ]) {
        expect(AppController.candidateUrls(address), [
          'https://$address',
          'http://$address',
        ], reason: address);
      }
      expect(AppController.normalizeUrl('box.lan'), 'https://box.lan');
    });

    test('a bare public address is only ever tried over https', () {
      for (final address in [
        'litter.example.com',
        '8.8.8.8:8420',
        '172.32.0.1',
        '11.0.0.1',
        'litter.example.com/path',
      ]) {
        expect(AppController.candidateUrls(address), [
          'https://$address',
        ], reason: address);
      }
    });
  });

  group('connecting', () {
    setUp(() => SharedPreferences.setMockInitialValues({}));

    test('never sends the key over http to a public address', () async {
      final seen = <String>[];
      final controller = AppController(
        httpClient: MockClient((request) async {
          seen.add(request.url.scheme);
          throw const SocketException('refused');
        }),
      );
      addTearDown(controller.dispose);
      expect(
        await controller.connect('litter.example.com', 'key'),
        "Can't reach a server at litter.example.com.",
      );
      expect(seen, ['https']);
    });

    test('works over plain http', () async {
      final controller = AppController(httpClient: onlyOn('http'));
      addTearDown(controller.dispose);
      expect(await controller.connect('box.lan:8420', 'key'), isNull);
      expect(controller.savedUrl, 'http://box.lan:8420');
      expect(controller.phase, AppPhase.ready);
    });

    test('works over https when the address has no scheme', () async {
      final controller = AppController(httpClient: onlyOn('https'));
      addTearDown(controller.dispose);
      expect(await controller.connect('box.lan', 'key'), isNull);
      expect(controller.savedUrl, 'https://box.lan');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('server_url'), 'https://box.lan');
    });

    test('works over https when it is typed', () async {
      final controller = AppController(httpClient: onlyOn('https'));
      addTearDown(controller.dispose);
      expect(await controller.connect('https://box.lan/', 'key'), isNull);
      expect(controller.savedUrl, 'https://box.lan');
    });

    test(
      'a wrong key is reported as such, not as an unreachable server',
      () async {
        final controller = AppController(httpClient: onlyOn('https', key: 'k'));
        addTearDown(controller.dispose);
        expect(
          await controller.connect('box.lan', 'wrong'),
          'That access key is wrong.',
        );
        expect(controller.phase, AppPhase.starting);
      },
    );

    test('says when nothing answers', () async {
      final controller = AppController(
        httpClient: MockClient((_) async => throw const SocketException('x')),
      );
      addTearDown(controller.dispose);
      expect(
        await controller.connect(' box.lan ', 'key'),
        "Can't reach a server at box.lan.",
      );
    });
  });

  group('connect screen', () {
    Future<AppController> pumpConnect(
      WidgetTester tester,
      http.Client client,
    ) async {
      tester.view.physicalSize = const Size(400, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      SharedPreferences.setMockInitialValues({});
      final controller = AppController(httpClient: client);
      await tester.runAsync(controller.start);
      await tester.pumpWidget(LitterBoxApp(controller: controller));
      await tester.pump();
      return controller;
    }

    testWidgets('asks for the address and the key off the web', (tester) async {
      await pumpConnect(tester, onlyOn('http'));
      expect(find.byType(ConnectScreen), findsOneWidget);
      expect(find.text('Server address'), findsOneWidget);
      expect(find.text('Access key'), findsOneWidget);
      expect(find.text('Connect'), findsOneWidget);
    });

    testWidgets('connects and lands on the home tab', (tester) async {
      final controller = await pumpConnect(tester, onlyOn('http'));
      await tester.enterText(
        find.widgetWithText(TextField, 'Server address'),
        'box.lan',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Access key'),
        'key',
      );
      await tester.tap(find.text('Connect'));
      await untilTrue(tester, () => controller.phase == AppPhase.ready);
      expect(find.byType(ConnectScreen), findsNothing);
      expect(find.text('Litter box'), findsWidgets);
      // Connecting starts polling inside the test clock, so stop it here.
      controller.dispose();
    });

    testWidgets('shows why it failed in the danger color', (tester) async {
      await pumpConnect(tester, onlyOn('http', key: 'other'));
      await tester.enterText(
        find.widgetWithText(TextField, 'Server address'),
        'box.lan',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Access key'),
        'nope',
      );
      await tester.tap(find.text('Connect'));
      await untilTrue(
        tester,
        () => find.text('That access key is wrong.').evaluate().isNotEmpty,
      );
      expect(find.text('That access key is wrong.'), findsOneWidget);
    });

    testWidgets('the error appearing moves nothing', (tester) async {
      await pumpConnect(tester, onlyOn('http', key: 'other'));
      await tester.enterText(
        find.widgetWithText(TextField, 'Server address'),
        'box.lan',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Access key'),
        'nope',
      );
      final field = find.widgetWithText(TextField, 'Access key');
      final fieldBefore = tester.getTopLeft(field);
      final buttonBefore = tester.getTopLeft(find.byType(FilledButton));

      await tester.tap(find.text('Connect'));
      await untilTrue(
        tester,
        () => find.text('That access key is wrong.').evaluate().isNotEmpty,
      );

      expect(find.text('That access key is wrong.'), findsOneWidget);
      expect(tester.getTopLeft(field), fieldBefore);
      expect(tester.getTopLeft(find.byType(FilledButton)), buttonBefore);
    });

    testWidgets('asks for both fields before trying', (tester) async {
      await pumpConnect(tester, onlyOn('http'));
      await tester.tap(find.text('Connect'));
      await tester.pump();
      expect(
        find.text('Enter the server address and access key.'),
        findsOneWidget,
      );
    });
  });
}
