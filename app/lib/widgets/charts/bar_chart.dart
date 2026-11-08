import 'package:flutter/material.dart';

import '../../theme.dart';
import 'axis.dart';
import 'chart_frame.dart';
import 'paint.dart';

const _plotTop = 8.0;
const _plotHeight = 134.0;
const _canvasHeight = 150.0;
const _inset = 4.0;

/// One bar per point with a dashed average. A null value has no bar and
/// cannot be selected.
class BarChart extends StatelessWidget {
  const BarChart({
    required this.values,
    required this.xLabels,
    required this.tooltips,
    required this.semanticLabel,
    this.average,
    super.key,
  });

  final List<double?> values;
  final double? average;
  final List<String> xLabels;
  final List<String?> tooltips;
  final String semanticLabel;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final geometry = _Geometry.of(values, average, values.length);
    return ChartFrame(
      height: _canvasHeight + chartAxisRowHeight,
      data: [...values, average, ...xLabels, ...tooltips],
      semanticLabel: semanticLabel,
      pick: (position, size) => geometry.pick(position, size, tooltips),
      paint: (selected) => _BarPainter(
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
  _Geometry(this.axis, this.gutter, this.count);

  factory _Geometry.of(List<double?> values, double? average, int count) {
    final present = [...values.whereType<double>(), ?average];
    final top = present.isEmpty ? 1.0 : present.reduce((a, b) => a > b ? a : b);
    final axis = ChartAxis.fit(0, top, fromZero: true);
    final widest = axis.ticks
        .map(
          (tick) => chartText(axis.label(tick), const Color(0xFF000000)).width,
        )
        .reduce((a, b) => a > b ? a : b);
    return _Geometry(axis, widest + 6 < 16 ? 22 : widest + 12, count);
  }

  final ChartAxis axis;
  final double gutter;
  final int count;

  double y(double value) => _plotTop + (1 - axis.fraction(value)) * _plotHeight;

  double slot(double width) => (width - gutter - _inset * 2) / count;

  double gap(double width) {
    final room = slot(width);
    final wanted = count <= 7 ? 12.0 : (count <= 12 ? 8.0 : 2.0);
    return wanted > room * 0.5 ? room * 0.5 : wanted;
  }

  double left(int index, double width) => gutter + _inset + index * slot(width);

  int? pick(Offset position, Size size, List<String?> tooltips) {
    if (position.dy > _canvasHeight || count == 0) return null;
    final index = ((position.dx - gutter - _inset) / slot(size.width)).floor();
    if (index < 0 || index >= count || tooltips[index] == null) return null;
    return index;
  }
}

class _BarPainter extends CustomPainter {
  _BarPainter({
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
    paintHorizontalGrid(
      canvas,
      palette,
      left: geometry.gutter,
      right: size.width,
      rows: [
        for (final tick in geometry.axis.ticks.reversed)
          (geometry.y(tick), geometry.axis.label(tick)),
      ],
    );

    final slot = geometry.slot(size.width);
    final gap = geometry.gap(size.width);
    final baseline = geometry.y(geometry.axis.min);
    final fill = Paint()..color = palette.chart;

    final index = selected;
    if (index != null && index < values.length && values[index] != null) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTRB(
            geometry.left(index, size.width),
            _plotTop,
            geometry.left(index, size.width) + slot,
            baseline,
          ),
          const Radius.circular(6),
        ),
        Paint()..color = palette.soft,
      );
    }

    for (var i = 0; i < values.length; i++) {
      final value = values[i];
      if (value == null || value <= 0) continue;
      final barLeft = geometry.left(i, size.width) + gap / 2;
      final width = slot - gap;
      final top = geometry.y(value).clamp(0.0, baseline - 2);
      final radius = Radius.circular(width / 2 < 5 ? width / 2 : 5);
      canvas.drawRRect(
        RRect.fromLTRBAndCorners(
          barLeft,
          top,
          barLeft + width,
          baseline,
          topLeft: radius,
          topRight: radius,
        ),
        fill,
      );
    }

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

    paintAxisLabels(
      canvas,
      palette,
      labels: xLabels,
      indexes: visibleLabelIndexes(xLabels, slot),
      centerOf: (i) => geometry.left(i, size.width) + slot / 2,
      left: geometry.gutter,
      right: size.width,
      top: _canvasHeight + 8,
    );

    if (index != null && index < values.length) {
      final value = values[index];
      final message = tooltips[index];
      if (value != null && message != null) {
        paintTooltip(
          canvas,
          size,
          Offset(
            geometry.left(index, size.width) + slot / 2,
            geometry.y(value),
          ),
          message,
          palette,
        );
      }
    }
  }

  @override
  bool shouldRepaint(_BarPainter old) =>
      old.palette != palette ||
      old.selected != selected ||
      old.average != average ||
      old.values != values ||
      old.xLabels != xLabels ||
      old.tooltips != tooltips;
}
