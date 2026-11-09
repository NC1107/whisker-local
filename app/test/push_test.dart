import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/push.dart';

import 'support/fixtures.dart';

class FakePush implements PushPlatform {
  FakePush({this.answer = PushPermission.granted});

  PushPermission answer;
  PushPermission current = PushPermission.denied;
  String? issued = 'token-1';
  int prompts = 0;
  bool forgotten = false;
  final refreshed = StreamController<String>.broadcast();

  @override
  String get name => 'ios';

  @override
  Future<PushPermission> permission() async => current;

  @override
  Future<PushPermission> requestPermission() async {
    prompts++;
    current = answer;
    return answer;
  }

  @override
  Future<String?> token() async => issued;

  @override
  Stream<String> get tokenRefreshed => refreshed.stream;

  @override
  Future<void> forgetToken() async => forgotten = true;
}

FakeServer serverWithPush({bool configured = true}) {
  final server = FakeServer(statusJson(pushConfigured: configured));
  server.routes['PUT /api/push/devices'] = (_) => http.Response('', 204);
  server.routes['DELETE /api/push/devices'] = (_) => http.Response('', 204);
  server.routes['GET /api/push'] = (_) => http.Response(
    jsonEncode({
      'configured': true,
      'devices': 1,
      'kinds': {'drawer_full': true, 'visit': false, 'future_kind': true},
      'defaults': {'drawer_full': true, 'visit': false, 'future_kind': true},
    }),
    200,
  );
  return server;
}

List<http.Request> pushCalls(FakeServer server) => [
  for (final request in server.requests)
    if (request.url.path == '/api/push/devices') request,
];

Future<AppController> started(FakeServer server, FakePush? push) async {
  SharedPreferences.setMockInitialValues({
    'server_url': 'http://box',
    'access_key': 'key',
  });
  final controller = AppController(httpClient: server.client, push: push);
  addTearDown(controller.dispose);
  await controller.start();
  while (controller.status == null) {
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
  return controller;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('without push', () {
    test('android and the web have no push platform', () {
      expect(defaultPushPlatform(), isNull);
    });

    test('the app starts and never offers notifications', () async {
      final server = serverWithPush();
      final controller = await started(server, null);
      expect(controller.pushAvailable, isFalse);
      expect(await controller.setPushEnabled(true), isNotNull);
      expect(pushCalls(server), isEmpty);
    });

    test('a server without a relay is not offered either', () async {
      final push = FakePush();
      final controller = await started(serverWithPush(configured: false), push);
      expect(controller.pushAvailable, isFalse);
      expect(push.prompts, 0);
    });
  });

  group('with push', () {
    test('nothing is asked or sent just by starting', () async {
      final server = serverWithPush();
      final push = FakePush();
      final controller = await started(server, push);
      expect(controller.pushAvailable, isTrue);
      expect(controller.pushEnabled, isFalse);
      expect(push.prompts, 0);
      expect(pushCalls(server), isEmpty);
    });

    test('turning it on prompts, then registers the token', () async {
      final server = serverWithPush();
      final push = FakePush();
      final controller = await started(server, push);

      expect(await controller.setPushEnabled(true), isNull);

      expect(push.prompts, 1);
      final [call] = pushCalls(server);
      expect(call.method, 'PUT');
      expect(jsonDecode(call.body), {'token': 'token-1', 'platform': 'ios'});
      expect(controller.pushEnabled, isTrue);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getBool('push_enabled'), isTrue);
    });

    test('a refused permission sends nothing and says why', () async {
      final server = serverWithPush();
      final controller = await started(
        server,
        FakePush(answer: PushPermission.denied),
      );
      final problem = await controller.setPushEnabled(true);
      expect(problem, contains('blocked'));
      expect(controller.pushEnabled, isFalse);
      expect(pushCalls(server), isEmpty);
    });

    test('turning it off unregisters and forgets the token', () async {
      final server = serverWithPush();
      final push = FakePush();
      final controller = await started(server, push);
      await controller.setPushEnabled(true);

      expect(await controller.setPushEnabled(false), isNull);

      final delete = pushCalls(server).last;
      expect(delete.method, 'DELETE');
      expect(jsonDecode(delete.body), {'token': 'token-1'});
      expect(push.forgotten, isTrue);
      expect(controller.pushEnabled, isFalse);
    });

    test('a later start re-registers quietly, without a prompt', () async {
      final server = serverWithPush();
      final push = FakePush()..current = PushPermission.granted;
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://box',
        'access_key': 'key',
        'push_enabled': true,
      });
      final controller = AppController(httpClient: server.client, push: push);
      addTearDown(controller.dispose);
      await controller.start();
      while (pushCalls(server).isEmpty) {
        await Future<void>.delayed(const Duration(milliseconds: 5));
      }
      expect(push.prompts, 0);
      expect(pushCalls(server).first.method, 'PUT');
    });

    test('a refreshed token replaces the old one on the server', () async {
      final server = serverWithPush();
      final push = FakePush();
      final controller = await started(server, push);
      await controller.setPushEnabled(true);

      push.refreshed.add('token-2');
      while (pushCalls(server).length < 2) {
        await Future<void>.delayed(const Duration(milliseconds: 5));
      }
      expect(jsonDecode(pushCalls(server).last.body)['token'], 'token-2');
    });

    test('forgetting the key unregisters this phone first', () async {
      final server = serverWithPush();
      final controller = await started(server, FakePush());
      await controller.setPushEnabled(true);

      await controller.disconnect();

      expect(pushCalls(server).last.method, 'DELETE');
      expect(controller.pushEnabled, isFalse);
    });
  });

  group('apns channel', () {
    const channel = MethodChannel(ApnsPushPlatform.channelName);
    final calls = <String>[];

    setUp(() {
      calls.clear();
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
            calls.add(call.method);
            return switch (call.method) {
              'permission' => false,
              'requestPermission' => true,
              'token' => 'abcdef',
              _ => null,
            };
          });
    });

    tearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, null);
    });

    test('asks the native side and names the platform ios', () async {
      final push = ApnsPushPlatform(channel: channel);
      expect(push.name, 'ios');
      expect(await push.permission(), PushPermission.denied);
      expect(await push.requestPermission(), PushPermission.granted);
      expect(await push.token(), 'abcdef');
      await push.forgetToken();
      expect(calls, [
        'permission',
        'requestPermission',
        'token',
        'forgetToken',
      ]);
    });

    test('a token pushed from the native side reaches the stream', () async {
      final push = ApnsPushPlatform(channel: channel);
      final next = push.tokenRefreshed.first;
      await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .handlePlatformMessage(
            ApnsPushPlatform.channelName,
            const StandardMethodCodec().encodeMethodCall(
              const MethodCall('onToken', 'fresh'),
            ),
            (_) {},
          );
      expect(await next, 'fresh');
    });
  });

  group('api', () {
    test('push settings parse', () async {
      final server = serverWithPush();
      final settings = await ApiClient(
        'http://box',
        'key',
        server.client,
      ).pushSettings();
      expect(settings.configured, isTrue);
      expect(settings.kinds['drawer_full'], isTrue);
      expect(settings.defaults['visit'], isFalse);
    });

    test('an older server without the field reports no push', () {
      final status = ServerStatus.fromJson(
        {...statusJson()}..remove('push_configured'),
      );
      expect(status.pushConfigured, isFalse);
    });
  });

  group('screens', () {
    testWidgets('the robot tab has no notifications card without push', (
      tester,
    ) async {
      final server = FakeServer(statusJson(robot: robotJson()));
      await pumpApp(tester, server);
      await tester.tap(find.byKey(const ValueKey('tab-robot')));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Forget access key'), 300);
      expect(find.byKey(const ValueKey('open-notifications')), findsNothing);
    });

    testWidgets('the card opens a screen that turns it on and lists alerts', (
      tester,
    ) async {
      final server = serverWithPush();
      server.status = statusJson(robot: robotJson(), pushConfigured: true);
      final push = FakePush();
      final controller = await pumpApp(tester, server, push: push);
      await tester.tap(find.byKey(const ValueKey('tab-robot')));
      await tester.pumpAndSettle();

      final card = find.byKey(const ValueKey('open-notifications'));
      await tester.scrollUntilVisible(card, 300);
      await tester.tap(card);
      await tester.pumpAndSettle();
      expect(find.text('Alerts on this phone'), findsOneWidget);
      expect(find.byKey(const ValueKey('push-kind-drawer_full')), findsNothing);

      await tester.tap(find.byKey(const ValueKey('push-device')));
      await untilTrue(tester, () => controller.pushEnabled);
      await untilTrue(
        tester,
        () => find
            .byKey(const ValueKey('push-kind-drawer_full'))
            .evaluate()
            .isNotEmpty,
      );
      await tester.pumpAndSettle();

      expect(find.text('Waste drawer full'), findsOneWidget);
      expect(find.text('Future kind'), findsOneWidget);
      expect(
        tester
            .widget<Switch>(find.byKey(const ValueKey('push-kind-visit')))
            .value,
        isFalse,
      );
    });
  });
}
