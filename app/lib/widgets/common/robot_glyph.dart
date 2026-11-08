import 'package:flutter/material.dart';

import '../../theme.dart';

/// The design's drawing of the litter robot: a globe on a base. It is a
/// stand-in for a product photo and takes its colors from the palette.
class RobotGlyph extends StatelessWidget {
  const RobotGlyph({this.width = 112, super.key});

  static const _aspect = 112 / 132;

  final double width;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      image: true,
      label: 'Litter robot',
      child: ExcludeSemantics(
        child: CustomPaint(
          size: Size(width, width / _aspect),
          painter: _GlyphPainter(palette),
        ),
      ),
    );
  }
}

class _GlyphPainter extends CustomPainter {
  _GlyphPainter(this.palette);

  final Palette palette;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / 112, size.height / 132);
    final body = Paint()..color = palette.body;
    final dark = Paint()..color = palette.bodyDark;
    canvas
      ..drawRRect(
        RRect.fromLTRBR(16, 82, 96, 130, const Radius.circular(12)),
        body,
      )
      ..drawRRect(
        RRect.fromLTRBR(30, 100, 82, 114, const Radius.circular(7)),
        dark,
      )
      ..drawCircle(const Offset(56, 52), 50, body)
      ..drawCircle(const Offset(56, 50), 34, dark)
      ..drawPath(
        Path()
          ..moveTo(24, 66)
          ..quadraticBezierTo(56, 92, 88, 66)
          ..lineTo(88, 76)
          ..quadraticBezierTo(56, 100, 24, 76)
          ..close(),
        Paint()..color = palette.card,
      );
  }

  @override
  bool shouldRepaint(_GlyphPainter old) => old.palette != palette;
}
