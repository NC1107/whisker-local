import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart' show DateFormat;

import '../api.dart';
import '../format.dart';
import '../theme.dart';

const _plotHeight = 168.0;
const _axisWidth = 40.0;
const _axisHeight = 26.0;
const _labelSize = 12.0;

/// Round tick values covering [low] to [high], about [target] of them.
List<double> niceTicks(double low, double high, {int target = 4}) {
  if (high <= low) high = low + 1;
  final rough = (high - low) / math.max(target - 1, 1);
  final magnitude = math
      .pow(10, (math.log(rough) / math.ln10).floor())
      .toDouble();
  final normalized = rough / magnitude;
  final step =
      magnitude *
      (normalized <= 1
          ? 1
          : normalized <= 2
          ? 2
          : normalized <= 5
          ? 5
          : 10);
  final first = (low / step).floor() * step;
  final last = (high / step).ceil() * step;
  return [for (var tick = first; tick <= last + step / 2; tick += step) tick];
}

/// Visits per calendar day, oldest first, including days with none.
List<int> visitsPerDay(List<HistoryEvent> visits, DateTime now, int days) {
  final today = DateTime(now.year, now.month, now.day);
  final counts = List<int>.filled(days, 0);
  for (final visit in visits) {
    final date = DateTime(visit.at.year, visit.at.month, visit.at.day);
    final index = days - 1 - (today.difference(date).inHours / 24).round();
    if (index >= 0 && index < days) counts[index]++;
  }
  return counts;
}

class _Scale {
  const _Scale(this.size, this.xMin, this.xMax, this.yMin, this.yMax);

  final Size size;
  final double xMin;
  final double xMax;
  final double yMin;
  final double yMax;

  double get _plotWidth => size.width - _axisWidth;

  double x(double value) =>
      _axisWidth + (value - xMin) / (xMax - xMin) * _plotWidth;
  double y(double value) => (1 - (value - yMin) / (yMax - yMin)) * _plotHeight;
}

TextPainter _text(String value, Color color, {bool bold = false}) =>
    TextPainter(
      text: TextSpan(
        text: value,
        style: TextStyle(
          fontFamily: 'Atkinson',
          fontSize: _labelSize,
          color: color,
          fontWeight: bold ? FontWeight.w700 : FontWeight.w400,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();

void _paintFrame(
  Canvas canvas,
  _Scale scale,
  List<double> ticks,
  String Function(double) tickLabel,
  List<(double, String)> xLabels,
  Palette palette,
) {
  final grid = Paint()
    ..color = palette.hairline
    ..strokeWidth = 1;
  for (final tick in ticks) {
    final y = scale.y(tick);
    canvas.drawLine(Offset(_axisWidth, y), Offset(scale.size.width, y), grid);
    final label = _text(tickLabel(tick), palette.muted);
    label.paint(
      canvas,
      Offset(_axisWidth - 8 - label.width, y - label.height / 2),
    );
  }
  for (final (position, text) in xLabels) {
    final label = _text(text, palette.muted);
    final left = (scale.x(position) - label.width / 2)
        .clamp(_axisWidth, scale.size.width - label.width)
        .toDouble();
    label.paint(canvas, Offset(left, _plotHeight + 8));
  }
}

void _paintTooltip(
  Canvas canvas,
  Size size,
  Offset anchor,
  String text,
  Palette palette,
) {
  const padX = 8.0;
  const padY = 5.0;
  final label = _text(text, palette.surface, bold: true);
  final width = label.width + padX * 2;
  final height = label.height + padY * 2;
  final left = (anchor.dx - width / 2)
      .clamp(0.0, size.width - width)
      .toDouble();
  final above = anchor.dy - height - 12;
  final top = above >= 0 ? above : anchor.dy + 12;
  canvas.drawRRect(
    RRect.fromRectAndRadius(
      Rect.fromLTWH(left, top, width, height),
      const Radius.circular(6),
    ),
    Paint()..color = palette.ink,
  );
  label.paint(canvas, Offset(left + padX, top + padY));
}

List<(double, String)> _dateLabels(
  DateTime start,
  DateTime end,
  double Function(DateTime) at,
) {
  final middle = start.add(end.difference(start) ~/ 2);
  final format = DateFormat.MMMd();
  return [
    for (final date in [start, middle, end]) (at(date), format.format(date)),
  ];
}

/// Tap on touch and hover with a mouse both select the nearest mark.
class _Interactive extends StatefulWidget {
  const _Interactive({
    required this.semantics,
    required this.builder,
    required this.pick,
  });

  final String semantics;
  final CustomPainter Function(Size size, int? selected) builder;
  final int? Function(Offset position, Size size) pick;

  @override
  State<_Interactive> createState() => _InteractiveState();
}

class _InteractiveState extends State<_Interactive> {
  int? _selected;

  void _select(Offset position, Size size) {
    final next = widget.pick(position, size);
    if (next != _selected) setState(() => _selected = next);
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      image: true,
      label: widget.semantics,
      child: SizedBox(
        height: _plotHeight + _axisHeight,
        child: LayoutBuilder(
          builder: (context, constraints) {
            final size = Size(constraints.maxWidth, _plotHeight + _axisHeight);
            return MouseRegion(
              onHover: (event) => _select(event.localPosition, size),
              onExit: (_) => setState(() => _selected = null),
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTapDown: (details) => _select(details.localPosition, size),
                onHorizontalDragUpdate: (details) =>
                    _select(details.localPosition, size),
                child: CustomPaint(
                  size: size,
                  painter: widget.builder(size, _selected),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

/// Dots rather than a line: the robot does not say which cat it weighed, so joining the readings
/// would draw a trend through different animals.
class WeightChart extends StatelessWidget {
  const WeightChart({
    required this.visits,
    required this.days,
    required this.now,
    super.key,
  });

  final List<HistoryEvent> visits;
  final int days;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final weighed = visits.where((visit) => visit.weightLb != null).toList();
    final weights = weighed.map((visit) => visit.weightLb!).toList();
    final ticks = niceTicks(
      weights.reduce(math.min) - 0.5,
      weights.reduce(math.max) + 0.5,
    );
    final start = now.subtract(Duration(days: days));
    double time(DateTime date) => date.millisecondsSinceEpoch.toDouble();
    _Scale scaleFor(Size size) =>
        _Scale(size, time(start), time(now), ticks.first, ticks.last);

    return _Interactive(
      semantics:
          'Weight at each visit, ${weights.reduce(math.min).toStringAsFixed(1)} to '
          '${weights.reduce(math.max).toStringAsFixed(1)} pounds. Every visit is listed below.',
      builder: (size, selected) => _WeightPainter(
        scale: scaleFor(size),
        points: weighed,
        ticks: ticks,
        xLabels: _dateLabels(start, now, time),
        selected: selected,
        palette: palette,
      ),
      pick: (position, size) {
        final scale = scaleFor(size);
        int? best;
        var bestDistance = 28.0;
        for (var i = 0; i < weighed.length; i++) {
          final point = Offset(
            scale.x(time(weighed[i].at)),
            scale.y(weighed[i].weightLb!),
          );
          final distance = (point - position).distance;
          if (distance < bestDistance) {
            best = i;
            bestDistance = distance;
          }
        }
        return best;
      },
    );
  }
}

class _WeightPainter extends CustomPainter {
  _WeightPainter({
    required this.scale,
    required this.points,
    required this.ticks,
    required this.xLabels,
    required this.selected,
    required this.palette,
  });

  final _Scale scale;
  final List<HistoryEvent> points;
  final List<double> ticks;
  final List<(double, String)> xLabels;
  final int? selected;
  final Palette palette;

  Offset _at(HistoryEvent point) => Offset(
    scale.x(point.at.millisecondsSinceEpoch.toDouble()),
    scale.y(point.weightLb!),
  );

  @override
  void paint(Canvas canvas, Size size) {
    _paintFrame(
      canvas,
      scale,
      ticks,
      (tick) => tick.toStringAsFixed(tick % 1 == 0 ? 0 : 1),
      xLabels,
      palette,
    );
    final ring = Paint()..color = palette.surface;
    final dot = Paint()..color = palette.chart;
    for (final point in points) {
      final center = _at(point);
      canvas
        ..drawCircle(center, 6, ring)
        ..drawCircle(center, 4, dot);
    }
    final index = selected;
    if (index != null) {
      final point = points[index];
      final center = _at(point);
      canvas
        ..drawCircle(center, 8, ring)
        ..drawCircle(center, 6, dot);
      final when = DateFormat.MMMd().add_jm().format(point.at);
      _paintTooltip(
        canvas,
        size,
        center,
        '${weightText(point.weightLb!)}, $when',
        palette,
      );
    }
  }

  @override
  bool shouldRepaint(_WeightPainter old) =>
      old.selected != selected ||
      old.points != points ||
      old.scale.size != scale.size ||
      old.palette != palette;
}

class VisitsChart extends StatelessWidget {
  const VisitsChart({required this.counts, required this.now, super.key});

  final List<int> counts;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final peak = counts.reduce(math.max);
    final ticks = niceTicks(0, math.max(peak, 4).toDouble());
    final days = counts.length;
    final today = DateTime(now.year, now.month, now.day);
    DateTime dayAt(int index) =>
        DateTime(today.year, today.month, today.day - (days - 1 - index));
    _Scale scaleFor(Size size) =>
        _Scale(size, 0, days.toDouble(), ticks.first, ticks.last);

    return _Interactive(
      semantics:
          'Visits per day over $days days, at most $peak in one day. '
          'Every visit is listed below.',
      builder: (size, selected) => _VisitsPainter(
        scale: scaleFor(size),
        counts: counts,
        ticks: ticks,
        xLabels: [
          (0.5, DateFormat.MMMd().format(dayAt(0))),
          (days / 2, DateFormat.MMMd().format(dayAt(days ~/ 2))),
          (days - 0.5, DateFormat.MMMd().format(dayAt(days - 1))),
        ],
        selected: selected,
        dayAt: dayAt,
        palette: palette,
      ),
      pick: (position, size) {
        final scale = scaleFor(size);
        final step = (scale.x(1) - scale.x(0));
        final index = ((position.dx - _axisWidth) / step).floor();
        return index >= 0 && index < days ? index : null;
      },
    );
  }
}

class _VisitsPainter extends CustomPainter {
  _VisitsPainter({
    required this.scale,
    required this.counts,
    required this.ticks,
    required this.xLabels,
    required this.selected,
    required this.dayAt,
    required this.palette,
  });

  final _Scale scale;
  final List<int> counts;
  final List<double> ticks;
  final List<(double, String)> xLabels;
  final int? selected;
  final DateTime Function(int) dayAt;
  final Palette palette;

  @override
  void paint(Canvas canvas, Size size) {
    _paintFrame(
      canvas,
      scale,
      ticks,
      (tick) => tick.toStringAsFixed(0),
      xLabels,
      palette,
    );
    final step = scale.x(1) - scale.x(0);
    final gap = step >= 6 ? 2.0 : 1.0;
    final width = math.max(step - gap, 1.0);
    final radius = Radius.circular(math.min(width / 2, 4));
    final baseline = scale.y(0);
    for (var i = 0; i < counts.length; i++) {
      if (counts[i] == 0) continue;
      final rect = Rect.fromLTRB(
        scale.x(i.toDouble()) + gap / 2,
        scale.y(counts[i].toDouble()),
        scale.x(i.toDouble()) + gap / 2 + width,
        baseline,
      );
      final isDimmed = selected != null && selected != i;
      canvas.drawRRect(
        RRect.fromRectAndCorners(rect, topLeft: radius, topRight: radius),
        Paint()..color = palette.chart.withValues(alpha: isDimmed ? 0.45 : 1),
      );
    }
    final index = selected;
    if (index != null) {
      final count = counts[index];
      final anchor = Offset(
        scale.x(index.toDouble()) + step / 2,
        scale.y(count.toDouble()),
      );
      final noun = count == 1 ? 'visit' : 'visits';
      _paintTooltip(
        canvas,
        size,
        anchor,
        '$count $noun, ${DateFormat.MMMd().format(dayAt(index))}',
        palette,
      );
    }
  }

  @override
  bool shouldRepaint(_VisitsPainter old) =>
      old.selected != selected ||
      old.counts != counts ||
      old.scale.size != scale.size ||
      old.palette != palette;
}
