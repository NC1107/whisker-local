import 'package:flutter/material.dart';

import '../../theme.dart';

/// The one word that stands for a litter level.
String litterWord(int? percent, {int lowAt = 30}) {
  if (percent == null) return 'Unknown';
  if (percent < 15) return 'Empty';
  if (percent < lowAt) return 'Low';
  if (percent > 90) return 'Full';
  return 'Optimal';
}

class _GaugeColumn extends StatelessWidget {
  const _GaugeColumn({
    required this.caption,
    required this.value,
    required this.semanticLabel,
    required this.large,
    required this.shape,
  });

  final String caption;
  final String value;
  final String semanticLabel;
  final bool large;
  final Widget shape;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      label: semanticLabel,
      child: ExcludeSemantics(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              caption,
              style: TextStyle(fontSize: large ? 15 : 14, color: palette.sub),
            ),
            const SizedBox(height: 4),
            shape,
            const SizedBox(height: 4),
            Text(
              value,
              style: TextStyle(
                fontSize: large ? 16 : 15,
                fontWeight: FontWeight.w700,
                color: palette.ink,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// The waste drawer as a rounded box that fills from the bottom.
/// [percent] is null while the robot has not reported it.
class WasteGauge extends StatelessWidget {
  const WasteGauge({
    this.percent,
    this.full = false,
    this.estimate = false,
    this.large = false,
    super.key,
  });

  final int? percent;

  /// A full drawer fills the box in the warning color and reads "Full".
  final bool full;

  /// After a Reset the reading is a guess until the next cycle measures it.
  final bool estimate;

  /// The bigger size used on a wide window.
  final bool large;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final level = full ? 100 : (percent ?? 0).clamp(0, 100);
    final text = full
        ? 'Full'
        : percent == null
        ? 'Unknown'
        : '${estimate ? 'About ' : ''}$percent%';
    return _GaugeColumn(
      caption: 'Waste',
      value: text,
      large: large,
      semanticLabel: full
          ? 'Waste drawer full'
          : percent == null
          ? 'Waste drawer level unknown'
          : 'Waste drawer ${estimate ? 'about ' : ''}$percent percent full',
      shape: ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: Container(
          width: large ? 60 : 52,
          height: large ? 50 : 44,
          color: palette.track,
          alignment: Alignment.bottomCenter,
          child: FractionallySizedBox(
            heightFactor: level / 100,
            widthFactor: 1,
            child: ColoredBox(color: full ? palette.warn : palette.fill),
          ),
        ),
      ),
    );
  }
}

/// The litter globe as a circle that fills from the bottom, with a dashed
/// line halfway up. Under [lowAt] percent it fills in the warning color.
class LitterGauge extends StatelessWidget {
  const LitterGauge({
    this.percent,
    this.lowAt = 30,
    this.large = false,
    this.estimate = false,
    super.key,
  });

  final int? percent;
  final int lowAt;
  final bool large;

  /// Until the full and empty points are set, the robot has only learned
  /// where full is from what it has seen, so the level is a guess.
  final bool estimate;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final word = litterWord(percent, lowAt: lowAt);
    final isLow = percent != null && percent! < lowAt;
    final size = large ? 50.0 : 44.0;
    final guess = estimate && percent != null;
    return _GaugeColumn(
      caption: guess ? 'Litter (est.)' : 'Litter',
      value: word,
      large: large,
      semanticLabel:
          'Litter level ${word.toLowerCase()}${guess ? ', an estimate' : ''}',
      shape: ClipOval(
        child: SizedBox(
          width: size,
          height: size,
          child: Stack(
            children: [
              Positioned.fill(child: ColoredBox(color: palette.track)),
              Align(
                alignment: Alignment.bottomCenter,
                child: FractionallySizedBox(
                  heightFactor: (percent ?? 0).clamp(0, 100) / 100,
                  widthFactor: 1,
                  child: ColoredBox(color: isLow ? palette.warn : palette.fill),
                ),
              ),
              Positioned.fill(
                child: CustomPaint(painter: _DashedMidline(palette.card)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DashedMidline extends CustomPainter {
  _DashedMidline(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 2;
    final y = size.height / 2;
    for (var x = 2.0; x < size.width - 2; x += 6) {
      canvas.drawLine(
        Offset(x, y),
        Offset((x + 3).clamp(0, size.width - 2), y),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(_DashedMidline old) => old.color != color;
}
