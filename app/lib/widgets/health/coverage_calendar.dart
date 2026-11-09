import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api.dart';
import '../../theme.dart';
import 'chart_keys.dart';

String dayDetail(QualityDay day) {
  final date = DateFormat('EEE, MMM d').format(day.date);
  final heard = switch (day.state) {
    'full' => 'The robot was heard all day.',
    'partial' => 'The robot was heard for only part of the day.',
    _ => 'No data from the robot. This is not counted as zero visits.',
  };
  final visits = day.state == 'none' && day.visits == 0
      ? ''
      : ' ${day.visits} ${day.visits == 1 ? 'visit' : 'visits'}'
            '${day.unnamed == 0 ? '' : ', ${day.unnamed} not matched to a cat'}.';
  final flags = [
    if (day.unreliable) ' Marked unreliable by you.',
    if (day.scaleDisturbed) ' The scale gave unusual readings.',
  ].join();
  return '$date. $heard$visits$flags';
}

/// One square per day, a row per week, Monday first. State is shown by fill
/// pattern as well as tone: solid is a full day, a half fill a partial day and
/// hatching a day with no data. A hatched amber border marks days you flagged
/// as unreliable, and a corner notch marks days the scale misbehaved.
class CoverageCalendar extends StatefulWidget {
  const CoverageCalendar({required this.quality, super.key});

  final DataQuality quality;

  @override
  State<CoverageCalendar> createState() => _CoverageCalendarState();
}

class _CoverageCalendarState extends State<CoverageCalendar> {
  QualityDay? _picked;

  @override
  void didUpdateWidget(CoverageCalendar old) {
    super.didUpdateWidget(old);
    if (old.quality != widget.quality) _picked = null;
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final days = {for (final d in widget.quality.days) _key(d.date): d};
    final first = widget.quality.start;
    final last = widget.quality.end;
    var monday = DateTime(
      first.year,
      first.month,
      first.day - (first.weekday - 1),
    );
    final weeks = <DateTime>[];
    while (!monday.isAfter(last)) {
      weeks.add(monday);
      monday = DateTime(monday.year, monday.month, monday.day + 7);
    }
    final picked = _picked;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const SizedBox(width: 52),
                for (final name in const ['M', 'T', 'W', 'T', 'F', 'S', 'S'])
                  Expanded(
                    child: Center(
                      child: Text(
                        name,
                        style: TextStyle(fontSize: 13, color: palette.sub),
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 4),
            for (final week in weeks.reversed)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Row(
                  children: [
                    SizedBox(
                      width: 52,
                      child: Text(
                        DateFormat.MMMd().format(week),
                        style: TextStyle(fontSize: 13, color: palette.sub),
                      ),
                    ),
                    for (var i = 0; i < 7; i++)
                      Expanded(
                        child: _cell(
                          context,
                          days[_key(
                            DateTime(week.year, week.month, week.day + i),
                          )],
                          picked,
                        ),
                      ),
                  ],
                ),
              ),
            const SizedBox(height: 8),
            if (picked != null)
              Text(
                dayDetail(picked),
                key: const ValueKey('calendar-detail'),
                style: TextStyle(fontSize: 15, height: 1.4, color: palette.ink),
              )
            else
              Text(
                'Tap a day for details.',
                style: TextStyle(fontSize: 14, color: palette.sub),
              ),
            const SizedBox(height: 8),
            const MarkLegend(
              items: [
                KeyItem('Heard all day', KeyMark.dot),
                KeyItem('Heard part of the day', KeyMark.band),
                KeyItem('No data', KeyMark.hatch),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              'An amber outline marks days you flagged as unreliable, and an '
              'amber corner marks days the scale gave unusual readings.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13, height: 1.35, color: palette.sub),
            ),
          ],
        ),
      ),
    );
  }

  static int _key(DateTime d) => d.year * 10000 + d.month * 100 + d.day;

  Widget _cell(BuildContext context, QualityDay? day, QualityDay? picked) {
    if (day == null) return const SizedBox(height: 34);
    return Padding(
      padding: const EdgeInsets.all(1.5),
      child: Semantics(
        button: true,
        label: dayDetail(day),
        child: Tooltip(
          message: dayDetail(day),
          child: InkWell(
            key: ValueKey('cal-${DateFormat('yyyy-MM-dd').format(day.date)}'),
            onTap: () => setState(() => _picked = day),
            borderRadius: BorderRadius.circular(6),
            child: SizedBox(
              height: 31,
              child: CustomPaint(
                painter: _CellPainter(context.palette, day, day == picked),
                child: Center(
                  child: ExcludeSemantics(
                    child: Text(
                      '${day.date.day}',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: day.state == 'full'
                            ? context.palette.onPrimary
                            : context.palette.ink,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _CellPainter extends CustomPainter {
  _CellPainter(this.palette, this.day, this.picked);

  final Palette palette;
  final QualityDay day;
  final bool picked;

  @override
  void paint(Canvas canvas, Size size) {
    final box = RRect.fromRectAndRadius(
      Offset.zero & size,
      const Radius.circular(6),
    );
    final outline = Paint()
      ..color = palette.sub
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    switch (day.state) {
      case 'full':
        canvas.drawRRect(box, Paint()..color = palette.chart);
      case 'partial':
        canvas.save();
        canvas.clipRRect(box);
        canvas.drawRect(
          Rect.fromLTWH(0, size.height * 0.55, size.width, size.height),
          Paint()..color = palette.chart.withValues(alpha: 0.4),
        );
        canvas.restore();
        canvas.drawRRect(box, outline);
      default:
        canvas.save();
        canvas.clipRRect(box);
        paintHatch(
          canvas,
          Offset.zero & size,
          palette.sub.withValues(alpha: 0.5),
        );
        canvas.restore();
        canvas.drawRRect(box, outline);
    }
    if (day.unreliable) {
      canvas.drawRRect(
        box.deflate(1),
        Paint()
          ..color = palette.avg
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.5,
      );
    }
    if (day.scaleDisturbed) {
      final notch = Path()
        ..moveTo(size.width - 11, 0)
        ..lineTo(size.width, 0)
        ..lineTo(size.width, 11)
        ..close();
      canvas.drawPath(notch, Paint()..color = palette.avg);
    }
    if (picked) {
      canvas.drawRRect(
        box.inflate(1),
        Paint()
          ..color = palette.ink
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
    }
  }

  @override
  bool shouldRepaint(_CellPainter old) =>
      old.palette != palette || old.day != day || old.picked != picked;
}
