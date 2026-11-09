import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/charts/bar_chart.dart';
import '../widgets/charts/chart_chrome.dart';
import '../widgets/charts/insight_text.dart';
import '../widgets/charts/latest_request.dart';
import '../widgets/common/common.dart';
import '../widgets/health/chart_keys.dart';
import '../widgets/health/rhythm_charts.dart';
import '../widgets/health/weight_dots_chart.dart';
import '../widgets/health/worth_a_look_card.dart';
import 'data_quality_screen.dart';

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

/// The days the dots and the rhythm cover for the period shown. Months in the
/// future are cut at today, and a very long history at the longest span the
/// server answers in one request.
(DateTime start, DateTime end) _detailSpan(CatInsights shown) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final end = shown.end.isAfter(today) ? today : shown.end;
  final floor = end.subtract(const Duration(days: 3659));
  return (shown.start.isBefore(floor) ? floor : shown.start, end);
}

class _CatInsightsScreenState extends State<CatInsightsScreen> {
  final _weight = LatestRequest<CatInsights>();
  final _week = LatestRequest<CatInsights>();
  final _look = LatestRequest<WorthALook>();
  final _chart = LatestRequest<WeightChart>();
  final _rhythm = LatestRequest<Rhythm>();
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
    _loadLook();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_refreshOnNewVisit);
    _weight.dispose();
    _week.dispose();
    _look.dispose();
    _chart.dispose();
    _rhythm.dispose();
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
    _loadLook(quiet: true);
  }

  Future<void> _loadWeight({bool quiet = false}) async {
    await _weight.run(
      () => widget.controller.api.catInsights(
        widget.catId,
        period: _period,
        offset: _offset,
      ),
      quiet: quiet,
    );
    await _loadDetail(quiet: quiet);
  }

  Future<void> _loadLook({bool quiet = false}) => _look.run(
    () => widget.controller.api.worthALook(catId: widget.catId),
    quiet: quiet,
  );

  /// The dots and the daily rhythm cover the same days as the period shown.
  /// Months far in the future are cut at today, and a very long history at the
  /// longest span the server answers in one request.
  Future<void> _loadDetail({bool quiet = false}) {
    final shown = _weight.value;
    if (shown == null || shown.period != _period || shown.offset != _offset) {
      return Future.value();
    }
    final (start, end) = _detailSpan(shown);
    final api = widget.controller.api;
    return Future.wait([
      _chart.run(
        () => api.weightChart(widget.catId, start: start, end: end),
        quiet: quiet,
      ),
      _rhythm.run(
        () => api.rhythm(catId: widget.catId, start: start, end: end),
        quiet: quiet,
      ),
    ]);
  }

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

  Future<void> _refresh() =>
      Future.wait([_loadWeight(), _loadWeek(), _loadLook()]);

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
              listenable: Listenable.merge([
                widget.controller,
                _weight,
                _week,
                _look,
                _chart,
                _rhythm,
              ]),
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
      WorthALookCard(look: _look.value, error: _look.error, onRetry: _loadLook),
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
        chart: _chart,
        onOpenQuality: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => DataQualityScreen(controller: widget.controller),
          ),
        ),
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
                value: summary.yesterdayVisits?.toString() ?? '-',
                label: "Yesterday's visits",
                valueSize: 26,
              ),
              StatTile(
                value:
                    (week?.overview.weekVisits ?? summary.weekVisits)
                        ?.toString() ??
                    '-',
                label: 'Visits this week',
                valueSize: 26,
              ),
              StatTile(
                value: minutesText(summary.yesterdayMinutes),
                unit: summary.yesterdayMinutes == null ? null : 'min',
                label: 'Time in box',
                valueSize: 26,
              ),
              StatTile(
                value: common?.time ?? '-',
                unit: common?.period,
                label: 'Usual visit time',
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
      _RhythmCard(
        request: _rhythm,
        waiting: _weight.loading,
        period: _period,
        onRetry: _loadDetail,
      ),
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
        message: 'This cat is no longer listed',
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
              "$title didn't load: ${request.error}",
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
              "Summary didn't load: ${request.error}",
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
    required this.chart,
    required this.onOpenQuality,
    required this.period,
    required this.offset,
    required this.onChange,
    required this.onRetry,
  });

  final LatestRequest<CatInsights> request;
  final LatestRequest<WeightChart> chart;
  final VoidCallback onOpenQuality;
  final InsightPeriod period;
  final int offset;
  final void Function(InsightPeriod period, int offset) onChange;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
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
            ..._chart(context, shown)
          else if (request.error != null)
            _Placeholder.failed(
              "Weight didn't load: ${request.error}",
              onRetry: onRetry,
              height: 240,
            )
          else
            const _Placeholder.loading(height: 240),
        ],
      ),
    );
  }

  List<Widget> _chart(BuildContext context, CatInsights data) {
    final palette = context.palette;
    final detail = chart.value;
    final (start, end) = _detailSpan(data);
    final fresh = detail != null && detail.start == start && detail.end == end;
    if (!fresh) {
      return [
        if (chart.error != null && !chart.loading)
          _Placeholder.failed(
            "Weight didn't load: ${chart.error}",
            onRetry: onRetry,
            height: 240,
          )
        else
          const _Placeholder.loading(height: 240),
      ];
    }
    if (detail.readings.isEmpty) {
      return [const _Placeholder.empty('No weighed visits', height: 240)];
    }
    final notes = <String>[
      ?_changeSentence(detail),
      ?detail.statusText,
      'Measured by the litter box scale, which is off by about '
          '${detail.noisePct == null ? '3 to 4' : detail.noisePct!.round()} '
          'percent on each reading. Not a diagnosis.',
    ];
    return [
      WeightDotsChart(chart: detail),
      const SizedBox(height: 10),
      for (final note in notes)
        Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Text(
            note,
            style: TextStyle(fontSize: 14, height: 1.4, color: palette.sub),
          ),
        ),
      Align(
        alignment: Alignment.centerLeft,
        child: TextButton(
          key: const ValueKey('open-data-quality'),
          onPressed: onOpenQuality,
          child: const Text('Which days can I trust?'),
        ),
      ),
    ];
  }

  /// A whole percent, never "-0".
  static String _whole(double value) {
    final rounded = value.round();
    return rounded == 0 ? '0' : rounded.toString();
  }

  static String? _changeSentence(WeightChart detail) {
    final pct = detail.changePct;
    final low = detail.rangeLowPct;
    final high = detail.rangeHighPct;
    if (pct == null || low == null || high == null || detail.anchorLb == null) {
      return null;
    }
    final range =
        '(range ${_whole(low)} to ${_whole(high)} '
        'percent)';
    final usual = 'the usual ${weightValue(detail.anchorLb)} lb';
    if (low <= 0 && high >= 0) {
      return 'The last two weeks are within the normal scatter of $usual '
          '$range.';
    }
    final direction = pct < 0 ? 'lower' : 'higher';
    return 'The last two weeks are about ${pct.abs().toStringAsFixed(0)} '
        'percent $direction than $usual $range.';
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
        return const _Placeholder.empty('No visits this week', height: 176);
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
        return const _Placeholder.empty('No visits this week', height: 176);
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
            semanticLabel: 'Minutes in the box on each day this week',
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

class _RhythmCard extends StatelessWidget {
  const _RhythmCard({
    required this.request,
    required this.waiting,
    required this.period,
    required this.onRetry,
  });

  final LatestRequest<Rhythm> request;

  /// True while the period it depends on is still being asked for.
  final bool waiting;
  final InsightPeriod period;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final data = request.value;
    return SurfaceCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'Daily rhythm',
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 12),
          if (data == null && !request.loading && !waiting)
            _Placeholder.failed(
              "Rhythm didn't load: ${request.error ?? 'no answer'}",
              onRetry: onRetry,
              height: 120,
            )
          else if (data == null)
            const _Placeholder.loading(height: 120)
          else if (data.actogram.every((d) => d.visits.isEmpty))
            const _Placeholder.empty('No visits in this period', height: 120)
          else
            ..._body(context, data),
        ],
      ),
    );
  }

  List<Widget> _body(BuildContext context, Rhythm data) {
    final palette = context.palette;
    final shownDays = data.actogram.length;
    final waiting = data.heatmapNeedDays <= 14
        ? 'about ${data.heatmapNeedDays} more days'
        : 'about ${(data.heatmapNeedDays / 7).ceil()} more weeks';
    return [
      Text(
        shownDays >= 90
            ? 'The newest 90 days of this period, a row for each day.'
            : 'A row for each day, midnight to midnight.',
        style: TextStyle(fontSize: 14, color: palette.sub),
      ),
      const SizedBox(height: 8),
      Actogram(
        days: data.actogram,
        lanes: 1,
        laneOf: (_) => 0,
        nameOf: (_) => 'this cat',
      ),
      const SizedBox(height: 8),
      const MarkLegend(
        items: [
          KeyItem('Visit', KeyMark.tick),
          KeyItem('Not matched to a cat', KeyMark.tickRing),
          KeyItem('No data', KeyMark.hatch),
        ],
      ),
      const SizedBox(height: 18),
      Text(
        'Time of day',
        style: TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w700,
          color: palette.ink,
        ),
      ),
      const SizedBox(height: 4),
      if (data.recentVisits < 12)
        Text(
          'Needs a few more weeks of visits to show a pattern.',
          style: TextStyle(fontSize: 14, color: palette.sub),
        )
      else
        HourProfile(blocks: data.blocks),
      const SizedBox(height: 18),
      Text(
        'Weekday and time of day',
        style: TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w700,
          color: palette.ink,
        ),
      ),
      const SizedBox(height: 4),
      if (data.heatmap == null)
        Text(
          'Shown once there are about eight weeks of visits: $waiting.',
          key: const ValueKey('heatmap-waiting'),
          style: TextStyle(fontSize: 14, color: palette.sub),
        )
      else
        WeekdayHeatmap(rows: data.heatmap!),
    ];
  }
}
