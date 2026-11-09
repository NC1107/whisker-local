import 'dart:async';

import 'dart:ui' show PointerDeviceKind;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/theme.dart';
import 'package:whisker_local/widgets/charts/axis.dart';
import 'package:whisker_local/widgets/charts/bar_chart.dart';
import 'package:whisker_local/widgets/charts/chart_chrome.dart';
import 'package:whisker_local/widgets/charts/chart_frame.dart';
import 'package:whisker_local/widgets/charts/insight_text.dart';
import 'package:whisker_local/widgets/charts/latest_request.dart';

Widget host(Widget child, {Brightness mode = Brightness.light}) => MaterialApp(
  theme: buildTheme(mode),
  home: Scaffold(
    body: Center(child: SizedBox(width: 340, child: child)),
  ),
);

Future<void> tapAt(WidgetTester tester, Finder chart, Offset local) async {
  final origin = tester.getTopLeft(chart);
  await tester.tapAt(origin + local);
  await tester.pump();
}

void main() {
  group('ChartAxis', () {
    test('rounds a weight range to half pound ticks', () {
      final axis = ChartAxis.fit(9.9, 10.6);
      expect(axis.ticks.first, lessThanOrEqualTo(9.9));
      expect(axis.ticks.last, greaterThanOrEqualTo(10.6));
      expect(axis.ticks.length, lessThanOrEqualTo(6));
    });

    test('starts bars at zero and covers the tallest bar', () {
      final axis = ChartAxis.fit(0, 3, fromZero: true);
      expect(axis.ticks, [0, 1, 2, 3]);
      expect(axis.label(2), '2');
    });

    test('gives an all zero series a plot with room', () {
      final axis = ChartAxis.fit(0, 0, fromZero: true);
      expect(axis.max, greaterThan(axis.min));
    });

    test('opens a single value into a range around it', () {
      final axis = ChartAxis.fit(10, 10);
      expect(axis.min, lessThan(10));
      expect(axis.max, greaterThan(10));
    });

    test('prints a decimal when the step is below one', () {
      final axis = ChartAxis.fit(10.0, 10.4);
      expect(axis.step, lessThan(1));
      expect(axis.label(10.2), '10.2');
    });
  });

  group('insight text', () {
    test('reads the hour of day in twelve hour time', () {
      expect(hourOfDay(19), (time: '7:00', period: 'PM'));
      expect(hourOfDay(0), (time: '12:00', period: 'AM'));
      expect(hourOfDay(12), (time: '12:00', period: 'PM'));
      expect(hourOfDay(null), isNull);
      expect(clockText(19.2), '7:12 PM');
      expect(clockText(23.999), '12:00 AM');
    });

    test('shows a few seconds as under a minute', () {
      expect(minutesText(0.2), '<1');
      expect(minutesText(3.0), '3');
      expect(minutesText(null), '-');
      expect(trimmed(3.8), '3.8');
      expect(trimmed(3), '3');
    });
  });

  group('LatestRequest', () {
    test('keeps only the answer to the newest request', () async {
      final request = LatestRequest<int>();
      final first = Completer<int>();
      final second = Completer<int>();
      final slow = request.run(() => first.future);
      final fast = request.run(() => second.future);
      second.complete(2);
      await fast;
      first.complete(1);
      await slow;
      expect(request.value, 2);
      expect(request.loading, isFalse);
    });

    test('drops an answer that arrives after dispose', () async {
      final request = LatestRequest<int>();
      final answer = Completer<int>();
      final pending = request.run(() => answer.future);
      request.dispose();
      answer.complete(1);
      await pending;
      expect(request.value, isNull);
    });

    test('shows the message of a failure and clears it on a retry', () async {
      final request = LatestRequest<int>();
      await request.run(() async => throw const ApiException('Server down'));
      expect(request.error, 'Server down');
      await request.run(() async => 3);
      expect(request.error, isNull);
      expect(request.value, 3);
    });

    test('keeps what is shown when a quiet refresh fails', () async {
      final request = LatestRequest<int>();
      await request.run(() async => 3);
      await request.run(
        () async => throw const ApiException('Server down'),
        quiet: true,
      );
      expect(request.value, 3);
      expect(request.error, isNull);
    });
  });

  group('charts', () {
    final days = [for (var i = 0; i < 7; i++) 'D$i'];

    BarChart bars(List<double?> values) => BarChart(
      values: values,
      average: 2,
      xLabels: days,
      tooltips: [
        for (var i = 0; i < values.length; i++)
          values[i] == null ? null : 'D$i: ${values[i]} visits',
      ],
      semanticLabel: 'Visits chart',
    );

    testWidgets('a bar chart paints with gaps and in both themes', (
      tester,
    ) async {
      for (final mode in Brightness.values) {
        await tester.pumpWidget(
          host(bars([1, null, 3, 2, null, null, 1]), mode: mode),
        );
        expect(tester.takeException(), isNull);
        expect(find.bySemanticsLabel('Visits chart'), findsOneWidget);
      }
    });

    testWidgets('selection is cleared when the data changes', (tester) async {
      await tester.pumpWidget(host(bars([1, 2, 3, 2, 1, 2, 3])));
      await tapAt(tester, find.byType(BarChart), const Offset(120, 60));
      final before = _selection(tester);
      expect(before, isNotNull);
      await tester.pumpWidget(host(bars([1, 2, 3, 2, 1, 2, 3])));
      expect(_selection(tester), before, reason: 'same data keeps it');
      await tester.pumpWidget(host(bars([3, 2, 1, 2, 3, 2, 1])));
      expect(_selection(tester), isNull);
    });

    testWidgets('a bar chart ignores days without data', (tester) async {
      const labels = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
      await tester.pumpWidget(
        host(
          const BarChart(
            values: [1, 0, 3, null, null, null, null],
            average: 1.3,
            xLabels: labels,
            tooltips: [
              '1 visit',
              '0 visits',
              '3 visits',
              null,
              null,
              null,
              null,
            ],
            semanticLabel: 'Visits chart',
          ),
        ),
      );
      final chart = find.byType(BarChart);
      await tapAt(tester, chart, const Offset(300, 60));
      expect(_selection(tester), isNull);
      await tapAt(tester, chart, const Offset(120, 60));
      expect(_selection(tester), 2);
      await tapAt(tester, chart, const Offset(120, 60));
      expect(_selection(tester), isNull, reason: 'a second tap lets go');
    });

    testWidgets('a bar chart of zeros paints', (tester) async {
      await tester.pumpWidget(
        host(
          const BarChart(
            values: [0, 0, 0],
            average: 0,
            xLabels: ['a', 'b', 'c'],
            tooltips: ['a', 'b', 'c'],
            semanticLabel: 'Empty bars',
          ),
        ),
      );
      expect(tester.takeException(), isNull);
    });

    testWidgets('hovering reads a value and leaving lets go', (tester) async {
      await tester.pumpWidget(host(bars([1, 2, 3, 2, 1, 2, 3])));
      final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
      await mouse.addPointer(location: Offset.zero);
      addTearDown(mouse.removePointer);
      final origin = tester.getTopLeft(find.byType(BarChart));
      await mouse.moveTo(origin + const Offset(120, 60));
      await tester.pump();
      expect(_selection(tester), isNotNull);
      await mouse.moveTo(origin + const Offset(-50, -50));
      await tester.pump();
      expect(_selection(tester), isNull);
    });
  });

  group('RangeNavigator', () {
    testWidgets('turns an arrow off with a null callback and keeps 44 px', (
      tester,
    ) async {
      var earlier = 0;
      await tester.pumpWidget(
        host(
          RangeNavigator(
            label: 'November 1 to November 7',
            onEarlier: () => earlier++,
            onLater: null,
          ),
        ),
      );
      await tester.tap(find.byKey(const ValueKey('range-earlier')));
      await tester.tap(find.byKey(const ValueKey('range-later')));
      expect(earlier, 1);
      expect(
        tester.getSize(find.byKey(const ValueKey('range-later'))),
        const Size(44, 44),
      );
    });
  });
}

/// The index the painter of the only chart in the tree has selected.
int? _selection(WidgetTester tester) {
  final paint = tester.widget<CustomPaint>(
    find.descendant(
      of: find.byType(ChartFrame).first,
      matching: find.byType(CustomPaint),
    ),
  );
  return (paint.painter! as dynamic).selected as int?;
}
