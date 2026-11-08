import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/charts/bar_chart.dart';
import '../widgets/charts/chart_chrome.dart';
import '../widgets/charts/insight_text.dart';
import '../widgets/charts/latest_request.dart';
import '../widgets/charts/line_chart.dart';
import '../widgets/charts/time_grid.dart';
import '../widgets/common/common.dart';

/// Charts and numbers for one cat: weight over a period you can step through,
/// and this week's visits.
class CatInsightsScreen extends StatefulWidget {
  const CatInsightsScreen({
    required this.controller,
    required this.catId,
    super.key,
  });

  final AppController controller;
  final int catId;

  @override
  State<CatInsightsScreen> createState() => _CatInsightsScreenState();
}

class _CatInsightsScreenState extends State<CatInsightsScreen> {
  final _weight = LatestRequest<CatInsights>();
  final _week = LatestRequest<CatInsights>();
  var _period = InsightPeriod.week;
  var _offset = 0;
  Object? _lastVisitMark;

  Cat? get _cat {
    for (final cat in widget.controller.cats) {
      if (cat.id == widget.catId) return cat;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _lastVisitMark = _visitMark();
    widget.controller.addListener(_refreshOnNewVisit);
    _loadWeight();
    _loadWeek();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_refreshOnNewVisit);
    _weight.dispose();
    _week.dispose();
    super.dispose();
  }

  Object? _visitMark() {
    final summary = _cat?.summary;
    return summary == null
        ? null
        : (summary.lastVisitAt, summary.weekVisits, summary.latestWeightLb);
  }

  void _refreshOnNewVisit() {
    final mark = _visitMark();
    if (mark == _lastVisitMark) return;
    _lastVisitMark = mark;
    _loadWeight(quiet: true);
    _loadWeek(quiet: true);
  }

  Future<void> _loadWeight({bool quiet = false}) => _weight.run(
    () => widget.controller.api.catInsights(
      widget.catId,
      period: _period,
      offset: _offset,
    ),
    quiet: quiet,
  );

  Future<void> _loadWeek({bool quiet = false}) => _week.run(
    () => widget.controller.api.catInsights(widget.catId),
    quiet: quiet,
  );

  void _show(InsightPeriod period, int offset) {
    setState(() {
      _period = period;
      _offset = offset;
    });
    _loadWeight();
  }

  Future<void> _refresh() => Future.wait([_loadWeight(), _loadWeek()]);

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Scaffold(
      backgroundColor: palette.bg,
      body: Column(
        children: [
          ListenableBuilder(
            listenable: widget.controller,
            builder: (context, _) => TopBar.back(
              title: _cat == null
                  ? 'Insights'
                  : "${possessive(_cat!.name)} insights",
              onBack: () => Navigator.of(context).pop(),
            ),
          ),
          Expanded(
            child: ListenableBuilder(
              listenable: Listenable.merge([widget.controller, _weight, _week]),
              builder: (context, _) {
                final cat = _cat;
                if (cat == null) return const _CatGone();
                return PageBody(
                  onRefresh: _refresh,
                  children: _sections(context, cat),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _sections(BuildContext context, Cat cat) {
    final wide = isWide(context);
    final summary = cat.summary;
    final week = _week.value;
    final common = hourOfDay(summary.commonVisitHour);

    final visitsCard = _VisitsPerDayCard(request: _week, onRetry: _loadWeek);
    final minutesCard = _MinutesCard(request: _week, onRetry: _loadWeek);

    return [
      _SummaryCard(request: _week, onRetry: _loadWeek),
      const _Heading('Weight'),
      SurfaceCard(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        child: StatTile(
          value: weightValue(summary.yesterdayWeightLb),
          unit: summary.yesterdayWeightLb == null ? null : 'lb',
          label: "Yesterday's weight",
          valueSize: 26,
          labelSize: 16,
        ),
      ),
      _WeightCard(
        request: _weight,
        period: _period,
        offset: _offset,
        onChange: _show,
        onRetry: _loadWeight,
      ),
      const _Heading('Visits'),
      SurfaceCard(
        padding: const EdgeInsets.all(18),
        child: LayoutBuilder(
          builder: (context, constraints) {
            final tiles = [
              StatTile(
                value: '${summary.yesterdayVisits}',
                label: "Yesterday's visits",
                valueSize: 26,
              ),
              StatTile(
                value: '${week?.overview.weekVisits ?? summary.weekVisits}',
                label: 'Visits this week',
                valueSize: 26,
              ),
              StatTile(
                value: minutesText(summary.yesterdayMinutes),
                unit: summary.yesterdayMinutes == null ? null : 'min',
                label: "Yesterday's time in the box",
                valueSize: 26,
              ),
              StatTile(
                value: common?.time ?? '-',
                unit: common?.period,
                label: 'Most common visit time',
                valueSize: 26,
              ),
            ];
            final columns = wide ? 4 : 2;
            final gap = 12.0;
            final width =
                (constraints.maxWidth - gap * (columns - 1)) / columns;
            return Wrap(
              spacing: gap,
              runSpacing: 16,
              children: [
                for (final tile in tiles) SizedBox(width: width, child: tile),
              ],
            );
          },
        ),
      ),
      if (wide)
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: visitsCard),
            const SizedBox(width: 14),
            Expanded(child: minutesCard),
          ],
        )
      else ...[
        visitsCard,
      ],
      _TimeOfVisitCard(request: _week, onRetry: _loadWeek),
      if (!wide) minutesCard,
    ];
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.title);

  final String title;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 6, bottom: 0),
    child: Semantics(
      header: true,
      child: Text(
        title,
        style: TextStyle(
          fontSize: 22,
          fontWeight: FontWeight.w700,
          color: context.palette.ink,
        ),
      ),
    ),
  );
}

class _CatGone extends StatelessWidget {
  const _CatGone();

  @override
  Widget build(BuildContext context) => PageBody(
    children: [
      NoticeCard(
        message: 'This cat is no longer in your list.',
        actionLabel: 'Back',
        onTap: () => Navigator.of(context).pop(),
      ),
    ],
  );
}

/// What to show in a card in place of a chart.
class _Placeholder extends StatelessWidget {
  const _Placeholder.loading({required this.height})
    : message = null,
      onRetry = null;
  const _Placeholder.empty(this.message, {required this.height})
    : onRetry = null;
  const _Placeholder.failed(
    this.message, {
    required this.onRetry,
    required this.height,
  });

  final double height;
  final String? message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    if (message == null) {
      return SizedBox(
        height: height,
        child: const Center(
          child: SizedBox.square(
            dimension: 28,
            child: CircularProgressIndicator(strokeWidth: 3),
          ),
        ),
      );
    }
    if (onRetry != null) {
      return NoticeCard(
        tone: NoticeTone.warning,
        message: message!,
        actionLabel: 'Try again',
        onTap: onRetry,
      );
    }
    return SizedBox(
      height: height,
      child: Center(
        child: Text(
          message!,
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 16, height: 1.4, color: palette.sub),
        ),
      ),
    );
  }
}

/// A card whose body depends on one request: its answer, a failure with a
/// retry, or a spinner while the first answer is on its way.
class _RequestCard extends StatelessWidget {
  const _RequestCard({
    required this.request,
    required this.onRetry,
    required this.title,
    required this.builder,
    required this.height,
  });

  final LatestRequest<CatInsights> request;
  final VoidCallback onRetry;
  final String title;
  final double height;
  final Widget Function(CatInsights data) builder;

  @override
  Widget build(BuildContext context) {
    final data = request.value;
    return SurfaceCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            title,
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w700,
              color: context.palette.ink,
            ),
          ),
          const SizedBox(height: 12),
          if (data != null)
            builder(data)
          else if (request.error != null)
            _Placeholder.failed(
              "$title didn't load. ${request.error}",
              onRetry: onRetry,
              height: height,
            )
          else
            _Placeholder.loading(height: height),
        ],
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.request, required this.onRetry});

  final LatestRequest<CatInsights> request;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final data = request.value;
    return SurfaceCard(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Semantics(
            header: true,
            child: Text(
              'Summary',
              style: TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.w700,
                color: palette.ink,
              ),
            ),
          ),
          const SizedBox(height: 10),
          if (data != null)
            for (var i = 0; i < data.summary.length; i++)
              Padding(
                padding: EdgeInsets.only(top: i == 0 ? 0 : 8),
                child: Text(
                  data.summary[i],
                  style: TextStyle(
                    fontSize: 16,
                    height: 1.4,
                    color: palette.sub,
                  ),
                ),
              )
          else if (request.error != null)
            _Placeholder.failed(
              "The summary didn't load. ${request.error}",
              onRetry: onRetry,
              height: 60,
            )
          else
            const _Placeholder.loading(height: 44),
        ],
      ),
    );
  }
}

class _WeightCard extends StatelessWidget {
  const _WeightCard({
    required this.request,
    required this.period,
    required this.offset,
    required this.onChange,
    required this.onRetry,
  });

  final LatestRequest<CatInsights> request;
  final InsightPeriod period;
  final int offset;
  final void Function(InsightPeriod period, int offset) onChange;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final data = request.value;
    final current =
        data != null && data.period == period && data.offset == offset;
    final shown = current ? data : null;
    return SurfaceCard(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          PeriodTabs(
            value: period,
            onChanged: (next) => next == period ? null : onChange(next, 0),
            semanticLabel: 'Weight period',
          ),
          const SizedBox(height: 10),
          RangeNavigator(
            label: shown?.rangeLabel ?? '',
            onEarlier: shown != null && shown.hasEarlier
                ? () => onChange(period, offset + 1)
                : null,
            onLater: offset > 0 && !request.loading
                ? () => onChange(period, offset - 1)
                : null,
          ),
          const SizedBox(height: 4),
          if (shown != null)
            ..._chart(shown, palette)
          else if (request.error != null)
            _Placeholder.failed(
              "Weight didn't load. ${request.error}",
              onRetry: onRetry,
              height: 206,
            )
          else
            const _Placeholder.loading(height: 206),
        ],
      ),
    );
  }

  List<Widget> _chart(CatInsights data, Palette palette) {
    final weighed = data.weight.values.whereType<double>().isNotEmpty;
    if (!weighed) {
      return [
        const _Placeholder.empty(
          'No weighed visits in this period.',
          height: 206,
        ),
      ];
    }
    final points = data.points;
    return [
      LineChart(
        values: data.weight.values,
        average: data.weight.average,
        xLabels: [for (final day in points) axisDate(day, data.period)],
        tooltips: [
          for (var i = 0; i < points.length; i++)
            data.weight.values[i] == null
                ? null
                : '${pointTooltipDate(points[i], data.period)}: ${weightValue(data.weight.values[i])} lb',
        ],
        semanticLabel:
            'Weight over ${data.rangeLabel}, averaging ${weightValue(data.weight.average)} pounds.',
      ),
      const SizedBox(height: 8),
      const ChartLegend(
        items: [
          LegendItem('Weight', LegendMark.dot),
          LegendItem('Average', LegendMark.dash),
        ],
      ),
    ];
  }
}

class _VisitsPerDayCard extends StatelessWidget {
  const _VisitsPerDayCard({required this.request, required this.onRetry});

  final LatestRequest<CatInsights> request;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => _RequestCard(
    request: request,
    onRetry: onRetry,
    title: 'Visits per day',
    height: 176,
    builder: (data) {
      final total = data.visits.values.whereType<double>().fold(
        0.0,
        (a, b) => a + b,
      );
      if (data.visits.values.every((v) => v == null)) {
        return const _Placeholder.empty('No visits this week.', height: 176);
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          BarChart(
            values: data.visits.values,
            average: data.visits.average,
            xLabels: [for (final day in data.points) weekday(day)],
            tooltips: [
              for (var i = 0; i < data.points.length; i++)
                data.visits.values[i] == null
                    ? null
                    : '${pointTooltipDate(data.points[i], data.period)}: ${visitsText(data.visits.values[i]!)}',
            ],
            semanticLabel:
                'Visits on each day this week, ${trimmed(total)} in total.',
          ),
          const SizedBox(height: 8),
          const ChartLegend(
            items: [
              LegendItem('Visits', LegendMark.dot),
              LegendItem('Average', LegendMark.dash),
            ],
          ),
        ],
      );
    },
  );
}

class _MinutesCard extends StatelessWidget {
  const _MinutesCard({required this.request, required this.onRetry});

  final LatestRequest<CatInsights> request;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => _RequestCard(
    request: request,
    onRetry: onRetry,
    title: 'Time in the box',
    height: 176,
    builder: (data) {
      if (data.minutes.values.every((v) => v == null)) {
        return const _Placeholder.empty('No visits this week.', height: 176);
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          BarChart(
            values: data.minutes.values,
            average: data.minutes.average,
            xLabels: [for (final day in data.points) weekday(day)],
            tooltips: [
              for (var i = 0; i < data.points.length; i++)
                data.minutes.values[i] == null
                    ? null
                    : '${pointTooltipDate(data.points[i], data.period)}: ${trimmed(data.minutes.values[i]!)} min',
            ],
            semanticLabel: 'Minutes in the box on each day this week.',
          ),
          const SizedBox(height: 8),
          const ChartLegend(
            items: [
              LegendItem('Minutes', LegendMark.dot),
              LegendItem('Average', LegendMark.dash),
            ],
          ),
        ],
      );
    },
  );
}

class _TimeOfVisitCard extends StatelessWidget {
  const _TimeOfVisitCard({required this.request, required this.onRetry});

  final LatestRequest<CatInsights> request;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return _RequestCard(
      request: request,
      onRetry: onRetry,
      title: 'Time of visit',
      height: 246,
      builder: (data) {
        if (data.timeOfVisit.isEmpty) {
          return const _Placeholder.empty('No visits this week.', height: 246);
        }
        final dots = [
          for (final visit in data.timeOfVisit)
            if (visit.day >= 0 && visit.day < data.points.length)
              TimeDot(
                column: visit.day,
                hour: visit.hour,
                durationS: visit.durationS,
                tooltip:
                    '${weekday(data.points[visit.day])} ${clockText(visit.hour)}, ${durationText(visit.durationS)}',
              ),
        ];
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TimeGrid(
              columns: data.points.length,
              dots: dots,
              xLabels: [for (final day in data.points) weekday(day)],
              semanticLabel:
                  'Visits by day and time of day, ${dots.length} in total.',
            ),
            const SizedBox(height: 8),
            Text(
              'Bigger dots are longer visits.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 15, color: palette.sub),
            ),
          ],
        );
      },
    );
  }
}
