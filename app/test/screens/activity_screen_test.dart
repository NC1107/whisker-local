import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:intl/intl.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/screens/activity_screen.dart';

import '../support/fixtures.dart';
import 'screen_support.dart';

final now = DateTime(2026, 11, 8, 20);

String stamp(int day, int hour, [int minute = 0]) =>
    DateTime(2026, 11, day, hour, minute).toUtc().toIso8601String();

Map<String, Object?> event(
  int id,
  String kind,
  String at,
  Map<String, Object?> detail,
) => {'id': id, 'cursor': 'c$id', 'at': at, 'kind': kind, 'detail': detail};

Map<String, Object?> visit(
  int id, {
  required String at,
  String label = 'cat',
  int? catId = 1,
  double? weight = 9.0,
  int seconds = 48,
}) => {
  'id': id,
  'cursor': 'c$id',
  'at': at,
  'kind': 'visit',
  'detail': {'started': at, 'duration_s': seconds, 'weight_lb': ?weight},
  'label': label,
  'cat_id': catId,
  'label_source': label == 'unknown' ? 'auto' : 'user',
};

List<Map<String, Object?>> sampleEvents() => [
  event(5, 'alert', stamp(8, 0), {
    'key': 'globe_clean_due',
    'raised': true,
    'urgent': false,
    'message': 'Globe clean is overdue',
  }),
  event(4, 'visit', stamp(8, 15, 42), {'weight_lb': 9.6, 'duration_s': 35}),
  event(3, 'clean_cycle', stamp(8, 7, 50), {'count': 5, 'drawer_percent': 55}),
  event(2, 'visit', stamp(8, 7, 36), {'weight_lb': 9.0, 'duration_s': 48}),
  event(1, 'visit', stamp(7, 19, 2), {'weight_lb': 10.2, 'duration_s': 71}),
];

List<Map<String, Object?>> sampleVisits() => [
  visit(1, at: stamp(7, 19, 2), weight: 10.2, seconds: 71),
  visit(2, at: stamp(8, 7, 36)),
  visit(
    4,
    at: stamp(8, 15, 42),
    label: 'unknown',
    catId: null,
    weight: 9.6,
    seconds: 35,
  ),
];

FakeServer activityServer({
  List<Map<String, Object?>>? events,
  List<Map<String, Object?>>? visits,
  List<Map<String, Object?>>? alerts,
}) {
  final server = FakeServer(
    statusJson(
        robot: robotJson(),
        cats: [
          catJson(id: 1, name: 'Miso'),
          catJson(id: 2, name: 'Pip'),
        ],
        alerts: alerts ?? const [],
        visitsNeedingCat: 1,
      )
      ..['latest_visit_needing_cat'] = {
        'id': 4,
        'at': stamp(8, 15, 42),
        'weight_lb': 9.6,
        'duration_s': 35,
        'closest_cat_id': 2,
      },
  );
  server.routes['GET /api/events'] = (_) =>
      jsonResponse(events ?? sampleEvents());
  server.routes['GET /api/visits'] = (_) =>
      jsonResponse(visits ?? sampleVisits());
  return server;
}

Future<AppController> pumpActivity(
  WidgetTester tester,
  http.Client client, {
  Size size = const Size(400, 1600),
  ChooseVisit? onChooseVisit,
}) async {
  final controller = await connectedController(tester, client);
  await pumpScreen(
    tester,
    controller,
    ActivityScreen(
      controller: controller,
      clock: () => now,
      onChooseVisit: onChooseVisit,
    ),
    size: size,
  );
  await settle(tester);
  return controller;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('groups rows by day with what happened and when', (tester) async {
    await pumpActivity(tester, activityServer().client);
    expect(find.text('Today'), findsOneWidget);
    expect(find.text('Yesterday'), findsOneWidget);
    expect(find.text('Pip visited'), findsNothing);
    expect(find.text('Miso visited'), findsNWidgets(2));
    expect(find.text('9.0 lb for 48 s'), findsOneWidget);
    expect(find.text('10.2 lb for 1 min 11 s'), findsOneWidget);
    expect(find.text('Clean cycle finished'), findsOneWidget);
    expect(find.text('Drawer at 55%'), findsOneWidget);
    expect(find.text('Globe clean is overdue'), findsOneWidget);
    expect(find.text('Needs attention'), findsOneWidget);
    expect(find.text('Cat visit'), findsOneWidget);
    expect(find.text('9.6 lb for 35 s'), findsOneWidget);
    expect(
      find.text(DateFormat.jm().format(DateTime(2026, 11, 8, 7, 36))),
      findsOneWidget,
    );
  });

  testWidgets('opening it marks the alerts as seen', (tester) async {
    final server = activityServer(
      alerts: [
        {
          'key': 'globe_clean_due',
          'urgent': false,
          'message': 'Globe clean is overdue',
        },
      ],
    );
    final controller = await connectedController(tester, server.client);
    expect(controller.hasUnreadAlerts, isTrue);
    await pumpScreen(
      tester,
      controller,
      ActivityScreen(controller: controller, clock: () => now),
    );
    await settle(tester);
    expect(controller.hasUnreadAlerts, isFalse);
  });

  testWidgets('the filters narrow the list to one kind', (tester) async {
    await pumpActivity(tester, activityServer().client);
    await tester.tap(find.byKey(const ValueKey('filter-cats')));
    await tester.pump();
    expect(find.text('Clean cycle finished'), findsNothing);
    expect(find.text('Globe clean is overdue'), findsNothing);
    expect(find.text('Miso visited'), findsNWidgets(2));

    await tester.tap(find.byKey(const ValueKey('filter-robot')));
    await tester.pump();
    expect(find.text('Clean cycle finished'), findsOneWidget);
    expect(find.text('Miso visited'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('filter-alerts')));
    await tester.pump();
    expect(find.text('Globe clean is overdue'), findsOneWidget);
    expect(find.text('Clean cycle finished'), findsNothing);
  });

  testWidgets('a filter with nothing in it says so', (tester) async {
    await pumpActivity(
      tester,
      activityServer(events: [sampleEvents()[2]]).client,
    );
    await tester.tap(find.byKey(const ValueKey('filter-alerts')));
    await tester.pump();
    expect(find.text('Nothing yet'), findsOneWidget);
    expect(find.text('No alerts'), findsOneWidget);
  });

  testWidgets('an empty history is a calm empty state', (tester) async {
    await pumpActivity(tester, activityServer(events: [], visits: []).client);
    expect(find.text('Nothing yet'), findsOneWidget);
    expect(find.text('Visits, cycles and alerts appear here'), findsOneWidget);
  });

  testWidgets('a cleared alert reads as cleared', (tester) async {
    await pumpActivity(
      tester,
      activityServer(
        events: [
          event(9, 'alert', stamp(8, 9), {
            'key': 'bonnet',
            'raised': false,
            'urgent': false,
            'message': 'The bonnet is off',
          }),
        ],
      ).client,
    );
    expect(find.text('The bonnet is off'), findsOneWidget);
    expect(find.text('Cleared'), findsOneWidget);
  });

  testWidgets('robot events get plain titles', (tester) async {
    await pumpActivity(
      tester,
      activityServer(
        events: [
          event(9, 'fault', stamp(8, 9), {'code': 'DF1'}),
          event(8, 'drawer_moved', stamp(8, 8), <String, Object?>{}),
          event(7, 'firmware', stamp(8, 7), {'version': '1.2.0'}),
          event(6, 'command', stamp(8, 6), {'label': 'cleanCycle'}),
        ],
        visits: [],
      ).client,
    );
    expect(find.text('Robot reported a fault'), findsOneWidget);
    expect(find.text('Code DF1'), findsOneWidget);
    expect(find.text('Waste drawer moved'), findsOneWidget);
    expect(find.text('Firmware changed'), findsOneWidget);
    expect(find.text('Clean started'), findsOneWidget);
    expect(find.textContaining('cleanCycle'), findsNothing);
  });

  testWidgets('Choose on a phone is a roomy target and the text stays put', (
    tester,
  ) async {
    await pumpActivity(
      tester,
      activityServer(
        events: [sampleEvents()[1]],
        visits: [visit(4, at: stamp(8, 15, 42), label: 'unknown', catId: null)],
      ).client,
      size: const Size(390, 844),
    );
    final choose = tester.getRect(find.byKey(const ValueKey('choose-4')));
    expect(choose.height, greaterThanOrEqualTo(48));
    expect(choose.width, greaterThanOrEqualTo(48));
    final title = tester.getRect(find.text('Cat visit'));
    expect(title.right, lessThan(choose.left));
    expect(title.height, lessThan(24));
  });

  testWidgets('an unknown command never shows its identifier', (tester) async {
    await pumpActivity(
      tester,
      activityServer(
        events: [
          event(6, 'command', stamp(8, 6), {'label': 'setFutureThing'}),
        ],
        visits: [],
      ).client,
    );
    expect(find.text('Robot command sent'), findsOneWidget);
    expect(find.textContaining('setFutureThing'), findsNothing);
  });

  testWidgets('a visit marked not a cat says so', (tester) async {
    await pumpActivity(
      tester,
      activityServer(
        events: [sampleEvents()[1]],
        visits: [
          visit(
            4,
            at: stamp(8, 15, 42),
            label: 'not_cat',
            catId: null,
            weight: 9.6,
            seconds: 35,
          ),
        ],
      ).client,
    );
    expect(find.text('Not a cat'), findsOneWidget);
    expect(find.text('Choose'), findsNothing);
  });

  testWidgets('Choose hands the visit and its closest cat to the opener', (
    tester,
  ) async {
    int? chosen;
    int? closest;
    await pumpActivity(
      tester,
      activityServer().client,
      onChooseVisit: (context, visitId, closestCatId) async {
        chosen = visitId;
        closest = closestCatId;
      },
    );
    await tester.tap(find.byKey(const ValueKey('choose-4')));
    await tester.pump();
    expect(chosen, 4);
    expect(closest, 2);
  });

  testWidgets('without an opener Choose labels the visit from a sheet', (
    tester,
  ) async {
    final server = activityServer();
    Map<String, Object?>? sent;
    server.routes['POST /api/visits/4/label'] = (request) {
      sent = jsonDecode(request.body) as Map<String, Object?>;
      return jsonResponse(sampleVisits().last);
    };
    await pumpActivity(tester, server.client);
    await tester.tap(find.byKey(const ValueKey('choose-4')));
    await tester.pumpAndSettle();
    expect(find.text('Who was this?'), findsOneWidget);
    expect(find.textContaining('9.6 lb for 35 s'), findsWidgets);
    expect(find.text('Leaves it out of the charts'), findsOneWidget);
    expect(find.text('Cancel'), findsOneWidget);
    await tester.tap(find.text('Pip'));
    await settle(tester);
    await tester.pumpAndSettle();
    expect(sent, {'label': 'cat', 'cat_id': 2});
  });

  testWidgets('a load that fails shows the reason with a retry', (
    tester,
  ) async {
    final server = activityServer();
    server.routes['GET /api/events'] = (_) => failure('Database is busy.', 503);
    await pumpActivity(tester, server.client);
    expect(find.text('Could not load activity'), findsOneWidget);
    expect(find.text('Database is busy.'), findsOneWidget);

    server.routes['GET /api/events'] = (_) => jsonResponse(sampleEvents());
    await tester.tap(find.text('Try again'));
    await settle(tester);
    expect(find.text('Could not load activity'), findsNothing);
    expect(find.text('Clean cycle finished'), findsOneWidget);
  });

  testWidgets('an older response never replaces a newer one', (tester) async {
    final slow = Completer<void>();
    final server = activityServer(events: [sampleEvents()[2]], visits: []);
    var calls = 0;
    final client = MockClient((request) async {
      if (request.url.path == '/api/events') {
        calls++;
        if (calls == 1) {
          await slow.future;
          return jsonResponse([
            event(1, 'drawer_moved', stamp(8, 6), <String, Object?>{}),
          ]);
        }
        return jsonResponse(sampleEvents());
      }
      return server.client
          .send(
            http.Request(request.method, request.url)
              ..headers.addAll(request.headers)
              ..body = request.body,
          )
          .then(http.Response.fromStream);
    });
    final controller = await pumpActivity(tester, client);
    expect(calls, 1);

    // A new alert makes the screen load again while the first load is pending.
    server.status = statusJson(
      robot: robotJson(),
      cats: [catJson()],
      alerts: [
        {'key': 'new', 'urgent': false, 'message': 'New alert'},
      ],
    );
    await tester.runAsync(controller.poll);
    await settle(tester);
    expect(calls, 2);
    expect(find.text('Globe clean is overdue'), findsOneWidget);

    slow.complete();
    await settle(tester);
    expect(find.text('Waste drawer moved'), findsNothing);
    expect(find.text('Globe clean is overdue'), findsOneWidget);
  });

  testWidgets('a response after leaving the screen is dropped quietly', (
    tester,
  ) async {
    final slow = Completer<void>();
    final server = activityServer();
    final client = MockClient((request) async {
      if (request.url.path == '/api/events') await slow.future;
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
      ActivityScreen(controller: controller, clock: () => now),
    );
    await tester.pumpWidget(const SizedBox());
    slow.complete();
    await settle(tester);
    expect(tester.takeException(), isNull);
  });

  testWidgets('renders wide and dark', (tester) async {
    final server = activityServer();
    final controller = await connectedController(tester, server.client);
    await pumpScreen(
      tester,
      controller,
      ActivityScreen(controller: controller, clock: () => now),
      size: const Size(1280, 900),
      mode: Brightness.dark,
    );
    await settle(tester);
    expect(find.text('Miso visited'), findsNWidgets(2));
    expect(tester.takeException(), isNull);
  });

  testWidgets('pages back through older events until the history ends', (
    tester,
  ) async {
    final server = activityServer(events: sampleEvents());
    final firstPage = [
      for (var i = 0; i < 300; i++)
        event(1000 + i, 'visit', stamp(8, 7, 36), {'weight_lb': 9.0}),
    ];
    final cursors = <String?>[];
    server.routes['GET /api/events'] = (request) {
      final before = request.url.queryParameters['before'];
      cursors.add(before);
      return jsonResponse(before == null ? firstPage : sampleEvents());
    };
    await pumpActivity(tester, server.client);
    final button = find.byKey(const ValueKey('activity-earlier'));
    await tester.scrollUntilVisible(button, 400);
    await tester.ensureVisible(button);
    await tester.pump();
    await tester.tap(button);
    await tester.pump();
    await settle(tester);
    expect(cursors, [null, 'c1299']);
    expect(
      find.byKey(const ValueKey('activity-earlier')),
      findsNothing,
      reason: 'a short page means the history has ended',
    );
  });
}
