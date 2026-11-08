import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

/// The robot's globe: real litter depth inside, turning only while the robot cycles.
class Globe extends StatefulWidget {
  const Globe({
    required this.statusLabel,
    required this.detail,
    required this.litterPercent,
    required this.turning,
    super.key,
  });

  final String statusLabel;
  final String detail;
  final int? litterPercent;
  final bool turning;

  @override
  State<Globe> createState() => _GlobeState();
}

class _GlobeState extends State<Globe> with SingleTickerProviderStateMixin {
  late final AnimationController _spin = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 7),
  );

  bool get _shouldTurn =>
      widget.turning && !MediaQuery.disableAnimationsOf(context);

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncSpin();
  }

  @override
  void didUpdateWidget(Globe oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncSpin();
  }

  void _syncSpin() {
    if (_shouldTurn && !_spin.isAnimating) {
      _spin.repeat();
    } else if (!_shouldTurn && _spin.isAnimating) {
      _spin.stop();
      _spin.value = 0;
    }
  }

  @override
  void dispose() {
    _spin.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      label: '${widget.statusLabel}. ${widget.detail}.',
      child: AspectRatio(
        aspectRatio: 1,
        child: Stack(
          fit: StackFit.expand,
          children: [
            ExcludeSemantics(
              child: CustomPaint(
                painter: _GlobePainter(
                  spin: _spin,
                  litterPercent: widget.litterPercent,
                  dimLitter: widget.turning,
                  palette: palette,
                ),
              ),
            ),
            // The litter never rises past the lower part of the globe, so this zone stays
            // clear of it and the text keeps its contrast in both themes.
            Align(
              alignment: const Alignment(0, -0.28),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 28),
                child: ExcludeSemantics(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      FittedBox(
                        fit: BoxFit.scaleDown,
                        child: Text(
                          widget.statusLabel,
                          style: TextStyle(
                            fontSize: 34,
                            height: 1.1,
                            fontWeight: FontWeight.w700,
                            color: palette.ink,
                          ),
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        widget.detail,
                        textAlign: TextAlign.center,
                        style: TextStyle(fontSize: 16, color: palette.sub),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _GlobePainter extends CustomPainter {
  _GlobePainter({
    required this.spin,
    required this.litterPercent,
    required this.dimLitter,
    required this.palette,
  }) : super(repaint: spin);

  final Animation<double> spin;
  final int? litterPercent;
  final bool dimLitter;
  final Palette palette;

  static const _fullDepth = 0.42;
  static const _rim = 3.0;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide / 2 - _rim / 2;
    final circle = Rect.fromCircle(center: center, radius: radius);

    canvas.drawCircle(center, radius, Paint()..color = palette.card);

    final percent = litterPercent;
    if (percent != null && percent > 0) {
      final depth = radius * 2 * _fullDepth * (percent.clamp(0, 100) / 100);
      canvas
        ..save()
        ..clipPath(Path()..addOval(circle))
        ..translate(center.dx, center.dy)
        ..rotate(spin.value * 2 * math.pi)
        ..translate(-center.dx, -center.dy)
        ..drawRect(
          Rect.fromLTRB(
            circle.left,
            circle.bottom - depth,
            circle.right,
            circle.bottom,
          ),
          // Dimmed while turning: the litter sweeps through the text zone on its way around.
          Paint()..color = palette.fill.withValues(alpha: dimLitter ? 0.35 : 1),
        )
        ..restore();
    }

    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..color = palette.line
        ..style = PaintingStyle.stroke
        ..strokeWidth = _rim,
    );
  }

  @override
  bool shouldRepaint(_GlobePainter old) =>
      old.litterPercent != litterPercent ||
      old.dimLitter != dimLitter ||
      old.palette != palette;
}
