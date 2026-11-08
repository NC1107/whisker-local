import 'package:flutter/material.dart';

import '../../theme.dart';

/// A rounded white (dark green in dark mode) card with the design's soft shadow.
/// Pass [onTap] to make the whole card a button.
class SurfaceCard extends StatelessWidget {
  const SurfaceCard({
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.radius = 20,
    this.color,
    this.shadow = true,
    this.onTap,
    this.semanticLabel,
    super.key,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double radius;

  /// Defaults to the card color. Use `palette.soft` for a tinted card.
  final Color? color;
  final bool shadow;
  final VoidCallback? onTap;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final shape = BorderRadius.circular(radius);
    Widget content = Padding(padding: padding, child: child);
    if (onTap != null) {
      content = InkWell(onTap: onTap, borderRadius: shape, child: content);
    }
    final card = DecoratedBox(
      decoration: BoxDecoration(
        color: color ?? palette.card,
        borderRadius: shape,
        boxShadow: shadow ? palette.shadow : null,
      ),
      child: Material(
        type: MaterialType.transparency,
        borderRadius: shape,
        clipBehavior: Clip.antiAlias,
        child: content,
      ),
    );
    if (semanticLabel == null) return card;
    return Semantics(
      container: true,
      button: onTap != null,
      label: semanticLabel,
      child: ExcludeSemantics(child: card),
    );
  }
}
