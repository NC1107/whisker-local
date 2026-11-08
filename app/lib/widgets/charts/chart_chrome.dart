import 'package:flutter/material.dart';

import '../../theme.dart';
import '../common/common.dart';

enum LegendMark { dot, dash }

class LegendItem {
  const LegendItem(this.label, this.mark);

  final String label;
  final LegendMark mark;
}

/// The key under a chart: a dot for the data and a dash for the average.
class ChartLegend extends StatelessWidget {
  const ChartLegend({required this.items, super.key});

  final List<LegendItem> items;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Wrap(
      alignment: WrapAlignment.center,
      spacing: 18,
      runSpacing: 4,
      children: [
        for (final item in items)
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              item.mark == LegendMark.dot
                  ? Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: palette.chart,
                      ),
                    )
                  : Container(
                      width: 16,
                      height: 3,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(2),
                        color: palette.avg,
                      ),
                    ),
              const SizedBox(width: 6),
              Text(
                item.label,
                style: TextStyle(fontSize: 15, color: palette.sub),
              ),
            ],
          ),
      ],
    );
  }
}

/// The range label of a chart with an arrow on each side to step to the
/// period before or after.
class RangeNavigator extends StatelessWidget {
  const RangeNavigator({
    required this.label,
    required this.onEarlier,
    required this.onLater,
    super.key,
  });

  final String label;

  /// Null turns the arrow off.
  final VoidCallback? onEarlier;
  final VoidCallback? onLater;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Row(
      children: [
        _Arrow(
          key: const ValueKey('range-earlier'),
          icon: AppIcons.chevronLeft,
          label: 'Earlier',
          onPressed: onEarlier,
        ),
        Expanded(
          child: Text(
            label,
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 16, color: palette.sub),
          ),
        ),
        _Arrow(
          key: const ValueKey('range-later'),
          icon: AppIcons.chevronRight,
          label: 'Later',
          onPressed: onLater,
        ),
      ],
    );
  }
}

class _Arrow extends StatelessWidget {
  const _Arrow({
    required this.icon,
    required this.label,
    required this.onPressed,
    super.key,
  });

  final String icon;
  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      button: true,
      enabled: onPressed != null,
      label: label,
      child: ExcludeSemantics(
        child: InkResponse(
          onTap: onPressed,
          radius: 24,
          child: SizedBox.square(
            dimension: 44,
            child: Center(
              child: AppIcon(
                icon,
                size: 24,
                color: onPressed == null
                    ? palette.sub.withValues(alpha: 0.35)
                    : palette.ink,
              ),
            ),
          ),
        ),
      ),
    );
  }
}
