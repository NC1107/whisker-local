import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/screens/data_quality_screen.dart';
import 'package:whisker_local/widgets/health/coverage_calendar.dart';
import 'package:whisker_local/widgets/health/rhythm_charts.dart';
import 'package:whisker_local/widgets/health/weight_dots_chart.dart';

import '../support/fixtures.dart';
import 'health_support.dart';
import 'insights_support.dart';

FakeServer serverWith({
  Map<String, Object?>? worth,
  bool heatmap = false,
  List<Map<String, Object?>>? cats,
}) {
  final server = FakeServer(
    statusJson(cats: cats ?? [catJson()], robot: robotJson()),
  );
  addHealthRoutes(server, worth: worth, heatmap: heatmap);
  server.routes['GET /api/cats/1/insights'] = (request) => jsonResponse(
    catInsightsJson(period: request.url.queryParameters['period'] ?? 'week'),
  );
  return server;
}

Future<void> openCat(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('tab-insights')));
  await tester.pump();
  await settle(tester);
  final card = find.byKey(const ValueKey('insights-cat-1'));
  await tester.scrollUntilVisible(card, 300);
  await tester.tap(card);
  await tester.pumpAndSettle(const Duration(milliseconds: 50));
  await settle(tester);
}

Future<void> settle(WidgetTester tester) async {
  await untilTrue(tester, () => false);
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  group('worth a look', () {
    testWidgets('says plainly that nothing stood out is not an all clear', (
      tester,
    ) async {
      await pumpApp(tester, serverWith());
      await openCat(tester);
      expect(find.textContaining('not a clean bill of health'), findsOneWidget);
      expect(
        find.textContaining('needs about 3 more weeks of data'),
        findsOneWidget,
        reason: 'a check that cannot run yet says how much is missing',
      );
      expect(
        find.textContaining('long quiet spell'),
        findsNothing,
        reason: 'a check that is ready is not listed as waiting',
      );
    });

    testWidgets('shows what changed and since when, with urgency as words', (
      tester,
    ) async {
      final worth = worthJson(
        notes: [
          {
            'key': 'weight_change:1:2026-11-02',
            'kind': 'weight_change',
            'cat_id': 1,
            'level': 'look',
            'title': "Miso's weight readings are lower than usual",
            'body': 'Since Nov 2, the middle of the last two weeks is lower.',
            'since': '2026-11-02',
          },
          {
            'key': 'burst:1:x',
            'kind': 'burst',
            'cat_id': 1,
            'level': 'urgent',
            'title': 'Miso visited the box 5 times in a short while',
            'body': 'Call a vet now.',
            'since': '2026-11-07',
          },
        ],
      );
      await pumpApp(tester, serverWith(worth: worth));
      await openCat(tester);
      expect(
        find.text("Miso's weight readings are lower than usual"),
        findsOneWidget,
      );
      expect(find.text('Since Nov 2'), findsOneWidget);
      expect(find.text('URGENT'), findsOneWidget);
      expect(find.textContaining('clean bill'), findsNothing);
    });

    testWidgets('shows a failure and retries it', (tester) async {
      final server = serverWith();
      var fail = true;
      server.routes['GET /api/worth-a-look'] = (_) => fail
          ? jsonResponse({'detail': 'Busy'}, 500)
          : jsonResponse(worthJson());
      await pumpApp(tester, server);
      await openCat(tester);
      expect(find.textContaining("This didn't load"), findsOneWidget);
      fail = false;
      await tester.tap(find.text('Try again'));
      await tester.pump();
      await settle(tester);
      expect(find.textContaining('not a clean bill'), findsOneWidget);
    });

    testWidgets('the Insights tab shows the household list and the link', (
      tester,
    ) async {
      await pumpApp(tester, serverWith());
      await tester.tap(find.byKey(const ValueKey('tab-insights')));
      await tester.pump();
      await settle(tester);
      expect(find.byKey(const ValueKey('worth-a-look')), findsOneWidget);
      expect(find.byKey(const ValueKey('data-quality-link')), findsOneWidget);
      expect(find.text('When the box is used'), findsOneWidget);
      expect(find.byType(Actogram), findsOneWidget);
    });
  });

  group('weight chart', () {
    testWidgets('puts the percent and its range beside the dots', (
      tester,
    ) async {
      await pumpApp(tester, serverWith());
      await openCat(tester);
      expect(find.byType(WeightDotsChart), findsOneWidget);
      expect(
        find.textContaining(
          'about 6 percent lower than the usual 10.0 lb (range -9 to -3 percent)',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('Not a diagnosis'), findsWidgets);
    });

    testWidgets('says what is missing before a change can be shown', (
      tester,
    ) async {
      final server = serverWith();
      server.routes['GET /api/cats/1/weight-chart'] = (request) => jsonResponse(
        weightChartJson(
          request,
          latest: {
            'date': '2026-11-07',
            'stage': 'trend',
            'median_lb': 10.0,
            'readings': 9,
            'change_pct': null,
            'range_pct': null,
            'noise_pct': 3.0,
          },
          status: {
            'line': true,
            'text': 'Change from usual needs about 2 more weeks of readings.',
          },
        ),
      );
      await pumpApp(tester, server);
      await openCat(tester);
      expect(find.textContaining('needs about 2 more weeks'), findsOneWidget);
      expect(find.textContaining('percent lower'), findsNothing);
    });

    testWidgets('a tap on a dot reads the value and a ring says why', (
      tester,
    ) async {
      await pumpApp(tester, serverWith());
      await openCat(tester);
      final chart = find.byType(WeightDotsChart);
      expect(chart, findsOneWidget);
      final frame = find.descendant(
        of: chart,
        matching: find.byType(CustomPaint),
      );
      final box = tester.getRect(frame.first);
      await tester.tapAt(box.topLeft + Offset(box.width * 0.5, 100));
      await tester.pump();
      expect(tester.takeException(), isNull);
    });

    testWidgets('the All tab asks for the whole history', (tester) async {
      final server = serverWith();
      await pumpApp(tester, server);
      await openCat(tester);
      await tester.tap(find.byKey(const ValueKey('period-all')));
      await tester.pump();
      await settle(tester);
      expect(
        server.requests.any(
          (r) =>
              r.url.path == '/api/cats/1/insights' &&
              r.url.queryParameters['period'] == 'all',
        ),
        isTrue,
      );
    });
  });

  group('rhythm', () {
    testWidgets('waits for eight weeks before the weekday map', (tester) async {
      await pumpApp(tester, serverWith());
      await openCat(tester);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('heatmap-waiting')),
        400,
      );
      expect(find.textContaining('about 5 more weeks'), findsOneWidget);
      expect(find.byType(HourProfile), findsOneWidget);
      expect(find.byType(WeekdayHeatmap), findsNothing);
    });

    testWidgets('shows the weekday map with numbers once it is ready', (
      tester,
    ) async {
      await pumpApp(tester, serverWith(heatmap: true));
      await openCat(tester);
      await tester.scrollUntilVisible(find.byType(WeekdayHeatmap), 400);
      expect(find.text('1.1'), findsWidgets);
    });

    testWidgets('holds back the time of day with too few visits', (
      tester,
    ) async {
      final server = serverWith();
      server.routes['GET /api/rhythm'] = (request) =>
          jsonResponse(rhythmJson(request, visits: 5));
      await pumpApp(tester, server);
      await openCat(tester);
      await tester.scrollUntilVisible(find.byType(Actogram), 400);
      expect(find.byType(HourProfile), findsNothing);
      expect(find.textContaining('Needs a few more weeks'), findsOneWidget);
    });
  });

  group('data quality', () {
    Future<void> open(WidgetTester tester, FakeServer server) async {
      await pumpApp(tester, server);
      await tester.tap(find.byKey(const ValueKey('tab-insights')));
      await tester.pump();
      await settle(tester);
      await tester.tap(find.byKey(const ValueKey('data-quality-link')));
      await tester.pumpAndSettle(const Duration(milliseconds: 50));
      await settle(tester);
    }

    testWidgets('draws the calendar, the unmatched share and the gaps', (
      tester,
    ) async {
      await open(tester, serverWith());
      expect(find.byType(DataQualityScreen), findsOneWidget);
      expect(find.byType(CoverageCalendar), findsOneWidget);
      expect(
        find.textContaining('25 of 100 visits (25%) could not be matched'),
        findsOneWidget,
      );
      await tester.scrollUntilVisible(
        find.textContaining('moved the box'),
        300,
      );
      expect(find.textContaining('not counted as zero visits'), findsWidgets);
      expect(
        find.textContaining('marked unreliable, "moved the box"'),
        findsOneWidget,
      );
    });

    testWidgets('a tapped day explains itself in words', (tester) async {
      await open(tester, serverWith());
      final days = find.byWidgetPredicate(
        (w) =>
            w.key is ValueKey<String> &&
            (w.key! as ValueKey<String>).value.startsWith('cal-'),
      );
      await tester.tap(days.first);
      await tester.pump();
      expect(find.byKey(const ValueKey('calendar-detail')), findsOneWidget);
    });

    testWidgets('steps back through earlier weeks without a limit', (
      tester,
    ) async {
      final server = serverWith();
      await open(tester, server);
      final before = server.requests
          .where((r) => r.url.path == '/api/data-quality')
          .length;
      await tester.tap(find.byKey(const ValueKey('range-earlier')));
      await tester.pump();
      await settle(tester);
      final asked = server.requests
          .where((r) => r.url.path == '/api/data-quality')
          .toList();
      expect(asked.length, before + 1);
      final first = DateTime.parse(asked.first.url.queryParameters['start']!);
      final second = DateTime.parse(asked.last.url.queryParameters['start']!);
      expect(first.difference(second).inDays, 91);
    });

    testWidgets('removes a range you marked', (tester) async {
      final server = serverWith();
      server.routes['DELETE /api/coverage/unreliable/7'] = (_) =>
          jsonResponse({}, 204);
      await open(tester, server);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('unmark-7')),
        300,
      );
      await tester.tap(find.byKey(const ValueKey('unmark-7')));
      await tester.pump();
      await settle(tester);
      expect(
        server.requests.any(
          (r) =>
              r.method == 'DELETE' &&
              r.url.path == '/api/coverage/unreliable/7',
        ),
        isTrue,
      );
    });
  });

  group('models', () {
    test('a worth-a-look answer with no notes parses', () {
      final look = WorthALook.fromJson(worthJson());
      expect(look.notes, isEmpty);
      expect(look.waiting.where((w) => !w.ready), hasLength(1));
    });
  });
}
