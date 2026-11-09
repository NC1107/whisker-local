import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api.dart';
import '../../theme.dart';
import '../charts/axis.dart';
import '../charts/chart_frame.dart';
import '../charts/insight_text.dart';
import '../charts/paint.dart';
import 'chart_keys.dart';

const _gutter = 54.0;
const _headerHeight = 24.0;
const _hourLabels = ['12 AM', '6 AM', '12 PM', '6 PM', '12 AM'];

/// A row per day and the 24 hours across, with a tick for each visit. Hatched
/// rows are days with no data; ticks drawn as outlines are visits the server
/// could not name with confidence.
///
/// With several [lanes] each row is split so that each cat keeps its own band,
/// which tells cats apart by position as well as by color.
class Actogram extends StatelessWidget {
  const Actogram({
    required this.days,
    required this.laneOf,
    required this.lanes,
    required this.nameOf,
    super.key,
  });

  final List<ActogramDay> days;

  /// The lane a visit is drawn in, or null for a visit with no confident cat.
  final int? Function(int? catId) laneOf;
  final int lanes;
  final String Function(int? catId) nameOf;

  double get _rowHeight => days.length <= 8
      ? 18
      : (days.length <= 31 ? 12 : (days.length <= 60 ? 9 : 5));

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final ticks = <_Tick>[
      for (var row = 0; row < days.length; row++)
        for (final v in days[row].visits) _Tick(row, v, laneOf(v.catId)),
    ];
    final height = _headerHeight + days.length * _rowHeight + 4;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ChartFrame(
          height: height,
          data: [days.length, ticks.length, lanes, days.firstOrNull?.date],
          semanticLabel:
              'Visits by day and time of day over ${days.length} days, ${ticks.length} in all.',
          pick: (position, size) => _pick(position, size, ticks),
          paint: (selected) => _ActogramPainter(
            palette: palette,
            days: days,
            ticks: ticks,
            lanes: lanes,
            rowHeight: _rowHeight,
            selected: selected,
            describe: (t) => _describe(t),
          ),
        ),
      ],
    );
  }

  String _describe(_Tick t) {
    final day = days[t.row];
    final long = t.visit.minutes == null
        ? ''
        : ', ${t.visit.minutes! < 1 ? 'under a minute' : '${trimmed(t.visit.minutes!)} min'}';
    final who = t.visit.catId == null
        ? 'not matched to a cat'
        : nameOf(t.visit.catId);
    return '${DateFormat.MMMd().format(day.date)} ${clockText(t.visit.hour)}$long, $who';
  }

  int? _pick(Offset position, Size size, List<_Tick> ticks) {
    int? best;
    var bestDistance = 14.0;
    for (var i = 0; i < ticks.length; i++) {
      final c = _tickRect(ticks[i], size.width, _rowHeight, lanes).center;
      final d = (c - position).distance;
      if (d < bestDistance) {
        best = i;
        bestDistance = d;
      }
    }
    return best;
  }
}

class _Tick {
  const _Tick(this.row, this.visit, this.lane);

  final int row;
  final RhythmVisit visit;
  final int? lane;
}

Rect _tickRect(_Tick t, double width, double rowHeight, int lanes) {
  final plot = width - _gutter - 4;
  final x = _gutter + t.visit.hour / 24 * plot;
  final minutes = t.visit.minutes ?? 0;
  final w = math.max(4.0, minutes / 1440 * plot);
  final top = _headerHeight + t.row * rowHeight;
  final lane = t.lane;
  if (lane == null || lanes <= 1) {
    return Rect.fromLTWH(x, top + 0.5, w, rowHeight - 1);
  }
  final h = (rowHeight - 1) / lanes;
  return Rect.fromLTWH(x, top + 0.5 + lane * h, w, h);
}

class _ActogramPainter extends CustomPainter {
  _ActogramPainter({
    required this.palette,
    required this.days,
    required this.ticks,
    required this.lanes,
    required this.rowHeight,
    required this.selected,
    required this.describe,
  });

  final Palette palette;
  final List<ActogramDay> days;
  final List<_Tick> ticks;
  final int lanes;
  final double rowHeight;
  final int? selected;
  final String Function(_Tick) describe;

  @override
  void paint(Canvas canvas, Size size) {
    final plot = size.width - _gutter - 4;
    final grid = Paint()
      ..color = palette.lineSoft
      ..strokeWidth = 1;
    for (var i = 0; i < _hourLabels.length; i++) {
      final x = _gutter + i * 6 / 24 * plot;
      canvas.drawLine(
        Offset(x, _headerHeight - 4),
        Offset(x, size.height - 4),
        grid,
      );
      final text = chartText(_hourLabels[i], palette.sub, size: 12);
      final left = (x - text.width / 2).clamp(
        _gutter - 8,
        size.width - text.width,
      );
      text.paint(canvas, Offset(left.toDouble(), 2));
    }
    for (var row = 0; row < days.length; row++) {
      final day = days[row];
      final rect = Rect.fromLTWH(
        _gutter,
        _headerHeight + row * rowHeight,
        plot,
        rowHeight,
      );
      if (day.state == 'none') {
        paintHatch(canvas, rect, palette.sub.withValues(alpha: 0.4), gap: 5);
      } else if (day.unreliable) {
        canvas.drawRect(
          rect,
          Paint()..color = palette.avg.withValues(alpha: 0.16),
        );
      } else if (day.state == 'partial') {
        canvas.drawRect(
          rect,
          Paint()..color = palette.sub.withValues(alpha: 0.07),
        );
      }
    }
    final fill = Paint()..color = palette.chart;
    final outline = Paint()
      ..color = palette.ink.withValues(alpha: 0.8)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2;
    for (final t in ticks) {
      final rect = _tickRect(t, size.width, rowHeight, lanes);
      if (t.visit.sure) {
        canvas.drawRect(rect, fill);
      } else {
        canvas.drawRect(rect.deflate(0.4), outline);
      }
    }
    final step = days.length <= 8 ? 1 : (days.length <= 31 ? 7 : 14);
    for (var row = 0; row < days.length; row += step) {
      final text = chartText(
        DateFormat.MMMd().format(days[row].date),
        palette.sub,
        size: 12,
      );
      text.paint(
        canvas,
        Offset(
          0,
          _headerHeight + row * rowHeight + rowHeight / 2 - text.height / 2,
        ),
      );
    }
    final index = selected;
    if (index != null && index < ticks.length) {
      final rect = _tickRect(ticks[index], size.width, rowHeight, lanes);
      canvas.drawRect(
        rect.inflate(2),
        Paint()
          ..color = palette.ink
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
      paintTooltip(canvas, size, rect.center, describe(ticks[index]), palette);
    }
  }

  @override
  bool shouldRepaint(_ActogramPainter old) =>
      old.palette != palette ||
      old.selected != selected ||
      old.days != days ||
      old.lanes != lanes;
}

/// Eight 3-hour bars: the share of visits in each part of the day over the
/// last four weeks, with the share over the eight weeks before as a dash.
class HourProfile extends StatelessWidget {
  const HourProfile({required this.blocks, super.key});

  final List<RhythmBlock> blocks;

  static const _labels = ['12a', '3a', '6a', '9a', '12p', '3p', '6p', '9p'];

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final top = blocks
        .expand((b) => [b.share, ?b.usualShare])
        .fold<double>(0, math.max);
    final axis = ChartAxis.fit(0, top * 100, fromZero: true, maxIntervals: 4);
    final usual = blocks.any((b) => b.usualShare != null);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ChartFrame(
          height: 190,
          data: [
            for (final b in blocks) ...[b.share, b.usualShare],
          ],
          semanticLabel:
              'Share of visits in each three hour part of the day. ${blocks.map((b) => '${b.label} ${(b.share * 100).round()} percent').join(', ')}.',
          pick: (position, size) {
            if (position.dy > 160) return null;
            final slot = (size.width - 44) / blocks.length;
            final i = ((position.dx - 44) / slot).floor();
            return i >= 0 && i < blocks.length ? i : null;
          },
          paint: (selected) => _ProfilePainter(palette, blocks, axis, selected),
        ),
        const SizedBox(height: 8),
        MarkLegend(
          items: [
            const KeyItem('Last 4 weeks', KeyMark.tick),
            if (usual) const KeyItem('Before that', KeyMark.dash),
          ],
        ),
      ],
    );
  }
}

class _ProfilePainter extends CustomPainter {
  _ProfilePainter(this.palette, this.blocks, this.axis, this.selected);

  final Palette palette;
  final List<RhythmBlock> blocks;
  final ChartAxis axis;
  final int? selected;

  double _y(double percent) => 8 + (1 - axis.fraction(percent)) * 140;

  @override
  void paint(Canvas canvas, Size size) {
    paintHorizontalGrid(
      canvas,
      palette,
      left: 44,
      right: size.width,
      rows: [for (final t in axis.ticks.reversed) (_y(t), '${axis.label(t)}%')],
    );
    final slot = (size.width - 44) / blocks.length;
    for (var i = 0; i < blocks.length; i++) {
      final b = blocks[i];
      final left = 44 + slot * i + slot * 0.18;
      final width = slot * 0.64;
      final y = _y(b.share * 100);
      canvas.drawRRect(
        RRect.fromRectAndCorners(
          Rect.fromLTRB(left, y, left + width, _y(0)),
          topLeft: const Radius.circular(4),
          topRight: const Radius.circular(4),
        ),
        Paint()
          ..color = palette.chart.withValues(alpha: i == selected ? 1 : 0.8),
      );
      final usual = b.usualShare;
      if (usual != null) {
        final uy = _y(usual * 100);
        final paint = Paint()
          ..color = palette.avg
          ..strokeWidth = 3;
        canvas.drawLine(
          Offset(left - 3, uy),
          Offset(left + width + 3, uy),
          paint,
        );
      }
      final text = chartText(HourProfile._labels[i], palette.sub);
      text.paint(canvas, Offset(44 + slot * (i + 0.5) - text.width / 2, 156));
    }
    final index = selected;
    if (index != null) {
      final b = blocks[index];
      final usual = b.usualShare == null
          ? ''
          : ', before ${(b.usualShare! * 100).round()}%';
      paintTooltip(
        canvas,
        size,
        Offset(44 + slot * (index + 0.5), _y(b.share * 100)),
        '${b.label}: ${(b.share * 100).round()}% (${b.recent} visits)$usual',
        palette,
      );
    }
  }

  @override
  bool shouldRepaint(_ProfilePainter old) =>
      old.palette != palette ||
      old.selected != selected ||
      old.blocks != blocks;
}

/// Weekday by part of day, as visits per week. Every cell carries its number,
/// so the shade is only a help.
class WeekdayHeatmap extends StatelessWidget {
  const WeekdayHeatmap({required this.rows, super.key});

  final List<List<double?>> rows;

  static const _days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  static const _blocks = ['12-6 AM', '6-12 AM', '12-6 PM', '6-12 PM'];

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final top = rows
        .expand((r) => r.whereType<double>())
        .fold<double>(0, math.max);
    return Column(
      children: [
        Row(
          children: [
            const SizedBox(width: 44),
            for (final b in _blocks)
              Expanded(
                child: Center(
                  child: Text(
                    b,
                    style: TextStyle(fontSize: 12, color: palette.sub),
                  ),
                ),
              ),
          ],
        ),
        for (var d = 0; d < rows.length; d++)
          Row(
            children: [
              SizedBox(
                width: 44,
                child: Text(
                  _days[d],
                  style: TextStyle(fontSize: 13, color: palette.sub),
                ),
              ),
              for (final value in rows[d])
                Expanded(
                  child: Tooltip(
                    message: value == null
                        ? 'No days to count'
                        : '${_days[d]}: ${trimmed(value)} visits a week',
                    child: Container(
                      height: 30,
                      margin: const EdgeInsets.all(1.5),
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(5),
                        color: palette.chart.withValues(
                          alpha: value == null || top == 0
                              ? 0.04
                              : 0.08 + 0.55 * value / top,
                        ),
                      ),
                      child: Text(
                        value == null ? '-' : trimmed(value),
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: palette.ink,
                        ),
                      ),
                    ),
                  ),
                ),
            ],
          ),
      ],
    );
  }
}
