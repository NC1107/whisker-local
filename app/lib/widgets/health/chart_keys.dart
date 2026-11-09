import 'package:flutter/material.dart';

import '../../theme.dart';

enum KeyMark { dot, ring, line, dash, band, hatch, tick, tickRing }

class KeyItem {
  const KeyItem(this.label, this.mark);

  final String label;
  final KeyMark mark;
}

/// A legend whose marks are drawn the way the chart draws them, so that no
/// series is told apart by color alone.
class MarkLegend extends StatelessWidget {
  const MarkLegend({required this.items, super.key});

  final List<KeyItem> items;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Wrap(
      alignment: WrapAlignment.center,
      spacing: 16,
      runSpacing: 4,
      children: [
        for (final item in items)
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              CustomPaint(
                size: const Size(20, 14),
                painter: _KeyPainter(item.mark, palette),
              ),
              const SizedBox(width: 6),
              Text(
                item.label,
                style: TextStyle(fontSize: 14, color: palette.sub),
              ),
            ],
          ),
      ],
    );
  }
}

/// Draws diagonal hatch lines inside [rect], the texture for "no data".
void paintHatch(Canvas canvas, Rect rect, Color color, {double gap = 6}) {
  canvas.save();
  canvas.clipRect(rect);
  final paint = Paint()
    ..color = color
    ..strokeWidth = 1;
  for (var x = rect.left - rect.height; x < rect.right; x += gap) {
    canvas.drawLine(
      Offset(x, rect.bottom),
      Offset(x + rect.height, rect.top),
      paint,
    );
  }
  canvas.restore();
}

class _KeyPainter extends CustomPainter {
  _KeyPainter(this.mark, this.palette);

  final KeyMark mark;
  final Palette palette;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final fill = Paint()..color = palette.chart;
    final stroke = Paint()
      ..color = palette.chart
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    switch (mark) {
      case KeyMark.dot:
        canvas.drawCircle(c, 4.5, fill);
      case KeyMark.ring:
        canvas.drawCircle(c, 4, stroke);
      case KeyMark.line:
        canvas.drawLine(
          Offset(0, c.dy),
          Offset(size.width, c.dy),
          stroke..strokeWidth = 3,
        );
      case KeyMark.dash:
        final avg = Paint()
          ..color = palette.avg
          ..strokeWidth = 2.5;
        canvas.drawLine(Offset(0, c.dy), Offset(7, c.dy), avg);
        canvas.drawLine(Offset(12, c.dy), Offset(size.width, c.dy), avg);
      case KeyMark.band:
        canvas.drawRect(
          Rect.fromLTWH(0, 2, size.width, size.height - 4),
          Paint()..color = palette.chart.withValues(alpha: 0.2),
        );
      case KeyMark.hatch:
        final box = Rect.fromLTWH(0, 1, size.width, size.height - 2);
        paintHatch(canvas, box, palette.sub, gap: 5);
        canvas.drawRect(
          box,
          Paint()
            ..color = palette.sub
            ..style = PaintingStyle.stroke,
        );
      case KeyMark.tick:
        canvas.drawRect(
          Rect.fromCenter(center: c, width: 5, height: size.height - 2),
          fill,
        );
      case KeyMark.tickRing:
        canvas.drawRect(
          Rect.fromCenter(center: c, width: 5, height: size.height - 2),
          stroke..strokeWidth = 1.5,
        );
    }
  }

  @override
  bool shouldRepaint(_KeyPainter old) =>
      old.mark != mark || old.palette != palette;
}
