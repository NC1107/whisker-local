import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/charts/latest_request.dart';
import '../widgets/common/common.dart';
import '../widgets/health/chart_keys.dart';
import '../widgets/health/rhythm_charts.dart';
import '../widgets/health/worth_a_look_card.dart';
import 'data_quality_screen.dart';

const _householdDays = 56;

/// The household part of the Insights tab: what is worth a look across all
/// cats, the way into data quality, and when the box is used.
class HouseholdHealth extends StatefulWidget {
  const HouseholdHealth({required this.controller, super.key});

  final AppController controller;

  @override
  State<HouseholdHealth> createState() => _HouseholdHealthState();
}

class _HouseholdHealthState extends State<HouseholdHealth> {
  final _look = LatestRequest<WorthALook>();
  final _rhythm = LatestRequest<Rhythm>();
  Object? _mark;

  @override
  void initState() {
    super.initState();
    _mark = _visitMark();
    widget.controller.addListener(_onController);
    _load();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onController);
    _look.dispose();
    _rhythm.dispose();
    super.dispose();
  }

  Object? _visitMark() =>
      [for (final cat in widget.controller.cats) cat.summary.lastVisitAt]
          .join('|');

  void _onController() {
    final mark = _visitMark();
    if (mark == _mark) return;
    _mark = mark;
    _load(quiet: true);
  }

  void _load({bool quiet = false}) {
    final api = widget.controller.api;
    final now = DateTime.now();
    final end = DateTime(now.year, now.month, now.day);
    _look.run(api.worthALook, quiet: quiet);
    _rhythm.run(
      () => api.rhythm(
        start: end.subtract(const Duration(days: _householdDays - 1)),
        end: end,
      ),
      quiet: quiet,
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return ListenableBuilder(
      listenable: Listenable.merge([_look, _rhythm]),
      builder: (context, _) {
        final cats = widget.controller.cats;
        final rhythm = _rhythm.value;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            WorthALookCard(
              look: _look.value,
              error: _look.error,
              onRetry: _load,
            ),
            const SizedBox(height: 14),
            SurfaceCard(
              key: const ValueKey('data-quality-link'),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) =>
                      DataQualityScreen(controller: widget.controller),
                ),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Data quality',
                          style: TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w700,
                            color: palette.ink,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          'Which days the robot was heard, and which to trust',
                          style: TextStyle(fontSize: 15, color: palette.sub),
                        ),
                      ],
                    ),
                  ),
                  AppIcon(AppIcons.chevronRight, size: 22, color: palette.sub),
                ],
              ),
            ),
            if (rhythm != null && rhythm.actogram.isNotEmpty) ...[
              const SizedBox(height: 14),
              SurfaceCard(
                padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'When the box is used',
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w700,
                        color: palette.ink,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Each cat has its own band in a row. Outlines are visits '
                      'not matched to a cat. Hatched rows had no data.',
                      style: TextStyle(
                        fontSize: 14,
                        height: 1.35,
                        color: palette.sub,
                      ),
                    ),
                    const SizedBox(height: 10),
                    Actogram(
                      days: rhythm.actogram,
                      lanes: cats.length,
                      laneOf: (catId) {
                        final i = cats.indexWhere((c) => c.id == catId);
                        return i < 0 ? null : i;
                      },
                      nameOf: (catId) {
                        for (final c in cats) {
                          if (c.id == catId) return c.name;
                        }
                        return 'a cat';
                      },
                    ),
                    const SizedBox(height: 8),
                    Text(
                      [
                        for (var i = 0; i < cats.length; i++)
                          'Band ${i + 1} (top to bottom): ${cats[i].name}',
                      ].join('. '),
                      style: TextStyle(fontSize: 14, color: palette.sub),
                    ),
                    const SizedBox(height: 8),
                    const MarkLegend(
                      items: [
                        KeyItem('Matched visit', KeyMark.tick),
                        KeyItem('Not matched', KeyMark.tickRing),
                        KeyItem('No data', KeyMark.hatch),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ],
        );
      },
    );
  }
}
