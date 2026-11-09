import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api.dart';
import '../../theme.dart';
import '../charts/axis.dart';
import '../charts/chart_frame.dart';
import '../charts/insight_text.dart';
import '../charts/paint.dart';
import 'chart_keys.dart';

const _plotTop = 10.0;
const _plotHeight = 200.0;
const _canvasHeight = 216.0;

/// Every weight reading as a dot, the two-week median as a line with its
/// spread as a band, the frozen anchor as a dashed line, and the days with no
/// data or unreliable data as hatched stripes. One axis, in pounds.
class WeightDotsChart extends StatelessWidget {
  const WeightDotsChart({required this.chart, super.key});

  final WeightChart chart;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final geometry = _Geometry.of(chart);
    final readings = chart.readings;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ChartFrame(
          height: _canvasHeight + chartAxisRowHeight,
          data: [
            chart.start,
            chart.end,
            chart.anchorLb,
            readings.length,
            chart.line.length,
            chart.gaps.length,
          ],
          semanticLabel: _summary(),
          pick: (position, size) => geometry.pick(position, size, readings),
          paint: (selected) => _Painter(
            palette: palette,
            chart: chart,
            geometry: geometry,
            selected: selected,
          ),
        ),
        const SizedBox(height: 8),
        MarkLegend(
          items: [
            const KeyItem('Reading', KeyMark.dot),
            if (readings.any((r) => r.hollow))
              const KeyItem('Not trusted', KeyMark.ring),
            if (chart.line.isNotEmpty) ...const [
              KeyItem('Two-week median', KeyMark.line),
              KeyItem('Usual spread', KeyMark.band),
            ],
            if (chart.anchorLb != null) const KeyItem('Anchor', KeyMark.dash),
            if (chart.gaps.isNotEmpty)
              const KeyItem('No or unsure data', KeyMark.hatch),
          ],
        ),
      ],
    );
  }

  String _summary() {
    final count = chart.readings.length;
    final span =
        '${DateFormat.MMMd().format(chart.start)} to ${DateFormat.MMMd().format(chart.end)}';
    return 'Weight readings from $span, $count in all'
        '${chart.medianLb == null ? '' : ', recent median ${weightValue(chart.medianLb)} pounds'}.';
  }
}

class _Geometry {
  _Geometry(this.axis, this.gutter, this.days, this.start);

  factory _Geometry.of(WeightChart chart) {
    final values = <double>[
      for (final r in chart.readings)
        if (!r.hollow) r.lb,
      for (final p in chart.line) ...[p.low, p.high],
      ?chart.anchorLb,
    ];
    if (values.isEmpty) {
      values.addAll([for (final r in chart.readings) r.lb]);
    }
    final axis = values.isEmpty
        ? ChartAxis.fit(0, 1)
        : ChartAxis.fit(values.reduce(math.min), values.reduce(math.max));
    final widest = axis.ticks
        .map((t) => chartText(axis.label(t), const Color(0xFF000000)).width)
        .reduce(math.max);
    return _Geometry(
      axis,
      widest + 12,
      math.max(chart.end.difference(chart.start).inDays + 1, 1),
      chart.start,
    );
  }

  final ChartAxis axis;
  final double gutter;
  final int days;
  final DateTime start;

  double y(double lb) => (_plotTop + (1 - axis.fraction(lb)) * _plotHeight)
      .clamp(_plotTop, _plotTop + _plotHeight);

  /// [day] counts whole days from the start; the fraction is the time of day.
  double x(double day, double width) =>
      gutter + 4 + day / days * (width - gutter - 8);

  double dayOf(DateTime date, DateTime start, [double hour = 0]) =>
      date.difference(start).inDays + hour / 24;

  int? pick(Offset position, Size size, List<WeightReading> readings) {
    if (position.dy > _canvasHeight) return null;
    int? best;
    var bestDistance = 22.0;
    for (var i = 0; i < readings.length; i++) {
      final r = readings[i];
      final at = Offset(x(dayOf(r.date, start, r.hour), size.width), y(r.lb));
      final d = (at - position).distance;
      if (d < bestDistance) {
        best = i;
        bestDistance = d;
      }
    }
    return best;
  }
}

class _Painter extends CustomPainter {
  _Painter({
    required this.palette,
    required this.chart,
    required this.geometry,
    required this.selected,
  });

  final Palette palette;
  final WeightChart chart;
  final _Geometry geometry;
  final int? selected;

  @override
  void paint(Canvas canvas, Size size) {
    final g = geometry;
    paintHorizontalGrid(
      canvas,
      palette,
      left: g.gutter,
      right: size.width,
      rows: [for (final t in g.axis.ticks.reversed) (g.y(t), g.axis.label(t))],
    );
    for (final gap in chart.gaps) {
      final left = g.x(g.dayOf(gap.start, chart.start), size.width);
      final right = g.x(g.dayOf(gap.end, chart.start) + 1, size.width);
      final rect = Rect.fromLTRB(left, _plotTop, right, _plotTop + _plotHeight);
      canvas.drawRect(
        rect,
        Paint()..color = palette.sub.withValues(alpha: 0.08),
      );
      paintHatch(
        canvas,
        rect,
        palette.sub.withValues(alpha: gap.kind == 'no_data' ? 0.35 : 0.2),
        gap: gap.kind == 'no_data' ? 6 : 12,
      );
    }
    _paintBand(canvas, size);
    if (chart.anchorLb != null) {
      final y = g.y(chart.anchorLb!);
      paintDashed(
        canvas,
        Offset(g.gutter, y),
        Offset(size.width, y),
        Paint()
          ..color = palette.avg
          ..strokeWidth = 2,
      );
    }
    _paintMedian(canvas, size);

    final many = chart.readings.length > 600;
    final radius = many ? 2.2 : 3.4;
    final fill = Paint()..color = palette.chart.withValues(alpha: 0.6);
    final ring = Paint()
      ..color = palette.ink.withValues(alpha: 0.75)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    for (final r in chart.readings) {
      final at = _at(r, size);
      canvas.drawCircle(
        at,
        r.hollow ? radius + 0.8 : radius,
        r.hollow ? ring : fill,
      );
    }

    final labels = [
      DateFormat.MMMd().format(chart.start),
      DateFormat.MMMd().format(chart.start.add(Duration(days: g.days ~/ 2))),
      DateFormat.MMMd().format(chart.end),
    ];
    paintAxisLabels(
      canvas,
      palette,
      labels: labels,
      indexes: const [0, 1, 2],
      centerOf: (i) => g.x(g.days * i / 2, size.width),
      left: g.gutter,
      right: size.width,
      top: _canvasHeight + 8,
    );

    final index = selected;
    if (index != null && index < chart.readings.length) {
      final r = chart.readings[index];
      final at = _at(r, size);
      canvas.drawCircle(at, 7, Paint()..color = palette.card);
      canvas.drawCircle(at, 5, Paint()..color = palette.chart);
      final when = '${DateFormat.MMMd().format(r.date)} ${clockText(r.hour)}';
      final why = switch (r.why) {
        'unreliable' => ', marked unreliable',
        'far_from_usual' => ', far from usual',
        _ => '',
      };
      paintTooltip(
        canvas,
        size,
        at,
        '$when: ${weightValue(r.lb)} lb$why',
        palette,
      );
    }
  }

  Offset _at(WeightReading r, Size size) => Offset(
    geometry.x(geometry.dayOf(r.date, chart.start, r.hour), size.width),
    geometry.y(r.lb),
  );

  /// Runs of the line that are on consecutive days; a skipped day breaks it.
  List<List<WeightLinePoint>> _runs() {
    final runs = <List<WeightLinePoint>>[];
    for (final p in chart.line) {
      if (runs.isNotEmpty &&
          p.date.difference(runs.last.last.date).inDays == 1) {
        runs.last.add(p);
      } else {
        runs.add([p]);
      }
    }
    return runs;
  }

  Offset _mid(WeightLinePoint p, Size size, double lb) => Offset(
    geometry.x(geometry.dayOf(p.date, chart.start) + 0.5, size.width),
    geometry.y(lb),
  );

  void _paintBand(Canvas canvas, Size size) {
    for (final run in _runs()) {
      if (run.length < 2) continue;
      final path = Path()
        ..moveTo(
          _mid(run.first, size, run.first.high).dx,
          _mid(run.first, size, run.first.high).dy,
        );
      for (final p in run.skip(1)) {
        final o = _mid(p, size, p.high);
        path.lineTo(o.dx, o.dy);
      }
      for (final p in run.reversed) {
        final o = _mid(p, size, p.low);
        path.lineTo(o.dx, o.dy);
      }
      canvas.drawPath(
        path..close(),
        Paint()..color = palette.chart.withValues(alpha: 0.18),
      );
    }
  }

  void _paintMedian(Canvas canvas, Size size) {
    final line = Paint()
      ..color = palette.chart
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3
      ..strokeJoin = StrokeJoin.round;
    for (final run in _runs()) {
      if (run.length < 2) continue;
      final path = Path();
      for (var i = 0; i < run.length; i++) {
        final o = _mid(run[i], size, run[i].median);
        i == 0 ? path.moveTo(o.dx, o.dy) : path.lineTo(o.dx, o.dy);
      }
      canvas.drawPath(path, line);
    }
  }

  @override
  bool shouldRepaint(_Painter old) =>
      old.palette != palette || old.selected != selected || old.chart != chart;
}
