import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../../theme.dart';

const chartLabelSize = 14.0;
const chartAxisRowHeight = 26.0;

TextPainter chartText(
  String value,
  Color color, {
  double size = chartLabelSize,
  bool bold = false,
}) => TextPainter(
  text: TextSpan(
    text: value,
    style: TextStyle(
      fontFamily: fontFamily,
      fontSize: size,
      color: color,
      fontWeight: bold ? FontWeight.w700 : FontWeight.w400,
    ),
  ),
  textDirection: ui.TextDirection.ltr,
  maxLines: 1,
)..layout();

/// A straight dashed line, drawn as short segments so it stays crisp.
void paintDashed(
  Canvas canvas,
  Offset from,
  Offset to,
  Paint paint, {
  double dash = 7,
  double gap = 6,
}) {
  final length = (to - from).distance;
  if (length == 0) return;
  final direction = (to - from) / length;
  for (var at = 0.0; at < length; at += dash + gap) {
    final end = at + dash > length ? length : at + dash;
    canvas.drawLine(from + direction * at, from + direction * end, paint);
  }
}

void paintHorizontalGrid(
  Canvas canvas,
  Palette palette, {
  required double left,
  required double right,
  required List<(double y, String label)> rows,
  double labelSize = chartLabelSize,
}) {
  final grid = Paint()
    ..color = palette.lineSoft
    ..strokeWidth = 1;
  for (final (y, label) in rows) {
    canvas.drawLine(Offset(left, y), Offset(right, y), grid);
    final text = chartText(label, palette.sub, size: labelSize);
    text.paint(canvas, Offset(left - 6 - text.width, y - text.height / 2));
  }
}

/// The indexes of [labels] worth printing: all when each fits in its slot,
/// otherwise the first, middle and last.
List<int> visibleLabelIndexes(
  List<String> labels,
  double slotWidth, {
  bool endsOnly = false,
}) {
  if (labels.isEmpty) return const [];
  final ends = {0, (labels.length - 1) ~/ 2, labels.length - 1}.toList()
    ..sort();
  if (endsOnly) return ends;
  final widest = labels
      .map((label) => chartText(label, const Color(0xFF000000)).width)
      .reduce((a, b) => a > b ? a : b);
  return widest + 6 <= slotWidth
      ? List.generate(labels.length, (i) => i)
      : ends;
}

/// Prints the axis labels under a plot. The first one starts at [left], the
/// last one ends at [right], and the others sit centered on their [centers].
void paintAxisLabels(
  Canvas canvas,
  Palette palette, {
  required List<String> labels,
  required List<int> indexes,
  required double Function(int index) centerOf,
  required double left,
  required double right,
  required double top,
}) {
  for (final index in indexes) {
    final text = chartText(labels[index], palette.sub);
    final x = (centerOf(index) - text.width / 2).clamp(
      left,
      right - text.width,
    );
    text.paint(canvas, Offset(x.toDouble(), top));
  }
}

/// A dark label above [anchor], or below it when there is no room above.
void paintTooltip(
  Canvas canvas,
  Size size,
  Offset anchor,
  String message,
  Palette palette,
) {
  const padX = 10.0;
  const padY = 6.0;
  final text = chartText(message, palette.bg, size: 14, bold: true);
  final width = text.width + padX * 2;
  final height = text.height + padY * 2;
  final left = (anchor.dx - width / 2).clamp(0.0, size.width - width);
  final above = anchor.dy - height - 12;
  final top = above >= 0 ? above : anchor.dy + 14;
  canvas.drawRRect(
    RRect.fromRectAndRadius(
      Rect.fromLTWH(left, top, width, height),
      const Radius.circular(8),
    ),
    Paint()..color = palette.ink,
  );
  text.paint(canvas, Offset(left + padX, top + padY));
}
