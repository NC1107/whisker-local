import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/screens/cat_insights_screen.dart';
import 'package:whisker_local/widgets/charts/bar_chart.dart';
import 'package:whisker_local/widgets/charts/line_chart.dart';
import 'package:whisker_local/widgets/charts/time_grid.dart';

import '../support/fixtures.dart';
import 'insights_support.dart';

FakeServer serverWith(
  List<Map<String, Object?>> cats, {
  Map<String, Object?>? robot,
}) => FakeServer(statusJson(cats: cats, robot: robot ?? robotJson()));

Future<void> openInsights(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('tab-insights')));
  await tester.pump();
}

Future<void> settle(WidgetTester tester) async {
  await untilTrue(tester, () => false);
  await tester.pump(const Duration(milliseconds: 50));
}

Future<void> openCat(WidgetTester tester, {int id = 1}) async {
  await openInsights(tester);
  await tester.tap(find.byKey(ValueKey('insights-cat-$id')));
  await tester.pumpAndSettle(const Duration(milliseconds: 50));
  await settle(tester);
}

int insightsRequests(FakeServer server, {String? period}) => server.requests
    .where(
      (r) =>
          r.url.path == '/api/cats/1/insights' &&
          (period == null || r.url.queryParameters['period'] == period),
    )
    .length;

void main() {
  group('insights tab', () {
    testWidgets('shows yesterday for each cat from the status', (tester) async {
      final server = serverWith([
        catJson(),
        catJson(
          id: 2,
          name: 'Pip',
          summary: {
            'yesterday_weight_lb': 8.8,
            'yesterday_visits': 3,
            'yesterday_minutes': 4.0,
            'common_visit_hour': 10,
          },
        ),
      ]);
      await pumpApp(tester, server);
      await openInsights(tester);
      expect(find.text('Miso'), findsOneWidget);
      expect(find.text('Pip'), findsOneWidget);
      expect(find.textContaining('10.2'), findsOneWidget);
      expect(find.textContaining('8.8'), findsOneWidget);
      expect(find.textContaining('7:00'), findsOneWidget);
      expect(find.textContaining('10:00'), findsOneWidget);
      expect(find.text("Yesterday's visits"), findsNWidgets(2));
      expect(find.text('Usual time'), findsNWidgets(2));
    });

    testWidgets('each cat card is one button with a clear label', (
      tester,
    ) async {
      final handle = tester.ensureSemantics();
      final server = serverWith([catJson()]);
      await pumpApp(tester, server);
      await openInsights(tester);
      final card = find.byKey(const ValueKey('insights-cat-1'));
      final semantics = tester.getSemantics(
        find.descendant(of: card, matching: find.byType(Semantics)).first,
      );
      expect(semantics.label, startsWith('Miso, '));
      expect(semantics.label, endsWith('Open insights'));
      expect(semantics.flagsCollection.isButton, isTrue);
      expect(find.bySemanticsLabel('Miso'), findsNothing);
      handle.dispose();
    });

    testWidgets('shows dashes for a cat with no visits yet', (tester) async {
      final server = serverWith([
        catJson(
          summary: {
            'yesterday_weight_lb': null,
            'yesterday_visits': 0,
            'yesterday_minutes': null,
            'common_visit_hour': null,
          },
        ),
      ]);
      await pumpApp(tester, server);
      await openInsights(tester);
      expect(find.textContaining('-'), findsNWidgets(3));
    });

    testWidgets('sends someone with no cats to add one', (tester) async {
      await pumpApp(tester, serverWith([]));
      await openInsights(tester);
      expect(find.byKey(const ValueKey('insights-no-cats')), findsOneWidget);
      await tester.tap(
        find.descendant(
          of: find.byKey(const ValueKey('insights-no-cats')),
          matching: find.text('Add a cat'),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('insights-no-cats')).hitTestable(),
        findsNothing,
      );
    });

    testWidgets('says numbers wait for the robot when it never connected', (
      tester,
    ) async {
      await pumpApp(tester, FakeServer(statusJson(cats: [catJson()])));
      await openInsights(tester);
      expect(find.text('Numbers appear after the first visit'), findsOneWidget);
    });

    testWidgets('lays two cats side by side on a wide window', (tester) async {
      final server = serverWith([catJson(), catJson(id: 2, name: 'Pip')]);
      await pumpApp(tester, server, size: const Size(1280, 900));
      await tester.tap(find.byKey(const ValueKey('tab-insights')));
      await tester.pump();
      expect(find.text('Insights'), findsWidgets);
      final first = tester.getTopLeft(
        find.byKey(const ValueKey('insights-cat-1')),
      );
      final second = tester.getTopLeft(
        find.byKey(const ValueKey('insights-cat-2')),
      );
      expect(second.dy, first.dy);
      expect(second.dx, greaterThan(first.dx));
    });
  });

  group('cat insights', () {
    FakeServer server0() {
      final server = serverWith([catJson()]);
      server.routes['GET /api/cats/1/insights'] = (request) {
        final period = request.url.queryParameters['period'];
        return switch (period) {
          'month' => jsonResponse(
            catInsightsJson(
              period: 'month',
              rangeLabel: 'October 9 to November 7',
              days: 30,
              timeOfVisit: [],
            ),
          ),
          'year' => jsonResponse(
            catInsightsJson(
              period: 'year',
              rangeLabel: 'December 2025 to November 2026',
              days: 12,
              timeOfVisit: [],
            ),
          ),
          _ => jsonResponse(catInsightsJson()),
        };
      };
      return server;
    }

    testWidgets('draws the cards of the design from the answer', (
      tester,
    ) async {
      final server = server0();
      await pumpApp(tester, server);
      await openCat(tester);
      expect(find.text("Miso's insights"), findsOneWidget);
      expect(find.text('2 visits yesterday, about average.'), findsOneWidget);
      expect(find.text('Weight is in range.'), findsOneWidget);
      expect(find.text('November 1 to November 7'), findsOneWidget);
      expect(find.byType(LineChart), findsOneWidget);
      await tester.scrollUntilVisible(find.text('Time in the box'), 400);
      expect(find.byType(BarChart), findsWidgets);
      expect(find.byType(TimeGrid), findsOneWidget);
      expect(find.text('Bigger dots, longer visits'), findsOneWidget);
    });

    testWidgets('steps back and forward and stops at the ends', (tester) async {
      final server = serverWith([catJson()]);
      server.routes['GET /api/cats/1/insights'] = (request) {
        final offset = int.parse(request.url.queryParameters['offset'] ?? '0');
        return jsonResponse(
          catInsightsJson(
            offset: offset,
            rangeLabel: 'Range $offset',
            hasEarlier: offset < 1,
          ),
        );
      };
      await pumpApp(tester, server);
      await openCat(tester);
      expect(find.text('Range 0'), findsOneWidget);
      var later = tester.widget<InkResponse>(
        find.descendant(
          of: find.byKey(const ValueKey('range-later')),
          matching: find.byType(InkResponse),
        ),
      );
      expect(later.onTap, isNull);

      await tester.tap(find.byKey(const ValueKey('range-earlier')));
      await tester.pump();
      await settle(tester);
      expect(find.text('Range 1'), findsOneWidget);
      later = tester.widget<InkResponse>(
        find.descendant(
          of: find.byKey(const ValueKey('range-later')),
          matching: find.byType(InkResponse),
        ),
      );
      expect(later.onTap, isNotNull);
      final earlier = tester.widget<InkResponse>(
        find.descendant(
          of: find.byKey(const ValueKey('range-earlier')),
          matching: find.byType(InkResponse),
        ),
      );
      expect(earlier.onTap, isNull, reason: 'nothing before that');

      await tester.tap(find.byKey(const ValueKey('range-later')));
      await tester.pump();
      await settle(tester);
      expect(find.text('Range 0'), findsOneWidget);
    });

    testWidgets('a period tab asks for that period from the current one', (
      tester,
    ) async {
      final server = server0();
      await pumpApp(tester, server);
      await openCat(tester);
      await tester.tap(find.byKey(const ValueKey('period-month')));
      await tester.pump();
      await settle(tester);
      expect(find.text('October 9 to November 7'), findsOneWidget);
      expect(insightsRequests(server, period: 'month'), 1);
      await tester.tap(find.byKey(const ValueKey('period-year')));
      await tester.pump();
      await settle(tester);
      expect(find.text('December 2025 to November 2026'), findsOneWidget);
    });

    testWidgets('shows the period tapped last when two are asked at once', (
      tester,
    ) async {
      final server = server0();
      await pumpApp(tester, server);
      await openCat(tester);
      await tester.tap(find.byKey(const ValueKey('period-month')));
      await tester.tap(find.byKey(const ValueKey('period-year')));
      await tester.pump();
      await settle(tester);
      expect(find.text('December 2025 to November 2026'), findsOneWidget);
      expect(find.text('October 9 to November 7'), findsNothing);
    });

    testWidgets('shows a failure in the weight card and retries it', (
      tester,
    ) async {
      final server = serverWith([catJson()]);
      var fail = true;
      server.routes['GET /api/cats/1/insights'] = (request) {
        if (fail && request.url.queryParameters['period'] == 'month') {
          return jsonResponse({'detail': 'Database is busy'}, 500);
        }
        return jsonResponse(
          catInsightsJson(period: request.url.queryParameters['period']!),
        );
      };
      await pumpApp(tester, server);
      await openCat(tester);
      await tester.tap(find.byKey(const ValueKey('period-month')));
      await tester.pump();
      await settle(tester);
      expect(find.textContaining("Weight didn't load"), findsOneWidget);
      expect(find.byType(LineChart), findsNothing);
      expect(
        find.text('2 visits yesterday, about average.'),
        findsOneWidget,
        reason: 'the rest of the page is unaffected',
      );
      fail = false;
      await tester.tap(find.text('Try again'));
      await tester.pump();
      await settle(tester);
      expect(find.byType(LineChart), findsOneWidget);
    });

    testWidgets('says so when a period has no weighed visit', (tester) async {
      final server = serverWith([catJson()]);
      server.routes['GET /api/cats/1/insights'] = (request) => jsonResponse(
        catInsightsJson(
          weight: List<double?>.filled(7, null),
          visits: List<double?>.filled(7, null),
          minutes: List<double?>.filled(7, null),
          timeOfVisit: [],
        ),
      );
      await pumpApp(tester, server);
      await openCat(tester);
      expect(find.text('No weighed visits'), findsOneWidget);
      expect(find.byType(LineChart), findsNothing);
      expect(find.byKey(const ValueKey('period-week')), findsOneWidget);
      await tester.scrollUntilVisible(find.text('Time of visit'), 300);
      expect(find.text('No visits this week'), findsWidgets);
    });

    testWidgets('survives being closed while answers are on their way', (
      tester,
    ) async {
      final server = server0();
      await pumpApp(tester, server);
      await openInsights(tester);
      await tester.tap(find.byKey(const ValueKey('insights-cat-1')));
      await tester.pumpAndSettle(const Duration(milliseconds: 50));
      await tester.tap(find.bySemanticsLabel('Back'));
      await tester.pumpAndSettle(const Duration(milliseconds: 50));
      await settle(tester);
      expect(tester.takeException(), isNull);
      expect(find.byType(CatInsightsScreen), findsNothing);
    });

    testWidgets('lays the visit charts side by side on a wide window', (
      tester,
    ) async {
      final server = server0();
      await pumpApp(tester, server, size: const Size(1280, 1400));
      await tester.tap(find.byKey(const ValueKey('tab-insights')));
      await tester.pump();
      await tester.tap(find.byKey(const ValueKey('insights-cat-1')));
      await tester.pumpAndSettle(const Duration(milliseconds: 50));
      await settle(tester);
      final bars = find.byType(BarChart);
      expect(bars, findsNWidgets(2));
      expect(
        tester.getTopLeft(bars.at(0)).dy,
        tester.getTopLeft(bars.at(1)).dy,
      );
    });

    testWidgets('tells someone the cat is gone when it is archived', (
      tester,
    ) async {
      final server = server0();
      await pumpApp(tester, server);
      await openCat(tester);
      server.status = statusJson(cats: [], robot: robotJson());
      await _controllerOf(tester).poll();
      await tester.pump();
      expect(find.text('This cat is no longer listed'), findsOneWidget);
    });
  });
}

AppController _controllerOf(WidgetTester tester) =>
    tester.widget<CatInsightsScreen>(find.byType(CatInsightsScreen)).controller;
