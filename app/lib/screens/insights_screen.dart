import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../shell.dart';
import '../theme.dart';
import '../widgets/charts/insight_text.dart';
import '../widgets/common/common.dart';
import 'cat_insights_screen.dart';
import 'household_health.dart';

/// Yesterday's numbers for each cat, and the way into one cat's charts.
class InsightsScreen extends StatelessWidget {
  const InsightsScreen({required this.controller, super.key});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final status = controller.status;
        final cats = controller.cats;
        return PageBody(
          children: [
            if (isWide(context)) const SectionHeader('Insights'),
            if (controller.unreachable)
              const NoticeCard(
                tone: NoticeTone.warning,
                message: "Can't reach the server, showing old numbers",
              ),
            if (status != null && status.robot == null && cats.isNotEmpty)
              const NoticeCard(message: 'Numbers appear after the first visit'),
            if (status != null && cats.isEmpty)
              const _NoCats()
            else if (cats.isNotEmpty) ...[
              HouseholdHealth(controller: controller),
              _CatGrid(
                cats: cats,
                onOpen: (cat) => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => CatInsightsScreen(
                      controller: controller,
                      catId: cat.id,
                    ),
                  ),
                ),
              ),
            ],
          ],
        );
      },
    );
  }
}

class _NoCats extends StatelessWidget {
  const _NoCats();

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return SurfaceCard(
      key: const ValueKey('insights-no-cats'),
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'No cats yet',
            style: TextStyle(
              fontSize: 20,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            'Add a cat to see weight and visits',
            style: TextStyle(fontSize: 16, height: 1.4, color: palette.sub),
          ),
          const SizedBox(height: 14),
          FilledButton(
            onPressed: () => ShellScope.of(context).select(AppTab.cats),
            child: const Text('Add a cat'),
          ),
        ],
      ),
    );
  }
}

class _CatGrid extends StatelessWidget {
  const _CatGrid({required this.cats, required this.onOpen});

  final List<Cat> cats;
  final ValueChanged<Cat> onOpen;

  @override
  Widget build(BuildContext context) {
    if (!isWide(context)) {
      return Column(
        children: [
          for (var i = 0; i < cats.length; i++) ...[
            if (i > 0) const SizedBox(height: 14),
            _CatCard(cat: cats[i], onOpen: () => onOpen(cats[i])),
          ],
        ],
      );
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        const spacing = 16.0;
        final width = (constraints.maxWidth - spacing) / 2;
        return Wrap(
          spacing: spacing,
          runSpacing: spacing,
          children: [
            for (final cat in cats)
              SizedBox(
                width: width,
                child: _CatCard(cat: cat, onOpen: () => onOpen(cat)),
              ),
          ],
        );
      },
    );
  }
}

class _CatCard extends StatelessWidget {
  const _CatCard({required this.cat, required this.onOpen});

  final Cat cat;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final summary = cat.summary;
    final common = hourOfDay(summary.commonVisitHour);
    final tiles = [
      StatTile(
        value: weightValue(summary.yesterdayWeightLb),
        unit: summary.yesterdayWeightLb == null ? null : 'lb',
        label: "Yesterday's weight",
      ),
      StatTile(
        value: summary.yesterdayVisits?.toString() ?? '-',
        label: "Yesterday's visits",
      ),
      StatTile(
        value: minutesText(summary.yesterdayMinutes),
        unit: summary.yesterdayMinutes == null ? null : 'min',
        label: 'Time in box',
      ),
      StatTile(
        value: common?.time ?? '-',
        unit: common?.period,
        label: 'Usual visit time',
      ),
    ];
    final visits = summary.yesterdayVisits;
    final label = [
      cat.name,
      if (summary.yesterdayWeightLb != null)
        'yesterday ${weightText(summary.yesterdayWeightLb!)}',
      if (visits == null)
        'no data from the robot yesterday'
      else
        '$visits ${visits == 1 ? 'visit' : 'visits'} yesterday',
      if (summary.yesterdayMinutes != null)
        summary.yesterdayMinutes! < 0.5
            ? 'under a minute in the box'
            : '${minutesText(summary.yesterdayMinutes)} minutes in the box',
      if (common != null)
        'most common visit time ${common.time} ${common.period}',
    ].join(', ');
    return SurfaceCard(
      key: ValueKey('insights-cat-${cat.id}'),
      onTap: onOpen,
      semanticLabel: '$label. Open insights',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CatAvatar(cat: cat),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  cat.name,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                    color: palette.ink,
                  ),
                ),
              ),
              AppIcon(AppIcons.chevronRight, size: 22, color: palette.sub),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: tiles[0]),
              const SizedBox(width: 12),
              Expanded(child: tiles[1]),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: tiles[2]),
              const SizedBox(width: 12),
              Expanded(child: tiles[3]),
            ],
          ),
        ],
      ),
    );
  }
}
