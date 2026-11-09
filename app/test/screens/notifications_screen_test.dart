import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/push.dart';
import 'package:whisker_local/screens/notifications_screen.dart';

import '../support/fixtures.dart';
import 'screen_support.dart';

class _Phone implements PushPlatform {
  @override
  String get name => 'ios';

  @override
  Future<PushPermission> permission() async => PushPermission.granted;

  @override
  Future<PushPermission> requestPermission() async => PushPermission.granted;

  @override
  Future<String?> token() async => 'token-1';

  @override
  Stream<String> get tokenRefreshed => const Stream.empty();

  @override
  Future<void> forgetToken() async {}
}

Map<String, Object?> kind(
  String key,
  String group,
  String name, {
  bool on = true,
  bool urgent = false,
  bool cats = false,
  bool available = true,
  Map<String, Object?>? threshold,
}) => {
  'key': key,
  'group': group,
  'name': name,
  'default': on,
  'urgent': urgent,
  'supports_cats': cats,
  'needs': null,
  'available': available,
  'threshold': threshold,
};

Map<String, Object?> pushJson({
  bool visitOn = false,
  Map<String, Object?>? quiet,
  Map<String, Object?>? visitCats,
}) => {
  'configured': true,
  'devices': 1,
  'kinds': {
    'bonnet_removed': true,
    'drawer_level': true,
    'visit': visitOn,
    'hopper_empty': true,
    'daily_summary': false,
  },
  'defaults': {
    'bonnet_removed': true,
    'drawer_level': true,
    'visit': false,
    'hopper_empty': true,
    'daily_summary': false,
  },
  'groups': ['Needs you now', 'Litter and waste', 'Cats', 'Routine'],
  'catalog': [
    kind('bonnet_removed', 'Needs you now', 'Bonnet off', urgent: true),
    kind(
      'drawer_level',
      'Litter and waste',
      'Drawer filling up',
      threshold: {
        'setting': 'drawer_level_percent',
        'unit': '%',
        'min': 50,
        'max': 95,
      },
    ),
    kind('hopper_empty', 'Litter and waste', 'Hopper empty', available: false),
    kind('visit', 'Cats', 'A cat used the box', on: false, cats: true),
    kind('daily_summary', 'Routine', 'Daily summary', on: false),
  ],
  'options': {
    'quiet_hours': {
      'enabled': false,
      'start': '22:00',
      'end': '07:00',
      'mode': 'hold',
      ...?quiet,
    },
    'cats': {
      'visit': {
        'mode': 'any',
        'cat_ids': <int>[],
        'include_unknown': false,
        ...?visitCats,
      },
    },
    'daily_summary_hour': 8,
  },
  'thresholds': {'drawer_level_percent': 80},
  'cats': [
    {'id': 1, 'name': 'Tom'},
    {'id': 2, 'name': 'Kit'},
  ],
};

class _Rig {
  _Rig(this.server);

  final FakeServer server;
  final List<http.Request> saves = [];
}

Future<(_Rig, AppController)> pumpNotifications(
  WidgetTester tester, {
  Map<String, Object?>? push,
}) async {
  final server = FakeServer(statusJson(pushConfigured: true));
  final rig = _Rig(server);
  var current = push ?? pushJson();
  server.routes['GET /api/push'] = (_) =>
      http.Response(jsonEncode(current), 200);
  server.routes['PUT /api/push/devices'] = (_) => http.Response('', 204);
  SharedPreferences.setMockInitialValues({
    'server_url': 'http://box',
    'access_key': 'key',
    'push_enabled': true,
  });
  final controller = AppController(httpClient: server.client, push: _Phone());
  addTearDown(controller.dispose);
  await tester.runAsync(controller.start);
  await untilTrue(tester, () => controller.status != null);
  await untilTrue(tester, () => controller.pushEnabled);
  server.routes['PATCH /api/alert-settings'] = (request) {
    rig.saves.add(request);
    return http.Response(jsonEncode({'settings': {}, 'defaults': {}}), 200);
  };
  server.routes['PATCH /api/push/options'] = (request) {
    rig.saves.add(request);
    final patch = jsonDecode(request.body) as Map<String, dynamic>;
    final next = jsonDecode(jsonEncode(current)) as Map<String, dynamic>;
    final options = next['options'] as Map<String, dynamic>;
    final quiet = patch['quiet_hours'] as Map<String, dynamic>?;
    if (quiet != null) {
      (options['quiet_hours'] as Map<String, dynamic>).addAll(quiet);
    }
    final cats = patch['cats'] as Map<String, dynamic>?;
    if (cats != null) {
      for (final entry in cats.entries) {
        (options['cats'] as Map<String, dynamic>)[entry.key] = {
          'mode': 'any',
          'cat_ids': <int>[],
          'include_unknown': false,
          ...(options['cats'] as Map<String, dynamic>)[entry.key],
          ...entry.value as Map<String, dynamic>,
        };
      }
    }
    current = next;
    return http.Response(jsonEncode(current), 200);
  };
  server.routes['PATCH /api/push/kinds'] = (request) {
    rig.saves.add(request);
    final patch =
        (jsonDecode(request.body) as Map<String, dynamic>)['kinds']
            as Map<String, dynamic>;
    final next = jsonDecode(jsonEncode(current)) as Map<String, dynamic>;
    (next['kinds'] as Map<String, dynamic>).addAll(patch);
    current = next;
    return http.Response(jsonEncode(current), 200);
  };
  await pumpScreen(
    tester,
    controller,
    NotificationsScreen(controller: controller),
  );
  await settle(tester);
  return (rig, controller);
}

void main() {
  testWidgets('the groups and names come from the server', (tester) async {
    await pumpNotifications(tester);
    expect(find.text('Needs you now'), findsOneWidget);
    expect(find.text('Bonnet off'), findsOneWidget);
    expect(find.text('Drawer filling up'), findsOneWidget);
    expect(find.text('A cat used the box'), findsOneWidget);
    expect(find.text('Daily summary'), findsOneWidget);
    expect(find.text('Hopper empty'), findsNothing);
  });

  testWidgets('a threshold slider shows under an enabled kind and saves', (
    tester,
  ) async {
    final (rig, _) = await pumpNotifications(tester);
    final slider = find.byKey(const ValueKey('push-threshold-drawer_level'));
    expect(slider, findsOneWidget);
    expect(find.text('80%'), findsOneWidget);

    await tester.drag(slider, const Offset(-60, 0));
    await tester.pump();
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 40)),
    );

    final save = rig.saves.firstWhere(
      (request) => request.url.path == '/api/alert-settings',
    );
    final value =
        (jsonDecode(save.body) as Map<String, dynamic>)['drawer_level_percent']
            as int;
    expect(value, lessThan(80));
    expect(value, greaterThanOrEqualTo(50));
  });

  testWidgets('a switched off kind has no threshold or cat choice', (
    tester,
  ) async {
    await pumpNotifications(tester);
    expect(
      find.byKey(const ValueKey('push-cats-visit-everyone')),
      findsNothing,
    );
  });

  testWidgets('the cat choice appears with the kind and saves the pick', (
    tester,
  ) async {
    final (rig, _) = await pumpNotifications(
      tester,
      push: pushJson(visitOn: true),
    );
    expect(find.text('Everyone'), findsOneWidget);
    expect(find.text('Unknown cat'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('push-cats-visit-1')));
    await settle(tester);

    final save = rig.saves.firstWhere(
      (request) => request.url.path == '/api/push/options',
    );
    expect(jsonDecode(save.body), {
      'cats': {
        'visit': {
          'mode': 'selected',
          'cat_ids': [1],
        },
      },
    });
    expect(find.text('Unknown cat'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('push-cats-visit-unknown')));
    await settle(tester);
    final last = rig.saves.last;
    expect(jsonDecode(last.body), {
      'cats': {
        'visit': {'include_unknown': true},
      },
    });
  });

  testWidgets('going back to everyone clears the pick', (tester) async {
    final (rig, _) = await pumpNotifications(
      tester,
      push: pushJson(
        visitOn: true,
        visitCats: {
          'mode': 'selected',
          'cat_ids': [1],
        },
      ),
    );
    await tester.tap(find.byKey(const ValueKey('push-cats-visit-everyone')));
    await settle(tester);
    expect(jsonDecode(rig.saves.last.body), {
      'cats': {
        'visit': {'mode': 'any'},
      },
    });
  });

  testWidgets('quiet hours turn on and then offer the times', (tester) async {
    final (rig, _) = await pumpNotifications(tester);
    expect(find.byKey(const ValueKey('push-quiet-start')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('push-quiet')));
    await settle(tester);

    expect(jsonDecode(rig.saves.last.body), {
      'quiet_hours': {'enabled': true},
    });
    expect(find.byKey(const ValueKey('push-quiet-start')), findsOneWidget);
    expect(find.byKey(const ValueKey('push-quiet-end')), findsOneWidget);
    expect(find.text('Merged into one'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('push-quiet-hold')));
    await settle(tester);
    expect(jsonDecode(rig.saves.last.body), {
      'quiet_hours': {'mode': 'drop'},
    });
    expect(find.text('Skipped'), findsOneWidget);
  });

  testWidgets('the daily summary asks for a time once it is on', (
    tester,
  ) async {
    await pumpNotifications(tester);
    expect(find.byKey(const ValueKey('push-summary-time')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('push-kind-daily_summary')));
    await settle(tester);
    expect(find.byKey(const ValueKey('push-summary-time')), findsOneWidget);
  });

  testWidgets('a refused save puts the screen back and says why', (
    tester,
  ) async {
    final (rig, _) = await pumpNotifications(tester);
    rig.server.routes['PATCH /api/push/options'] = (_) =>
        failure('Quiet hours need different start and end times', 422);
    await tester.tap(find.byKey(const ValueKey('push-quiet')));
    await settle(tester);
    expect(
      find.text('Quiet hours need different start and end times'),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('push-quiet-start')), findsNothing);
  });

  testWidgets('an older server without a catalog keeps the built-in list', (
    tester,
  ) async {
    await pumpNotifications(
      tester,
      push: {
        'configured': true,
        'devices': 1,
        'kinds': {'drawer_full': true, 'brand_new': false},
        'defaults': {'drawer_full': true, 'brand_new': false},
      },
    );
    expect(find.text('Waste drawer full'), findsOneWidget);
    expect(find.text('Brand new'), findsOneWidget);
    expect(find.byKey(const ValueKey('push-quiet')), findsNothing);
  });
}
