part of 'api.dart';

/// A plain "worth a look" item: what changed and since when, never a diagnosis.
class HealthNote {
  const HealthNote({
    required this.key,
    required this.kind,
    required this.catId,
    required this.level,
    required this.title,
    required this.body,
    required this.since,
  });

  factory HealthNote.fromJson(Map<String, dynamic> json) => HealthNote(
    key: json['key'] as String,
    kind: json['kind'] as String,
    catId: json['cat_id'] as int?,
    level: json['level'] as String,
    title: json['title'] as String,
    body: json['body'] as String,
    since: _date(json['since']),
  );

  final String key;
  final String kind;
  final int? catId;

  /// `urgent`, `look` or `info`.
  final String level;
  final String title;
  final String body;
  final DateTime? since;

  bool get urgent => level == 'urgent';
}

/// A check that cannot run yet, with how much more history it needs.
class WaitingCheck {
  const WaitingCheck({
    required this.kind,
    required this.catId,
    required this.ready,
    required this.text,
  });

  factory WaitingCheck.fromJson(Map<String, dynamic> json) => WaitingCheck(
    kind: json['kind'] as String,
    catId: json['cat_id'] as int?,
    ready: json['ready'] as bool,
    text: json['text'] as String,
  );

  final String kind;
  final int? catId;
  final bool ready;
  final String text;
}

class WorthALook {
  const WorthALook({required this.notes, required this.waiting});

  factory WorthALook.fromJson(Map<String, dynamic> json) => WorthALook(
    notes: _list(json['notes'], HealthNote.fromJson),
    waiting: _list(json['waiting'], WaitingCheck.fromJson),
  );

  final List<HealthNote> notes;
  final List<WaitingCheck> waiting;
}

class QualityDay {
  const QualityDay({
    required this.date,
    required this.state,
    required this.unreliable,
    required this.scaleDisturbed,
    required this.visits,
    required this.named,
    required this.unnamed,
    this.cyclesWatched = 0,
    this.pausesPerCycle,
  });

  factory QualityDay.fromJson(Map<String, dynamic> json) => QualityDay(
    date: _date(json['date'])!,
    state: json['state'] as String,
    unreliable: json['unreliable'] != null,
    scaleDisturbed: json['scale_disturbed'] as bool,
    visits: json['visits'] as int,
    named: json['named'] as int,
    unnamed: json['unnamed'] as int,
    cyclesWatched: json['cycles_watched'] as int? ?? 0,
    pausesPerCycle: _number(json['pauses_per_cycle']),
  );

  final DateTime date;

  /// `full`, `partial` or `none`.
  final String state;
  final bool unreliable;
  final bool scaleDisturbed;
  final int visits;
  final int named;
  final int unnamed;

  /// Clean cycles the server saw from start to end that day. Imported ones
  /// are not counted, because they carry no pause figures.
  final int cyclesWatched;

  /// Null on a day with no cycle watched, which is not the same as none.
  final double? pausesPerCycle;
}

/// A run of days that should not be read as ordinary data.
class DataGap {
  const DataGap({required this.start, required this.end, required this.kind});

  factory DataGap.fromJson(Map<String, dynamic> json) => DataGap(
    start: _date(json['start'])!,
    end: _date(json['end'])!,
    kind: json['kind'] as String,
  );

  final DateTime start;
  final DateTime end;

  /// `no_data`, `unreliable` or `scale`.
  final String kind;
}

class UnreliableRange {
  const UnreliableRange({
    required this.id,
    required this.start,
    required this.end,
    required this.note,
  });

  factory UnreliableRange.fromJson(Map<String, dynamic> json) =>
      UnreliableRange(
        id: json['id'] as int,
        start: _date(json['start_date'])!,
        end: _date(json['end_date'])!,
        note: json['note'] as String? ?? '',
      );

  final int id;
  final DateTime start;
  final DateTime end;
  final String note;
}

class DataQuality {
  const DataQuality({
    required this.start,
    required this.end,
    required this.earliest,
    required this.days,
    required this.gaps,
    required this.ranges,
    required this.visits,
    required this.unnamed,
  });

  factory DataQuality.fromJson(Map<String, dynamic> json) {
    final summary = _map(json['summary']);
    return DataQuality(
      start: _date(json['start'])!,
      end: _date(json['end'])!,
      earliest: _date(json['earliest']),
      days: _list(json['days'], QualityDay.fromJson),
      gaps: _list(json['gaps'], DataGap.fromJson),
      ranges: _list(json['unreliable_ranges'], UnreliableRange.fromJson),
      visits: summary['visits'] as int,
      unnamed: summary['unnamed'] as int,
    );
  }

  final DateTime start;
  final DateTime end;
  final DateTime? earliest;
  final List<QualityDay> days;
  final List<DataGap> gaps;
  final List<UnreliableRange> ranges;
  final int visits;
  final int unnamed;

  double? get unnamedShare => visits == 0 ? null : unnamed / visits;

  /// Pauses per cycle over every cycle watched in these weeks, or null when
  /// none was.
  double? get pausesPerCycle {
    var cycles = 0;
    var pauses = 0.0;
    for (final day in days) {
      final rate = day.pausesPerCycle;
      if (rate == null) continue;
      cycles += day.cyclesWatched;
      pauses += rate * day.cyclesWatched;
    }
    return cycles == 0 ? null : pauses / cycles;
  }
}

class WeightReading {
  const WeightReading({
    required this.date,
    required this.hour,
    required this.lb,
    required this.hollow,
    required this.why,
  });

  factory WeightReading.fromJson(Map<String, dynamic> json) => WeightReading(
    date: _date(json['date'])!,
    hour: _number(json['hour'])!,
    lb: _number(json['lb'])!,
    hollow: json['hollow'] as bool,
    why: json['why'] as String?,
  );

  final DateTime date;
  final double hour;
  final double lb;

  /// Drawn as a ring: kept in view, left out of the line.
  final bool hollow;
  final String? why;
}

class WeightLinePoint {
  const WeightLinePoint({
    required this.date,
    required this.median,
    required this.low,
    required this.high,
  });

  factory WeightLinePoint.fromJson(Map<String, dynamic> json) =>
      WeightLinePoint(
        date: _date(json['date'])!,
        median: _number(json['median'])!,
        low: _number(json['low'])!,
        high: _number(json['high'])!,
      );

  final DateTime date;
  final double median;
  final double low;
  final double high;
}

class WeightChart {
  const WeightChart({
    required this.start,
    required this.end,
    required this.anchorLb,
    required this.readings,
    required this.line,
    required this.gaps,
    required this.stage,
    required this.medianLb,
    required this.changePct,
    required this.rangeLowPct,
    required this.rangeHighPct,
    required this.noisePct,
    required this.statusText,
  });

  factory WeightChart.fromJson(Map<String, dynamic> json) {
    final latest = json['latest'] as Map<String, dynamic>?;
    final range = latest?['range_pct'] as List<dynamic>?;
    final status = _map(json['status']);
    return WeightChart(
      start: _date(json['start'])!,
      end: _date(json['end'])!,
      anchorLb: json['anchor'] == null
          ? null
          : _number(_map(json['anchor'])['weight_lb']),
      readings: _list(json['readings'], WeightReading.fromJson),
      line: _list(json['line'], WeightLinePoint.fromJson),
      gaps: _list(json['bands'], DataGap.fromJson),
      stage: latest?['stage'] as String? ?? 'none',
      medianLb: _number(latest?['median_lb']),
      changePct: _number(latest?['change_pct']),
      rangeLowPct: range == null ? null : _number(range[0]),
      rangeHighPct: range == null ? null : _number(range[1]),
      noisePct: _number(latest?['noise_pct']),
      statusText: status['text'] as String?,
    );
  }

  final DateTime start;
  final DateTime end;
  final double? anchorLb;
  final List<WeightReading> readings;
  final List<WeightLinePoint> line;
  final List<DataGap> gaps;

  /// `none`, `trend` or `change`: how much the history allows saying.
  final String stage;
  final double? medianLb;
  final double? changePct;
  final double? rangeLowPct;
  final double? rangeHighPct;
  final double? noisePct;

  /// What is still missing, or null when nothing is.
  final String? statusText;
}

class RhythmVisit {
  const RhythmVisit({
    required this.hour,
    required this.minutes,
    required this.catId,
    required this.sure,
  });

  factory RhythmVisit.fromJson(Map<String, dynamic> json) => RhythmVisit(
    hour: _number(json['hour'])!,
    minutes: _number(json['minutes']),
    catId: json['cat_id'] as int?,
    sure: json['sure'] as bool,
  );

  final double hour;
  final double? minutes;
  final int? catId;
  final bool sure;
}

class ActogramDay {
  const ActogramDay({
    required this.date,
    required this.state,
    required this.unreliable,
    required this.visits,
  });

  factory ActogramDay.fromJson(Map<String, dynamic> json) => ActogramDay(
    date: _date(json['date'])!,
    state: json['state'] as String,
    unreliable: json['unreliable'] as bool,
    visits: _list(json['visits'], RhythmVisit.fromJson),
  );

  final DateTime date;
  final String state;
  final bool unreliable;
  final List<RhythmVisit> visits;
}

class RhythmBlock {
  const RhythmBlock({
    required this.label,
    required this.recent,
    required this.share,
    required this.usualShare,
  });

  factory RhythmBlock.fromJson(Map<String, dynamic> json) => RhythmBlock(
    label: json['label'] as String,
    recent: json['recent'] as int,
    share: _number(json['share'])!,
    usualShare: _number(json['usual_share']),
  );

  final String label;
  final int recent;
  final double share;
  final double? usualShare;
}

class Rhythm {
  const Rhythm({
    required this.actogram,
    required this.blocks,
    required this.recentVisits,
    required this.heatmap,
    required this.heatmapNeedDays,
  });

  factory Rhythm.fromJson(Map<String, dynamic> json) {
    final profile = json['profile'] as Map<String, dynamic>?;
    final heat = json['heatmap'] as Map<String, dynamic>?;
    final rows = heat?['rows'] as List<dynamic>?;
    return Rhythm(
      actogram: _list(json['actogram'], ActogramDay.fromJson),
      blocks: profile == null
          ? const []
          : _list(profile['blocks'], RhythmBlock.fromJson),
      recentVisits: profile?['recent_visits'] as int? ?? 0,
      heatmap: rows == null
          ? null
          : [
              for (final row in rows)
                [for (final cell in row as List<dynamic>) _number(cell)],
            ],
      heatmapNeedDays: heat?['need_days'] as int? ?? 0,
    );
  }

  final List<ActogramDay> actogram;
  final List<RhythmBlock> blocks;
  final int recentVisits;

  /// Visits per week for each weekday (Monday first) and 6-hour block, or null
  /// until there are about eight weeks.
  final List<List<double?>>? heatmap;
  final int heatmapNeedDays;
}
