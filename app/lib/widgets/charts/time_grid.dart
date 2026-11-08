import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../theme.dart';
import 'chart_frame.dart';
import 'paint.dart';

const _gutter = 50.0;
const _plotHeight = 216.0;
const _canvasHeight = 220.0;
const _hourLabels = ['12 AM', '6 AM', '12 PM', '6 PM', '12 AM'];

/// One visit on the time-of-visit chart.
class TimeDot {
  const TimeDot({
    required this.column,
    required this.hour,
    required this.durationS,
    required this.tooltip,
  });

  final int column;

  /// Hour of the day as a decimal, so 19.5 is 7:30 PM.
  final double hour;
  final int durationS;
  final String tooltip;
}

/// Days across, hours down, and a dot for each visit that grows with its
/// length.
class TimeGrid extends StatelessWidget {
  const TimeGrid({
    required this.columns,
    required this.dots,
    required this.xLabels,
    required this.semanticLabel,
    super.key,
  });

  final int columns;
  final List<TimeDot> dots;
  final List<String> xLabels;
  final String semanticLabel;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return ChartFrame(
      height: _canvasHeight + chartAxisRowHeight,
      data: [
        columns,
        for (final dot in dots) ...[dot.column, dot.hour, dot.durationS],
        ...xLabels,
      ],
      semanticLabel: semanticLabel,
      pick: (position, size) => _pick(position, size),
      paint: (selected) => _GridPainter(
        palette: palette,
        columns: columns,
        dots: dots,
        xLabels: xLabels,
        selected: selected,
      ),
    );
  }

  int? _pick(Offset position, Size size) {
    int? best;
    var bestDistance = double.infinity;
    for (var i = 0; i < dots.length; i++) {
      final distance =
          (_center(dots[i], columns, size.width) - position).distance;
      final reach = math.max(_diameter(dots[i], columns, size.width) / 2, 16);
      if (distance <= reach && distance < bestDistance) {
        best = i;
        bestDistance = distance;
      }
    }
    return best;
  }
}

double _columnWidth(int columns, double width) => (width - _gutter) / columns;

double _hourY(double hour) => 2 + hour / 24 * 212;

Offset _center(TimeDot dot, int columns, double width) => Offset(
  _gutter + _columnWidth(columns, width) * (dot.column + 0.5),
  _hourY(dot.hour),
);

double _diameter(TimeDot dot, int columns, double width) {
  final wanted = 8 + 16 * math.sqrt((dot.durationS / 480).clamp(0.0, 1.0));
  return math.min(wanted, math.max(6.0, _columnWidth(columns, width) * 1.4));
}

class _GridPainter extends CustomPainter {
  _GridPainter({
    required this.palette,
    required this.columns,
    required this.dots,
    required this.xLabels,
    required this.selected,
  });

  final Palette palette;
  final int columns;
  final List<TimeDot> dots;
  final List<String> xLabels;
  final int? selected;

  @override
  void paint(Canvas canvas, Size size) {
    paintHorizontalGrid(
      canvas,
      palette,
      left: _gutter,
      right: size.width,
      rows: [
        for (var i = 0; i < _hourLabels.length; i++)
          (_hourY(i * 6), _hourLabels[i]),
      ],
      labelSize: 13,
    );
    final grid = Paint()
      ..color = palette.lineSoft
      ..strokeWidth = 1;
    final column = _columnWidth(columns, size.width);
    for (var d = 0; d <= columns; d++) {
      final x = _gutter + column * d;
      canvas.drawLine(Offset(x, 0), Offset(x, _plotHeight), grid);
    }

    final order = List.generate(dots.length, (i) => i)
      ..sort(
        (a, b) => _diameter(
          dots[b],
          columns,
          size.width,
        ).compareTo(_diameter(dots[a], columns, size.width)),
      );
    for (final i in order) {
      final dot = dots[i];
      if (dot.column < 0 || dot.column >= columns) continue;
      canvas.drawCircle(
        _center(dot, columns, size.width),
        _diameter(dot, columns, size.width) / 2,
        Paint()
          ..color = palette.chart.withValues(alpha: i == selected ? 1 : 0.75),
      );
    }

    paintAxisLabels(
      canvas,
      palette,
      labels: xLabels,
      indexes: visibleLabelIndexes(xLabels, column),
      centerOf: (i) => _gutter + column * (i + 0.5),
      left: _gutter,
      right: size.width,
      top: _canvasHeight + 6,
    );

    final index = selected;
    if (index != null && index < dots.length) {
      final dot = dots[index];
      final anchor = _center(dot, columns, size.width);
      canvas.drawCircle(
        anchor,
        _diameter(dot, columns, size.width) / 2 + 2,
        Paint()
          ..color = palette.ink
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
      paintTooltip(canvas, size, anchor, dot.tooltip, palette);
    }
  }

  @override
  bool shouldRepaint(_GridPainter old) =>
      old.palette != palette ||
      old.selected != selected ||
      old.columns != columns ||
      old.dots != dots ||
      old.xLabels != xLabels;
}
