import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/format.dart';
import 'package:whisker_local/widgets/charts.dart';

import 'support/fixtures.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
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
        id: 1,
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
      'https://10.0.0.100:8420',
    );
    expect(AppController.normalizeUrl('http://box.lan'), 'http://box.lan');
  });

  group('unexpected server answers', () {
    http.Client answering(String body, {int code = 200}) =>
        MockClient((_) async => http.Response(body, code));

    test('non-JSON on success becomes an ApiException', () async {
      final api = ApiClient('http://box', 'key', answering('<html>'));
      await expectLater(api.status(), throwsA(isA<UnexpectedResponse>()));
    });

    test('a field of the wrong type becomes an ApiException', () async {
      final bad = jsonEncode({...statusJson(), 'online': null});
      final api = ApiClient('http://box', 'key', answering(bad));
      await expectLater(api.status(), throwsA(isA<UnexpectedResponse>()));
      await expectLater(
        ApiClient('http://box', 'key', answering('{}')).events(),
        throwsA(isA<UnexpectedResponse>()),
      );
    });

    test('a malformed host is reported as unreachable', () async {
      final api = ApiClient('http://[bad', 'key', answering('{}'));
      await expectLater(api.status(), throwsA(isA<ApiException>()));
    });

    test('connect explains an address that is not our server', () async {
      SharedPreferences.setMockInitialValues({});
      final controller = AppController(httpClient: answering('<html>'));
      addTearDown(controller.dispose);
      expect(
        await controller.connect('10.0.0.1', 'key'),
        'That address is not a litter box server.',
      );
    });

    test('polling recovers after a garbage answer', () async {
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://box',
        'access_key': 'key',
      });
      var garbage = true;
      final controller = AppController(
        httpClient: MockClient(
          (_) async => garbage
              ? http.Response('<html>', 200)
              : http.Response(jsonEncode(statusJson()), 200),
        ),
      );
      addTearDown(controller.dispose);
      await controller.start();
      while (!controller.unreachable) {
        await Future<void>.delayed(const Duration(milliseconds: 5));
      }
      garbage = false;
      await controller.poll();
      expect(controller.unreachable, isFalse);
      expect(controller.status, isNotNull);
    });

    test('changing settings while disconnected fails cleanly', () async {
      final controller = AppController(httpClient: answering('{}'));
      addTearDown(controller.dispose);
      await expectLater(
        controller.updateSettings({'night_light_mode': 'on'}),
        throwsA(isA<ApiException>()),
      );
    });
  });

  test('events carry the server cursor, not the timestamp', () async {
    final api = ApiClient(
      'http://box',
      'key',
      MockClient((request) async {
        expect(request.url.queryParameters['before'], 'c2');
        return http.Response(
          jsonEncode([
            {
              'id': 7,
              'cursor': 'c1',
              'at': '2026-11-08T12:00:00.000+00:00',
              'kind': 'visit',
              'detail': <String, Object?>{},
            },
          ]),
          200,
        );
      }),
    );
    final events = await api.events(before: 'c2');
    expect(events.single.cursor, 'c1');
  });

  group('platform config', () {
    test('Android release builds may use the network', () {
      final manifest = File('android/app/src/main/AndroidManifest.xml')
          .readAsStringSync();
      expect(manifest, contains('android.permission.INTERNET'));
    });

    test('Android keeps the access key out of cloud backups', () {
      final manifest = File('android/app/src/main/AndroidManifest.xml')
          .readAsStringSync();
      expect(manifest, contains('android:allowBackup="false"'));
      expect(manifest, contains('@xml/data_extraction_rules'));
      expect(
        File('android/app/src/main/res/xml/data_extraction_rules.xml')
            .readAsStringSync(),
        contains('<exclude domain="sharedpref"'),
      );
    });

    test('iOS allows plain http to LAN host names', () {
      final plist = File('ios/Runner/Info.plist').readAsStringSync();
      expect(plist, contains('NSAllowsArbitraryLoads'));
    });
  });
}
