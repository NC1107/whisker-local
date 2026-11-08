import 'package:flutter/material.dart';

import '../../theme.dart';

enum StatusTone { ready, warning, offline, critical }

/// The small colored circle in front of the robot's status text. The text
/// carries the meaning, so the dot is hidden from screen readers.
class StatusDot extends StatelessWidget {
  const StatusDot(this.tone, {this.size = 12, super.key});

  final StatusTone tone;
  final double size;

  static Color colorOf(Palette palette, StatusTone tone) => switch (tone) {
    StatusTone.ready => palette.primary,
    StatusTone.warning => palette.warn,
    StatusTone.offline => palette.sub,
    StatusTone.critical => palette.danger,
  };

  @override
  Widget build(BuildContext context) {
    return ExcludeSemantics(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: colorOf(context.palette, tone),
        ),
      ),
    );
  }
}
