import 'dart:convert';

import 'package:http/http.dart' as http;

class ApiException implements Exception {
  const ApiException(this.message, {this.status});

  final String message;
  final int? status;

  @override
  String toString() => message;
}

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

class RobotView {
  const RobotView({
    required this.status,
    required this.statusLabel,
    required this.isCleaning,
    required this.catPresent,
    required this.drawerIsEstimate,
    required this.drawerFull,
    required this.settings,
    this.drawerPercent,
    this.litterPercent,
    this.cleanCycles,
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
        settings: RobotSettings.fromJson(json['settings'] as Map<String, dynamic>),
        drawerPercent: json['drawer_percent'] as int?,
        litterPercent: json['litter_percent'] as int?,
        cleanCycles: json['clean_cycles'] as int?,
        wifiRssi: json['wifi_rssi'] as int?,
        espFirmware: json['esp_firmware'] as String?,
        picFirmware: json['pic_firmware'] as String?,
        lastVisitAt: _time(json['last_visit_at']),
        lastVisitDurationS: json['last_visit_duration_s'] as int?,
        lastWeightLb: (json['last_weight_lb'] as num?)?.toDouble(),
      );

  final String? status;
  final String statusLabel;
  final bool isCleaning;
  final bool catPresent;
  final bool drawerIsEstimate;
  final bool drawerFull;
  final RobotSettings settings;
  final int? drawerPercent;
  final int? litterPercent;
  final int? cleanCycles;
  final int? wifiRssi;
  final String? espFirmware;
  final String? picFirmware;
  final DateTime? lastVisitAt;
  final int? lastVisitDurationS;
  final double? lastWeightLb;
}

class RobotAlert {
  const RobotAlert({required this.key, required this.urgent, required this.message});

  factory RobotAlert.fromJson(Map<String, dynamic> json) => RobotAlert(
        key: json['key'] as String,
        urgent: json['urgent'] as bool,
        message: json['message'] as String,
      );

  final String key;
  final bool urgent;
  final String message;
}

class ServerStatus {
  const ServerStatus({
    required this.serial,
    required this.online,
    required this.brokerConnected,
    required this.alerts,
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
            : RobotView.fromJson(json['robot'] as Map<String, dynamic>),
        alerts: (json['alerts'] as List<dynamic>)
            .map((alert) => RobotAlert.fromJson(alert as Map<String, dynamic>))
            .toList(),
      );

  final String serial;
  final bool online;
  final bool brokerConnected;
  final DateTime? lastSeen;
  final RobotView? robot;
  final List<RobotAlert> alerts;
}

class HistoryEvent {
  const HistoryEvent({
    required this.cursor,
    required this.at,
    required this.kind,
    required this.detail,
  });

  factory HistoryEvent.fromJson(Map<String, dynamic> json) => HistoryEvent(
        cursor: json['at'] as String,
        at: _time(json['at'])!,
        kind: json['kind'] as String,
        detail: json['detail'] as Map<String, dynamic>,
      );

  /// The server's own timestamp text, sent back unchanged when asking for older events.
  final String cursor;
  final DateTime at;
  final String kind;
  final Map<String, dynamic> detail;

  double? get weightLb => (detail['weight_lb'] as num?)?.toDouble();
  int? get durationS => detail['duration_s'] as int?;
}

DateTime? _time(Object? value) =>
    value == null ? null : DateTime.parse(value as String).toLocal();

class ApiClient {
  ApiClient(this.baseUrl, this.accessKey, this._http);

  final String baseUrl;
  final String accessKey;
  final http.Client _http;

  static const _timeout = Duration(seconds: 10);
  // A weekly schedule is fourteen writes, and each one is read back before the next.
  static const _settingsTimeout = Duration(seconds: 120);

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $accessKey',
        'Content-Type': 'application/json',
      };

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse('$baseUrl$path').replace(queryParameters: query);

  Future<Object?> _send(Future<http.Response> Function() request, Duration timeout) async {
    final http.Response response;
    try {
      response = await request().timeout(timeout);
    } on Exception {
      throw const ApiException("Can't reach the server.");
    }
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return response.body.isEmpty ? null : jsonDecode(response.body);
    }
    throw ApiException(_detail(response), status: response.statusCode);
  }

  String _detail(http.Response response) {
    try {
      final body = jsonDecode(response.body);
      if (body is Map && body['detail'] is String) return body['detail'] as String;
    } on FormatException {
      // Fall through: something other than our server answered.
    }
    return 'The server answered with an error (${response.statusCode}).';
  }

  Future<ServerStatus> status() async {
    final body = await _send(() => _http.get(_uri('/api/status'), headers: _headers), _timeout);
    return ServerStatus.fromJson(body! as Map<String, dynamic>);
  }

  Future<List<HistoryEvent>> events({int limit = 100, String? before}) async {
    final query = {'limit': '$limit', 'before': ?before};
    final body =
        await _send(() => _http.get(_uri('/api/events', query), headers: _headers), _timeout);
    return _events(body);
  }

  Future<List<HistoryEvent>> visits({required int days}) async {
    final body = await _send(
      () => _http.get(_uri('/api/visits', {'days': '$days'}), headers: _headers),
      _timeout,
    );
    return _events(body);
  }

  Future<void> action(String name) async {
    await _send(() => _http.post(_uri('/api/actions/$name'), headers: _headers), _timeout);
  }

  Future<ServerStatus> updateSettings(Map<String, Object> patch) async {
    final body = await _send(
      () => _http.patch(_uri('/api/settings'), headers: _headers, body: jsonEncode(patch)),
      _settingsTimeout,
    );
    return ServerStatus.fromJson(body! as Map<String, dynamic>);
  }

  List<HistoryEvent> _events(Object? body) => (body! as List<dynamic>)
      .map((event) => HistoryEvent.fromJson(event as Map<String, dynamic>))
      .toList();
}
