import 'package:flutter/material.dart';

import '../../theme.dart';
import 'axis.dart';
import 'chart_frame.dart';
import 'paint.dart';

const _plotTop = 8.0;
const _plotHeight = 164.0;
const _canvasHeight = 180.0;
const _inset = 4.0;

/// A line through one value per point, with a dashed average and a break
/// wherever a value is missing.
class LineChart extends StatelessWidget {
  const LineChart({
    required this.values,
    required this.xLabels,
    required this.tooltips,
    required this.semanticLabel,
    this.average,
    super.key,
  });

  final List<double?> values;
  final double? average;

  /// One per point; only some are printed.
  final List<String> xLabels;

  /// One per point; a null one cannot be selected.
  final List<String?> tooltips;
  final String semanticLabel;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final geometry = _Geometry.of(values, average);
    return ChartFrame(
      height: _canvasHeight + chartAxisRowHeight,
      data: [...values, average, ...xLabels, ...tooltips],
      semanticLabel: semanticLabel,
      pick: (position, size) => geometry.pick(position, size, tooltips),
      paint: (selected) => _LinePainter(
        palette: palette,
        geometry: geometry,
        values: values,
        average: average,
        xLabels: xLabels,
        tooltips: tooltips,
        selected: selected,
      ),
    );
  }
}

class _Geometry {
  _Geometry(this.axis, this.gutter);

  factory _Geometry.of(List<double?> values, double? average) {
    final present = [...values.whereType<double>(), ?average];
    final axis = present.isEmpty
        ? ChartAxis.fit(0, 1)
        : ChartAxis.fit(
            present.reduce((a, b) => a < b ? a : b),
            present.reduce((a, b) => a > b ? a : b),
          );
    final widest = axis.ticks
        .map(
          (tick) => chartText(axis.label(tick), const Color(0xFF000000)).width,
        )
        .reduce((a, b) => a > b ? a : b);
    return _Geometry(axis, widest + 6 < 22 ? 28 : widest + 12);
  }

  final ChartAxis axis;
  final double gutter;

  double y(double value) => _plotTop + (1 - axis.fraction(value)) * _plotHeight;

  double x(int index, int count, double width) {
    final span = width - gutter - _inset * 2;
    if (count < 2) return gutter + _inset + span / 2;
    return gutter + _inset + index / (count - 1) * span;
  }

  int? pick(Offset position, Size size, List<String?> tooltips) {
    if (position.dy > _canvasHeight) return null;
    int? best;
    var bestDistance = double.infinity;
    for (var i = 0; i < tooltips.length; i++) {
      if (tooltips[i] == null) continue;
      final distance = (x(i, tooltips.length, size.width) - position.dx).abs();
      if (distance < bestDistance) {
        best = i;
        bestDistance = distance;
      }
    }
    return best;
  }
}

class _LinePainter extends CustomPainter {
  _LinePainter({
    required this.palette,
    required this.geometry,
    required this.values,
    required this.average,
    required this.xLabels,
    required this.tooltips,
    required this.selected,
  });

  final Palette palette;
  final _Geometry geometry;
  final List<double?> values;
  final double? average;
  final List<String> xLabels;
  final List<String?> tooltips;
  final int? selected;

  @override
  void paint(Canvas canvas, Size size) {
    final ticks = geometry.axis.ticks;
    paintHorizontalGrid(
      canvas,
      palette,
      left: geometry.gutter,
      right: size.width,
      rows: [
        for (final tick in ticks.reversed)
          (geometry.y(tick), geometry.axis.label(tick)),
      ],
    );

    if (average != null) {
      final y = geometry.y(average!);
      paintDashed(
        canvas,
        Offset(geometry.gutter, y),
        Offset(size.width, y),
        Paint()
          ..color = palette.avg
          ..strokeWidth = 2,
      );
    }

    final line = Paint()
      ..color = palette.chart
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round;
    final dot = Paint()..color = palette.chart;
    var run = <Offset>[];
    void flush() {
      if (run.length == 1) canvas.drawCircle(run.first, 3.5, dot);
      if (run.length > 1) {
        final path = Path()..moveTo(run.first.dx, run.first.dy);
        for (final point in run.skip(1)) {
          path.lineTo(point.dx, point.dy);
        }
        canvas.drawPath(path, line);
      }
      run = [];
    }

    for (var i = 0; i < values.length; i++) {
      final value = values[i];
      if (value == null) {
        flush();
      } else {
        run.add(
          Offset(geometry.x(i, values.length, size.width), geometry.y(value)),
        );
      }
    }
    flush();

    paintAxisLabels(
      canvas,
      palette,
      labels: xLabels,
      indexes: visibleLabelIndexes(xLabels, 0, endsOnly: true),
      centerOf: (i) => geometry.x(i, xLabels.length, size.width),
      left: geometry.gutter,
      right: size.width,
      top: _canvasHeight + 8,
    );

    final index = selected;
    if (index != null && index < values.length) {
      final value = values[index];
      final message = tooltips[index];
      if (value != null && message != null) {
        final anchor = Offset(
          geometry.x(index, values.length, size.width),
          geometry.y(value),
        );
        canvas.drawCircle(anchor, 7, Paint()..color = palette.card);
        canvas.drawCircle(anchor, 5, dot);
        paintTooltip(canvas, size, anchor, message, palette);
      }
    }
  }

  @override
  bool shouldRepaint(_LinePainter old) =>
      old.palette != palette ||
      old.selected != selected ||
      old.average != average ||
      old.values != values ||
      old.xLabels != xLabels ||
      old.tooltips != tooltips;
}
