import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import '../widgets/common/common.dart';

/// Where a running clean cycle is: four stages, and a percentage only when
/// the server could estimate one from this robot's earlier cycles.
class CycleProgressCard extends StatelessWidget {
  const CycleProgressCard({required this.cycle, super.key});

  final CycleView cycle;

  String get _title =>
      cycle.paused ? (cycle.pauseLabel ?? 'Paused') : 'Cleaning';

  int? get _percent {
    final progress = cycle.progress;
    return progress == null ? null : (progress * 100).round();
  }

  String get _semanticLabel {
    final stage = cycle.stage;
    final parts = [
      _title,
      if (stage != null)
        'stage ${stage + 1} of ${cycle.stages.length}, ${cycle.stages[stage]}',
      if (_percent != null) 'estimated $_percent percent',
    ];
    return parts.join(', ');
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final percent = _percent;
    return SurfaceCard(
      key: const ValueKey('cycle-progress'),
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
      child: Semantics(
        container: true,
        label: _semanticLabel,
        child: ExcludeSemantics(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      _title,
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w700,
                        color: cycle.paused ? palette.warn : palette.ink,
                      ),
                    ),
                  ),
                  if (percent != null)
                    Text(
                      'Estimated $percent%',
                      key: const ValueKey('cycle-estimate'),
                      style: TextStyle(fontSize: 15, color: palette.sub),
                    ),
                ],
              ),
              const SizedBox(height: 12),
              _StageBar(cycle: cycle),
            ],
          ),
        ),
      ),
    );
  }
}

class _StageBar extends StatelessWidget {
  const _StageBar({required this.cycle});

  final CycleView cycle;

  /// How much of stage [index] to fill.
  double _fill(int index) {
    final stage = cycle.stage;
    if (stage == null || index > stage) return 0;
    if (index < stage) return 1;
    final progress = cycle.progress;
    if (progress == null) return 1;
    return (progress * cycle.stages.length - stage).clamp(0.0, 1.0);
  }

  /// Without an estimate the current stage is tinted whole, which says where
  /// the robot is and nothing about how far through it.
  bool _underWayWithoutEstimate(int index) =>
      index == cycle.stage && cycle.progress == null;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final fillColor = cycle.paused ? palette.warn : palette.primary;
    final count = cycle.stages.length;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            for (var i = 0; i < count; i++) ...[
              if (i > 0) const SizedBox(width: 6),
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: SizedBox(
                    height: 8,
                    child: Stack(
                      children: [
                        Positioned.fill(
                          child: ColoredBox(color: palette.track),
                        ),
                        Positioned.fill(
                          child: FractionallySizedBox(
                            alignment: Alignment.centerLeft,
                            widthFactor: _fill(i),
                            child: ColoredBox(
                              color: _underWayWithoutEstimate(i)
                                  ? fillColor.withValues(alpha: 0.4)
                                  : fillColor,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ],
          ],
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            for (var i = 0; i < count; i++) ...[
              if (i > 0) const SizedBox(width: 6),
              Expanded(
                child: Text(
                  cycle.stages[i],
                  textAlign: TextAlign.center,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: i == cycle.stage
                        ? FontWeight.w700
                        : FontWeight.w400,
                    color: i == cycle.stage ? palette.ink : palette.sub,
                  ),
                ),
              ),
            ],
          ],
        ),
      ],
    );
  }
}
