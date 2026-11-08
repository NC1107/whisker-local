import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

class ApiException implements Exception {
  const ApiException(this.message, {this.status});

  final String message;
  final int? status;

  @override
  String toString() => message;
}

/// The server answered, but not with something this app understands.
class UnexpectedResponse extends ApiException {
  const UnexpectedResponse()
    : super('The server sent something unexpected.', status: null);
}

DateTime? _time(Object? value) =>
    value == null ? null : DateTime.parse(value as String).toLocal();

/// A calendar day like `2026-11-08`, as local midnight so it never shifts a day.
DateTime? _date(Object? value) =>
    value == null ? null : DateTime.parse(value as String);

double? _number(Object? value) => (value as num?)?.toDouble();

Map<String, dynamic> _map(Object? value) => value! as Map<String, dynamic>;

List<T> _list<T>(Object? value, T Function(Map<String, dynamic>) read) =>
    (value! as List<dynamic>).map((item) => read(_map(item))).toList();

class RobotSettings {
  const RobotSettings({
    this.nightLightMode,
    this.nightLightBrightness,
    this.cleanCycleWaitMinutes,
    this.keypadLockout,
    this.sleepEnabled,
    this.sleepMinute,
    this.wakeMinute,
  });

  factory RobotSettings.fromJson(Map<String, dynamic> json) => RobotSettings(
    nightLightMode: json['night_light_mode'] as String?,
    nightLightBrightness: json['night_light_brightness'] as int?,
    cleanCycleWaitMinutes: json['clean_cycle_wait_minutes'] as int?,
    keypadLockout: json['keypad_lockout'] as bool?,
    sleepEnabled: json['sleep_enabled'] as bool?,
    sleepMinute: json['sleep_minute'] as int?,
    wakeMinute: json['wake_minute'] as int?,
  );

  final String? nightLightMode;
  final int? nightLightBrightness;
  final int? cleanCycleWaitMinutes;
  final bool? keypadLockout;
  final bool? sleepEnabled;
  final int? sleepMinute;
  final int? wakeMinute;
}

/// Where the litter percentage comes from, which decides how far to trust it.
enum LitterSource {
  reported,
  calibrated,
  learned,
  fallback;

  static LitterSource? parse(String? code) => switch (code) {
    'reported' => reported,
    'calibrated' => calibrated,
    'learned' => learned,
    'default' => fallback,
    _ => null,
  };
}

class LitterCalibration {
  const LitterCalibration({this.fullMm, this.emptyMm});

  factory LitterCalibration.fromJson(Map<String, dynamic> json) =>
      LitterCalibration(
        fullMm: _number(json['full_mm']),
        emptyMm: _number(json['empty_mm']),
      );

  final double? fullMm;
  final double? emptyMm;
}

class Hopper {
  const Hopper({
    required this.status,
    required this.connected,
    required this.empty,
    this.levelPercent,
    this.levelSource,
    this.fillRaw,
    this.lastDispensed,
  });

  factory Hopper.fromJson(Map<String, dynamic> json) => Hopper(
    status: json['status'] as String,
    connected: json['connected'] as bool? ?? false,
    empty: json['empty'] as bool? ?? false,
    levelPercent: (json['level_percent'] as num?)?.round(),
    levelSource: json['level_source'] as String?,
    fillRaw: _number(json['fill_raw']),
    lastDispensed: _time(json['last_dispensed']),
  );

  /// `ok`, `empty`, `disconnected` or `unknown`.
  final String status;
  final bool connected;
  final bool empty;
  final int? levelPercent;

  /// `measured` or `estimate`.
  final String? levelSource;
  final double? fillRaw;
  final DateTime? lastDispensed;
}

class RobotView {
  const RobotView({
    required this.status,
    required this.statusLabel,
    required this.isCleaning,
    required this.catPresent,
    required this.drawerIsEstimate,
    required this.drawerFull,
    required this.settings,
    this.drawerFullLockout = false,
    this.drawerLastMoved,
    this.bonnetRemoved = false,
    this.drawerPercent,
    this.litterPercent,
    this.litterLevelMm,
    this.litterSource,
    this.litterCalibrated = false,
    this.litterCalibration = const LitterCalibration(),
    this.hopper,
    this.cleanCycles,
    this.emptyCycles,
    this.wifiRssi,
    this.espFirmware,
    this.picFirmware,
    this.lastVisitAt,
    this.lastVisitDurationS,
    this.lastWeightLb,
  });

  factory RobotView.fromJson(Map<String, dynamic> json) => RobotView(
    status: json['status'] as String?,
    statusLabel: json['status_label'] as String,
    isCleaning: json['is_cleaning'] as bool,
    catPresent: json['cat_present'] as bool,
    drawerIsEstimate: json['drawer_is_estimate'] as bool,
    drawerFull: json['drawer_full'] as bool,
    drawerFullLockout: json['drawer_full_lockout'] as bool? ?? false,
    drawerLastMoved: _time(json['drawer_last_moved']),
    bonnetRemoved: json['bonnet_removed'] as bool? ?? false,
    settings: RobotSettings.fromJson(_map(json['settings'])),
    drawerPercent: json['drawer_percent'] as int?,
    litterPercent: json['litter_percent'] as int?,
    litterLevelMm: _number(json['litter_level_mm']),
    litterSource: LitterSource.parse(json['litter_source'] as String?),
    litterCalibrated: json['litter_calibrated'] as bool? ?? false,
    litterCalibration: json['litter_calibration'] == null
        ? const LitterCalibration()
        : LitterCalibration.fromJson(_map(json['litter_calibration'])),
    hopper: json['hopper'] == null
        ? null
        : Hopper.fromJson(_map(json['hopper'])),
    cleanCycles: json['clean_cycles'] as int?,
    emptyCycles: json['empty_cycles'] as int?,
    wifiRssi: json['wifi_rssi'] as int?,
    espFirmware: json['esp_firmware'] as String?,
    picFirmware: json['pic_firmware'] as String?,
    lastVisitAt: _time(json['last_visit_at']),
    lastVisitDurationS: json['last_visit_duration_s'] as int?,
    lastWeightLb: _number(json['last_weight_lb']),
  );

  final String? status;
  final String statusLabel;
  final bool isCleaning;
  final bool catPresent;
  final bool drawerIsEstimate;
  final bool drawerFull;

  /// A full drawer stopped the robot until somebody presses Reset.
  final bool drawerFullLockout;
  final DateTime? drawerLastMoved;
  final bool bonnetRemoved;
  final RobotSettings settings;
  final int? drawerPercent;
  final int? litterPercent;
  final double? litterLevelMm;
  final LitterSource? litterSource;
  final bool litterCalibrated;
  final LitterCalibration litterCalibration;
  final Hopper? hopper;
  final int? cleanCycles;
  final int? emptyCycles;
  final int? wifiRssi;
  final String? espFirmware;
  final String? picFirmware;
  final DateTime? lastVisitAt;
  final int? lastVisitDurationS;
  final double? lastWeightLb;
}

class RobotAlert {
  const RobotAlert({
    required this.key,
    required this.urgent,
    required this.message,
  });

  factory RobotAlert.fromJson(Map<String, dynamic> json) => RobotAlert(
    key: json['key'] as String,
    urgent: json['urgent'] as bool,
    message: json['message'] as String,
  );

  final String key;
  final bool urgent;
  final String message;
}

/// A visit that still has no cat, newest first on the home tab.
class UnlabelledVisit {
  const UnlabelledVisit({
    required this.id,
    required this.at,
    this.weightLb,
    this.durationS,
    this.closestCatId,
  });

  factory UnlabelledVisit.fromJson(Map<String, dynamic> json) =>
      UnlabelledVisit(
        id: json['id'] as int,
        at: _time(json['at'])!,
        weightLb: _number(json['weight_lb']),
        durationS: json['duration_s'] as int?,
        closestCatId: json['closest_cat_id'] as int?,
      );

  final int id;
  final DateTime at;
  final double? weightLb;
  final int? durationS;

  /// The cat whose usual weight is nearest, to suggest in the picker.
  final int? closestCatId;
}

class ServerStatus {
  const ServerStatus({
    required this.serial,
    required this.online,
    required this.brokerConnected,
    required this.alerts,
    this.demo = false,
    this.cats = const [],
    this.visitsNeedingCat = 0,
    this.latestVisitNeedingCat,
    this.lastSeen,
    this.robot,
  });

  factory ServerStatus.fromJson(Map<String, dynamic> json) => ServerStatus(
    serial: json['serial'] as String,
    online: json['online'] as bool,
    brokerConnected: json['broker_connected'] as bool,
    lastSeen: _time(json['last_seen']),
    robot: json['robot'] == null
        ? null
        : RobotView.fromJson(_map(json['robot'])),
    alerts: _list(json['alerts'], RobotAlert.fromJson),
    demo: json['demo'] as bool? ?? false,
    cats: json['cats'] == null ? const [] : _list(json['cats'], Cat.fromJson),
    visitsNeedingCat: json['visits_needing_cat'] as int? ?? 0,
    latestVisitNeedingCat: json['latest_visit_needing_cat'] == null
        ? null
        : UnlabelledVisit.fromJson(_map(json['latest_visit_needing_cat'])),
  );

  final String serial;
  final bool online;
  final bool brokerConnected;
  final DateTime? lastSeen;
  final RobotView? robot;
  final List<RobotAlert> alerts;

  /// True when the server runs on sample data instead of a real robot.
  final bool demo;
  final List<Cat> cats;
  final int visitsNeedingCat;
  final UnlabelledVisit? latestVisitNeedingCat;

  /// The server answers settings and calibration with the robot part only, so
  /// the cats and counters are carried over from the status it replaces.
  ServerStatus withLiveFrom(ServerStatus live) => ServerStatus(
    serial: live.serial,
    online: live.online,
    brokerConnected: live.brokerConnected,
    lastSeen: live.lastSeen,
    robot: live.robot,
    alerts: live.alerts,
    demo: demo,
    cats: cats,
    visitsNeedingCat: visitsNeedingCat,
    latestVisitNeedingCat: latestVisitNeedingCat,
  );

  Cat? catById(int id) {
    for (final cat in cats) {
      if (cat.id == id) return cat;
    }
    return null;
  }
}

enum CatSex {
  female('female', 'Female'),
  spayedFemale('spayed_female', 'Spayed female'),
  male('male', 'Male'),
  neuteredMale('neutered_male', 'Neutered male'),
  unknown('unknown', 'Unknown');

  const CatSex(this.code, this.label);

  final String code;
  final String label;

  static CatSex parse(String? code) => values.firstWhere(
    (sex) => sex.code == code,
    orElse: () => CatSex.unknown,
  );
}

/// Numbers worked out by the server for one cat. A null means there is nothing to work it out from.
class CatSummary {
  const CatSummary({
    this.latestWeightLb,
    this.usualWeightLb,
    this.visitsToday = 0,
    this.visitsPerDay,
    this.avgVisitS,
    this.weightChangePct,
    this.lastVisitAt,
    this.yesterdayWeightLb,
    this.yesterdayVisits = 0,
    this.yesterdayMinutes,
    this.weekVisits = 0,
    this.commonVisitHour,
  });

  factory CatSummary.fromJson(Map<String, dynamic> json) => CatSummary(
    latestWeightLb: _number(json['latest_weight_lb']),
    usualWeightLb: _number(json['usual_weight_lb']),
    visitsToday: json['visits_today'] as int? ?? 0,
    visitsPerDay: _number(json['visits_per_day']),
    avgVisitS: _number(json['avg_visit_s']),
    weightChangePct: _number(json['weight_change_pct']),
    lastVisitAt: _time(json['last_visit_at']),
    yesterdayWeightLb: _number(json['yesterday_weight_lb']),
    yesterdayVisits: json['yesterday_visits'] as int? ?? 0,
    yesterdayMinutes: _number(json['yesterday_minutes']),
    weekVisits: json['week_visits'] as int? ?? 0,
    commonVisitHour: json['common_visit_hour'] as int?,
  );

  final double? latestWeightLb;
  final double? usualWeightLb;
  final int visitsToday;
  final double? visitsPerDay;
  final double? avgVisitS;
  final double? weightChangePct;
  final DateTime? lastVisitAt;
  final double? yesterdayWeightLb;
  final int yesterdayVisits;
  final double? yesterdayMinutes;
  final int weekVisits;

  /// 0 to 23, or null while there are no visits.
  final int? commonVisitHour;
}

class Cat {
  const Cat({
    required this.id,
    required this.name,
    required this.colorIndex,
    required this.sex,
    required this.hasPhoto,
    required this.photoVersion,
    required this.archived,
    required this.summary,
    this.breed,
    this.birthday,
    this.referenceWeightLb,
    this.createdAt,
  });

  factory Cat.fromJson(Map<String, dynamic> json) => Cat(
    id: json['id'] as int,
    name: json['name'] as String,
    colorIndex: json['color_index'] as int? ?? 0,
    breed: json['breed'] as String?,
    birthday: _date(json['birthday']),
    sex: CatSex.parse(json['sex'] as String?),
    referenceWeightLb: _number(
      json['reference_weight_lb'] ?? json['estimated_weight_lb'],
    ),
    hasPhoto: json['has_photo'] as bool? ?? false,
    photoVersion: json['photo_version'] as int? ?? 0,
    archived: json['archived'] as bool? ?? false,
    createdAt: _time(json['created_at']),
    summary: json['summary'] == null
        ? const CatSummary()
        : CatSummary.fromJson(_map(json['summary'])),
  );

  final int id;
  final String name;

  /// Never wraps on the server, so take it modulo the palette (see `Palette.catColor`).
  final int colorIndex;
  final String? breed;
  final DateTime? birthday;
  final CatSex sex;
  final double? referenceWeightLb;
  final bool hasPhoto;

  /// Changes whenever the photo does.
  final int photoVersion;
  final bool archived;
  final DateTime? createdAt;
  final CatSummary summary;
}

class HistoryEvent {
  const HistoryEvent({
    required this.id,
    required this.cursor,
    required this.at,
    required this.kind,
    required this.detail,
  });

  factory HistoryEvent.fromJson(Map<String, dynamic> json) => HistoryEvent(
    id: json['id'] as int,
    cursor: json['cursor'] as String,
    at: _time(json['at'])!,
    kind: json['kind'] as String,
    detail: _map(json['detail']),
  );

  final int id;

  /// Opaque position of this event, sent back unchanged when asking for older events.
  final String cursor;
  final DateTime at;
  final String kind;
  final Map<String, dynamic> detail;

  double? get weightLb => _number(detail['weight_lb']);
  int? get durationS => detail['duration_s'] as int?;

  /// For an `alert` event: true when the alert started, false when it ended.
  bool get raised => detail['raised'] as bool? ?? true;
  bool get urgent => detail['urgent'] as bool? ?? false;
  String? get message => detail['message'] as String?;
  String? get alertKey => detail['key'] as String?;
}

enum VisitLabel {
  cat('cat'),
  notCat('not_cat'),
  unknown('unknown');

  const VisitLabel(this.code);

  final String code;

  static VisitLabel parse(String? code) => values.firstWhere(
    (label) => label.code == code,
    orElse: () => VisitLabel.unknown,
  );
}

class Visit {
  const Visit({
    required this.id,
    required this.cursor,
    required this.at,
    required this.label,
    required this.userLabelled,
    this.catId,
    this.startedAt,
    this.durationS,
    this.weightLb,
  });

  factory Visit.fromJson(Map<String, dynamic> json) {
    final detail = _map(json['detail']);
    return Visit(
      id: json['id'] as int,
      cursor: json['cursor'] as String,
      at: _time(json['at'])!,
      startedAt: _time(detail['started']),
      durationS: detail['duration_s'] as int?,
      weightLb: _number(detail['weight_lb']),
      label: VisitLabel.parse(json['label'] as String?),
      catId: json['cat_id'] as int?,
      userLabelled: json['label_source'] == 'user',
    );
  }

  final int id;
  final String cursor;

  /// When the visit ended.
  final DateTime at;
  final DateTime? startedAt;
  final int? durationS;
  final double? weightLb;
  final VisitLabel label;
  final int? catId;

  /// True when a person set the label, so the server never changes it.
  final bool userLabelled;
}

class WeightCluster {
  const WeightCluster({
    required this.centerLb,
    required this.lowLb,
    required this.highLb,
    required this.visits,
  });

  factory WeightCluster.fromJson(Map<String, dynamic> json) => WeightCluster(
    centerLb: _number(json['center_lb'])!,
    lowLb: _number(json['low_lb'])!,
    highLb: _number(json['high_lb'])!,
    visits: json['visits'] as int,
  );

  final double centerLb;
  final double lowLb;
  final double highLb;
  final int visits;
}

class CatSuggestions {
  const CatSuggestions({
    required this.enoughData,
    required this.visitsConsidered,
    required this.daysOfData,
    required this.minVisits,
    required this.minDays,
    required this.clusters,
  });

  factory CatSuggestions.fromJson(Map<String, dynamic> json) => CatSuggestions(
    enoughData: json['enough_data'] as bool,
    visitsConsidered: json['visits_considered'] as int,
    daysOfData: _number(json['days_of_data'])!,
    minVisits: json['min_visits'] as int,
    minDays: _number(json['min_days'])!,
    clusters: _list(json['clusters'], WeightCluster.fromJson),
  );

  final bool enoughData;
  final int visitsConsidered;
  final double daysOfData;
  final int minVisits;
  final double minDays;

  /// Lightest first.
  final List<WeightCluster> clusters;
}

class CatDay {
  const CatDay({
    required this.date,
    required this.visits,
    this.medianWeightLb,
    this.avgVisitS,
  });

  factory CatDay.fromJson(Map<String, dynamic> json) => CatDay(
    date: _date(json['date'])!,
    visits: json['visits'] as int,
    medianWeightLb: _number(json['median_weight_lb']),
    avgVisitS: _number(json['avg_visit_s']),
  );

  final DateTime date;
  final int visits;
  final double? medianWeightLb;
  final double? avgVisitS;
}

class CatSeries {
  const CatSeries({
    required this.catId,
    required this.start,
    required this.end,
    required this.days,
  });

  factory CatSeries.fromJson(Map<String, dynamic> json) => CatSeries(
    catId: json['cat_id'] as int,
    start: _date(json['start'])!,
    end: _date(json['end'])!,
    days: _list(json['days'], CatDay.fromJson),
  );

  final int catId;
  final DateTime start;
  final DateTime end;
  final List<CatDay> days;
}

enum InsightPeriod {
  week('week', 'Week'),
  month('month', 'Month'),
  year('year', 'Year');

  const InsightPeriod(this.code, this.label);

  final String code;
  final String label;
}

/// One value per point of the period, with null where there is no data.
class MetricSeries {
  const MetricSeries({required this.values, this.average});

  factory MetricSeries.fromJson(Map<String, dynamic> json) => MetricSeries(
    values: (json['series'] as List<dynamic>).map(_number).toList(),
    average: _number(json['average']),
  );

  final List<double?> values;
  final double? average;
}

/// A dot on the time-of-visit chart.
class VisitDot {
  const VisitDot({
    required this.day,
    required this.hour,
    required this.durationS,
  });

  factory VisitDot.fromJson(Map<String, dynamic> json) => VisitDot(
    day: json['day'] as int,
    hour: _number(json['hour'])!,
    durationS: json['duration_s'] as int? ?? 0,
  );

  /// The column, counting from 0 at the start of the period.
  final int day;

  /// Hour of the day as a decimal, so 19.5 is 7:30 PM.
  final double hour;
  final int durationS;
}

class CatInsights {
  const CatInsights({
    required this.catId,
    required this.period,
    required this.offset,
    required this.start,
    required this.end,
    required this.rangeLabel,
    required this.hasEarlier,
    required this.points,
    required this.weight,
    required this.visits,
    required this.minutes,
    required this.timeOfVisit,
    required this.overview,
    required this.summary,
  });

  factory CatInsights.fromJson(Map<String, dynamic> json) {
    final summary = json['summary'];
    return CatInsights(
      catId: json['cat_id'] as int,
      period: InsightPeriod.values.firstWhere(
        (period) => period.code == json['period'],
      ),
      offset: json['offset'] as int,
      start: _date(json['start'])!,
      end: _date(json['end'])!,
      rangeLabel: json['range_label'] as String,
      hasEarlier: json['has_earlier'] as bool,
      points: (json['points'] as List<dynamic>)
          .map(_date)
          .cast<DateTime>()
          .toList(),
      weight: MetricSeries.fromJson(_map(json['weight'])),
      visits: MetricSeries.fromJson(_map(json['visits'])),
      minutes: MetricSeries.fromJson(_map(json['minutes'])),
      timeOfVisit: _list(json['time_of_visit'], VisitDot.fromJson),
      overview: CatSummary.fromJson(_map(json['overview'])),
      summary: summary is String
          ? [summary]
          : (summary as List<dynamic>).cast<String>(),
    );
  }

  final int catId;
  final InsightPeriod period;

  /// 0 is the current period, 1 the one before.
  final int offset;
  final DateTime start;
  final DateTime end;
  final String rangeLabel;

  /// False when there is nothing before this period, so the back arrow stops.
  final bool hasEarlier;

  /// The first day of each point: a day, or a month for the year view.
  final List<DateTime> points;
  final MetricSeries weight;
  final MetricSeries visits;
  final MetricSeries minutes;
  final List<VisitDot> timeOfVisit;

  /// Only the yesterday and week fields are set.
  final CatSummary overview;

  /// The plain sentences for the summary card.
  final List<String> summary;
}

enum Granularity {
  day('day'),
  week('week'),
  month('month');

  const Granularity(this.code);

  final String code;
}

class InsightsPoint {
  const InsightsPoint({
    required this.date,
    required this.visits,
    this.avgVisitS,
    this.cleanCycles,
  });

  factory InsightsPoint.fromJson(Map<String, dynamic> json) => InsightsPoint(
    date: _date(json['date'])!,
    visits: json['visits'] as int,
    avgVisitS: _number(json['avg_visit_s']),
    cleanCycles: json['clean_cycles'] as int?,
  );

  final DateTime date;
  final int visits;
  final double? avgVisitS;
  final int? cleanCycles;
}

class InsightsChange {
  const InsightsChange({this.visitsPct, this.avgVisitPct, this.cleanCyclesPct});

  factory InsightsChange.fromJson(Map<String, dynamic> json) => InsightsChange(
    visitsPct: _number(json['visits_pct']),
    avgVisitPct: _number(json['avg_visit_pct']),
    cleanCyclesPct: _number(json['clean_cycles_pct']),
  );

  final double? visitsPct;
  final double? avgVisitPct;
  final double? cleanCyclesPct;
}

/// The counts for one cat, for all cats together, or for visits with no cat.
class InsightsGroup {
  const InsightsGroup({
    required this.visits,
    required this.visitsPerDay,
    required this.hours,
    required this.series,
    required this.previousVisits,
    required this.change,
    this.avgVisitS,
    this.cleanCycles,
    this.cleanCyclesPerDay,
    this.previousAvgVisitS,
    this.previousCleanCycles,
    this.catId,
    this.name,
    this.colorIndex,
    this.archived = false,
  });

  factory InsightsGroup.fromJson(Map<String, dynamic> json) {
    final previous = _map(json['previous']);
    return InsightsGroup(
      visits: json['visits'] as int,
      visitsPerDay: _number(json['visits_per_day'])!,
      avgVisitS: _number(json['avg_visit_s']),
      cleanCycles: json['clean_cycles'] as int?,
      cleanCyclesPerDay: _number(json['clean_cycles_per_day']),
      hours: (json['hours'] as List<dynamic>).cast<int>(),
      series: _list(json['series'], InsightsPoint.fromJson),
      previousVisits: previous['visits'] as int,
      previousAvgVisitS: _number(previous['avg_visit_s']),
      previousCleanCycles: previous['clean_cycles'] as int?,
      change: InsightsChange.fromJson(_map(json['change'])),
      catId: json['cat_id'] as int?,
      name: json['name'] as String?,
      colorIndex: json['color_index'] as int?,
      archived: json['archived'] as bool? ?? false,
    );
  }

  final int visits;
  final double visitsPerDay;
  final double? avgVisitS;

  /// Only on the overall group, because cycles do not belong to a cat.
  final int? cleanCycles;
  final double? cleanCyclesPerDay;

  /// Visit counts for the hours 0 to 23.
  final List<int> hours;
  final List<InsightsPoint> series;
  final int previousVisits;
  final double? previousAvgVisitS;
  final int? previousCleanCycles;
  final InsightsChange change;

  /// Only on the per-cat groups.
  final int? catId;
  final String? name;
  final int? colorIndex;
  final bool archived;
}

class Insights {
  const Insights({
    required this.start,
    required this.end,
    required this.days,
    required this.granularity,
    required this.previousStart,
    required this.previousEnd,
    required this.overall,
    required this.cats,
    required this.unassigned,
  });

  factory Insights.fromJson(Map<String, dynamic> json) => Insights(
    start: _date(json['start'])!,
    end: _date(json['end'])!,
    days: json['days'] as int,
    granularity: Granularity.values.firstWhere(
      (step) => step.code == json['granularity'],
    ),
    previousStart: _date(json['previous_start'])!,
    previousEnd: _date(json['previous_end'])!,
    overall: InsightsGroup.fromJson(_map(json['overall'])),
    cats: _list(json['cats'], InsightsGroup.fromJson),
    unassigned: InsightsGroup.fromJson(_map(json['unassigned'])),
  );

  final DateTime start;
  final DateTime end;
  final int days;
  final Granularity granularity;
  final DateTime previousStart;
  final DateTime previousEnd;
  final InsightsGroup overall;
  final List<InsightsGroup> cats;
  final InsightsGroup unassigned;
}

class MaintenanceItem {
  const MaintenanceItem({
    required this.kind,
    required this.label,
    required this.intervalDays,
    required this.isDue,
    this.lastDone,
    this.due,
    this.daysUntilDue,
  });

  factory MaintenanceItem.fromJson(Map<String, dynamic> json) =>
      MaintenanceItem(
        kind: json['kind'] as String,
        label: json['label'] as String,
        intervalDays: json['interval_days'] as int,
        lastDone: _date(json['last_done']),
        due: _date(json['due']),
        daysUntilDue: json['days_until_due'] as int?,
        isDue: json['is_due'] as bool,
      );

  /// `filter`, `deep_clean` or `globe_clean`.
  final String kind;
  final String label;
  final int intervalDays;
  final DateTime? lastDone;
  final DateTime? due;

  /// Negative once it is late.
  final int? daysUntilDue;
  final bool isDue;
}

class MaintenanceEntry {
  const MaintenanceEntry({
    required this.id,
    required this.kind,
    required this.label,
    required this.doneOn,
  });

  factory MaintenanceEntry.fromJson(Map<String, dynamic> json) =>
      MaintenanceEntry(
        id: json['id'] as int,
        kind: json['kind'] as String,
        label: json['label'] as String,
        doneOn: _date(json['done_on'])!,
      );

  final int id;
  final String kind;
  final String label;
  final DateTime doneOn;
}

class Maintenance {
  const Maintenance({
    required this.today,
    required this.items,
    required this.log,
  });

  factory Maintenance.fromJson(Map<String, dynamic> json) => Maintenance(
    today: _date(json['today'])!,
    items: _list(json['items'], MaintenanceItem.fromJson),
    log: _list(json['log'], MaintenanceEntry.fromJson),
  );

  final DateTime today;
  final List<MaintenanceItem> items;

  /// Newest first.
  final List<MaintenanceEntry> log;
}

/// The thresholds the alerts use, keyed by the server's field names.
class AlertSettings {
  const AlertSettings({required this.settings, required this.defaults});

  factory AlertSettings.fromJson(Map<String, dynamic> json) => AlertSettings(
    settings: _intMap(json['settings']),
    defaults: _intMap(json['defaults']),
  );

  static Map<String, int> _intMap(Object? value) =>
      _map(value).map((key, number) => MapEntry(key, number as int));

  final Map<String, int> settings;
  final Map<String, int> defaults;

  int get drawerLevelPercent => settings['drawer_level_percent']!;
  int get litterLowPercent => settings['litter_low_percent']!;
  int get noVisitHours => settings['no_visit_hours']!;
  int get filterDays => settings['filter_days']!;
  int get deepCleanDays => settings['deep_clean_days']!;
  int get globeCleanDays => settings['globe_clean_days']!;
}

class ApiClient {
  ApiClient(this.baseUrl, this.accessKey, this._http);

  final String baseUrl;
  final String accessKey;
  final http.Client _http;

  static const _timeout = Duration(seconds: 10);
  // A weekly schedule is fourteen writes, and each one is read back before the next.
  static const _settingsTimeout = Duration(seconds: 120);
  // The robot has to answer a fresh state request before a calibration is saved.
  static const _calibrateTimeout = Duration(seconds: 45);
  static const _transferTimeout = Duration(seconds: 120);

  Map<String, String> get _authorization => {
    'Authorization': 'Bearer $accessKey',
  };

  Map<String, String> get _headers => {
    ..._authorization,
    'Content-Type': 'application/json',
  };

  Uri _uri(String path, [Map<String, Object>? query]) =>
      Uri.parse('$baseUrl$path').replace(
        queryParameters: query?.map((key, value) => MapEntry(key, '$value')),
      );

  Future<http.Response> _answer(
    Future<http.Response> Function() request,
    Duration timeout,
  ) async {
    final http.Response response;
    try {
      response = await request().timeout(timeout);
    } on Exception {
      throw const ApiException("Can't reach the server.");
    } on ArgumentError {
      throw const ApiException("Can't reach the server.");
    }
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return response;
    }
    throw ApiException(_detail(response), status: response.statusCode);
  }

  Future<Object?> _send(
    Future<http.Response> Function() request,
    Duration timeout,
  ) async {
    final response = await _answer(request, timeout);
    return response.body.isEmpty
        ? null
        : _parse(() => jsonDecode(response.body));
  }

  T _parse<T>(T Function() read) {
    try {
      return read();
    } on FormatException {
      throw const UnexpectedResponse();
    } on TypeError {
      throw const UnexpectedResponse();
    } on StateError {
      throw const UnexpectedResponse();
    }
  }

  String _detail(http.Response response) {
    try {
      final body = jsonDecode(response.body);
      if (body is Map && body['detail'] is String) {
        return body['detail'] as String;
      }
    } on FormatException {
      // Fall through: something other than our server answered.
    }
    return 'The server answered with an error (${response.statusCode}).';
  }

  Future<Object?> _get(
    String path, [
    Map<String, Object>? query,
    Duration timeout = _timeout,
  ]) => _send(() => _http.get(_uri(path, query), headers: _headers), timeout);

  Future<Object?> _post(
    String path, {
    Object? body,
    Duration timeout = _timeout,
  }) => _send(
    () => _http.post(
      _uri(path),
      headers: _headers,
      body: body == null ? null : jsonEncode(body),
    ),
    timeout,
  );

  Future<Object?> _patch(
    String path,
    Object body, {
    Duration timeout = _timeout,
  }) => _send(
    () => _http.patch(_uri(path), headers: _headers, body: jsonEncode(body)),
    timeout,
  );

  Future<Object?> _delete(String path, {Duration timeout = _timeout}) =>
      _send(() => _http.delete(_uri(path), headers: _headers), timeout);

  Future<ServerStatus> status() async => _status(await _get('/api/status'));

  Future<List<HistoryEvent>> events({
    int limit = 100,
    String? before,
    List<String> kinds = const [],
  }) async {
    final query = <String, Object>{'limit': limit, 'before': ?before};
    // A repeated parameter cannot live in a map, so the kinds are appended by hand.
    final uri = _uri('/api/events', query);
    final withKinds = uri.replace(
      query: [
        if (uri.query.isNotEmpty) uri.query,
        for (final kind in kinds) 'kind=${Uri.encodeQueryComponent(kind)}',
      ].join('&'),
    );
    final body = await _send(
      () => _http.get(withKinds, headers: _headers),
      _timeout,
    );
    return _parse(() => _list(body, HistoryEvent.fromJson));
  }

  /// Visits, oldest first.
  Future<List<Visit>> visits({
    int days = 30,
    int? catId,
    bool needsCat = false,
  }) async {
    final body = await _get('/api/visits', {
      'days': days,
      'cat_id': ?catId,
      if (needsCat) 'needs_cat': true,
    });
    return _parse(() => _list(body, Visit.fromJson));
  }

  /// Sets or, with [VisitLabel.unknown], clears the label on one visit.
  Future<Visit> labelVisit(int id, VisitLabel label, {int? catId}) async {
    final body = await _post(
      '/api/visits/$id/label',
      body: {'label': label.code, 'cat_id': ?catId},
    );
    return _parse(() => Visit.fromJson(_map(body)));
  }

  Future<List<Cat>> cats({bool includeArchived = false}) async {
    final body = await _get('/api/cats', {
      if (includeArchived) 'include_archived': true,
    });
    return _parse(() => _list(body, Cat.fromJson));
  }

  Future<Cat> cat(int id) async {
    final body = await _get('/api/cats/$id');
    return _parse(() => Cat.fromJson(_map(body)));
  }

  Future<Cat> createCat({
    required String name,
    double? referenceWeightLb,
    String? breed,
    DateTime? birthday,
    CatSex? sex,
  }) async {
    final body = await _post(
      '/api/cats',
      body: {
        'name': name,
        'reference_weight_lb': ?referenceWeightLb,
        'breed': ?breed,
        if (birthday != null) 'birthday': formatDate(birthday),
        if (sex != null) 'sex': sex.code,
      },
    );
    return _parse(() => Cat.fromJson(_map(body)));
  }

  /// Sends only the fields in [patch]. A null value clears `breed`, `birthday`
  /// and `reference_weight_lb`; dates go as `YYYY-MM-DD` strings.
  Future<Cat> updateCat(int id, Map<String, Object?> patch) async {
    final body = await _patch('/api/cats/$id', patch);
    return _parse(() => Cat.fromJson(_map(body)));
  }

  Future<CatSuggestions> catSuggestions() async {
    final body = await _get('/api/cats/suggestions');
    return _parse(() => CatSuggestions.fromJson(_map(body)));
  }

  Future<CatSeries> catSeries(int id, {DateTime? start, DateTime? end}) async {
    final body = await _get('/api/cats/$id/series', {
      if (start != null) 'start': formatDate(start),
      if (end != null) 'end': formatDate(end),
    });
    return _parse(() => CatSeries.fromJson(_map(body)));
  }

  Future<CatInsights> catInsights(
    int id, {
    InsightPeriod period = InsightPeriod.week,
    int offset = 0,
  }) async {
    final body = await _get('/api/cats/$id/insights', {
      'period': period.code,
      'offset': offset,
    });
    return _parse(() => CatInsights.fromJson(_map(body)));
  }

  /// The photo as bytes, because an image widget cannot send the access key.
  Future<Uint8List> photo(int id) async {
    final response = await _answer(
      () => _http.get(_uri('/api/cats/$id/photo'), headers: _authorization),
      _transferTimeout,
    );
    return response.bodyBytes;
  }

  Future<Cat> putPhoto(
    int id,
    Uint8List bytes, {
    String contentType = 'image/jpeg',
  }) async {
    final body = await _send(
      () => _http.put(
        _uri('/api/cats/$id/photo'),
        headers: {..._authorization, 'Content-Type': contentType},
        body: bytes,
      ),
      _transferTimeout,
    );
    return _parse(() => Cat.fromJson(_map(body)));
  }

  Future<void> deletePhoto(int id) async {
    await _delete('/api/cats/$id/photo');
  }

  Future<Insights> insights({
    int? days,
    DateTime? start,
    DateTime? end,
    Granularity? granularity,
  }) async {
    final body = await _get('/api/insights', {
      'days': ?days,
      if (start != null) 'start': formatDate(start),
      if (end != null) 'end': formatDate(end),
      if (granularity != null) 'granularity': granularity.code,
    });
    return _parse(() => Insights.fromJson(_map(body)));
  }

  Future<Maintenance> maintenance({int limit = 50}) async {
    final body = await _get('/api/maintenance', {'limit': limit});
    return _parse(() => Maintenance.fromJson(_map(body)));
  }

  Future<MaintenanceEntry> logMaintenance(
    String kind, {
    DateTime? doneOn,
  }) async {
    final body = await _post(
      '/api/maintenance',
      body: {'kind': kind, if (doneOn != null) 'done_on': formatDate(doneOn)},
    );
    return _parse(() => MaintenanceEntry.fromJson(_map(body)));
  }

  Future<void> deleteMaintenance(int id) async {
    await _delete('/api/maintenance/$id');
  }

  Future<AlertSettings> alertSettings() async {
    final body = await _get('/api/alert-settings');
    return _parse(() => AlertSettings.fromJson(_map(body)));
  }

  Future<AlertSettings> updateAlertSettings(Map<String, int> patch) async {
    final body = await _patch('/api/alert-settings', patch);
    return _parse(() => AlertSettings.fromJson(_map(body)));
  }

  Future<AlertSettings> resetAlertSettings() async {
    final body = await _delete('/api/alert-settings');
    return _parse(() => AlertSettings.fromJson(_map(body)));
  }

  Future<void> action(String name) async {
    await _post('/api/actions/$name');
  }

  /// Saves the current litter reading as `full` or `empty`. Answers with the live status only.
  Future<ServerStatus> calibrateLitter(String point) async => _status(
    await _post(
      '/api/actions/calibrate-litter',
      body: {'point': point},
      timeout: _calibrateTimeout,
    ),
  );

  Future<ServerStatus> clearLitterCalibration() async =>
      _status(await _delete('/api/actions/calibrate-litter'));

  /// Answers with the live status only, without cats.
  Future<ServerStatus> updateSettings(Map<String, Object> patch) async =>
      _status(await _patch('/api/settings', patch, timeout: _settingsTimeout));

  Future<Uint8List> exportVisitsCsv({DateTime? start, DateTime? end}) async {
    final response = await _answer(
      () => _http.get(
        _uri('/api/export/visits.csv', {
          if (start != null) 'start': formatDate(start),
          if (end != null) 'end': formatDate(end),
        }),
        headers: _authorization,
      ),
      _transferTimeout,
    );
    return response.bodyBytes;
  }

  Future<Uint8List> exportDatabase() async {
    final response = await _answer(
      () => _http.get(_uri('/api/export/database'), headers: _authorization),
      _transferTimeout,
    );
    return response.bodyBytes;
  }

  ServerStatus _status(Object? body) =>
      _parse(() => ServerStatus.fromJson(_map(body)));
}

/// `YYYY-MM-DD`, the form every date takes on the wire.
String formatDate(DateTime day) =>
    '${day.year.toString().padLeft(4, '0')}-'
    '${day.month.toString().padLeft(2, '0')}-'
    '${day.day.toString().padLeft(2, '0')}';
