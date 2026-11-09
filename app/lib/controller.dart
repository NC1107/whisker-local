import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'photos.dart';

enum AppPhase { starting, needsConnection, ready }

class AppController extends ChangeNotifier with WidgetsBindingObserver {
  AppController({http.Client? httpClient})
    : _http = httpClient ?? http.Client() {
    photos = PhotoCache((id) => _connected.photo(id));
  }

  static const _pollEvery = Duration(seconds: 3);
  static const _urlPref = 'server_url';
  static const _keyPref = 'access_key';
  static const _seenAlertsPref = 'seen_alerts';

  final http.Client _http;

  late final PhotoCache photos;

  AppPhase phase = AppPhase.starting;
  ServerStatus? status;
  bool unreachable = false;
  String savedUrl = '';
  String? connectError;

  ApiClient? _client;
  Timer? _timer;
  bool _polling = false;
  bool _disposed = false;
  Set<String> _seenAlerts = {};

  /// Non-null once [phase] is ready.
  ApiClient get api => _client!;

  ApiClient get _connected {
    final client = _client;
    if (client == null) throw const ApiException('Not connected to a server.');
    return client;
  }

  /// The cats that aren't archived, in the order they were made.
  List<Cat> get cats => status?.cats ?? const [];

  /// True while an alert is active that the person has not looked at yet.
  bool get hasUnreadAlerts =>
      status?.alerts.any((alert) => !_seenAlerts.contains(alert.key)) ?? false;

  int get unreadAlertCount =>
      status?.alerts
          .where((alert) => !_seenAlerts.contains(alert.key))
          .length ??
      0;

  /// The web app is served by the server itself, so its own origin is the
  /// address, whichever scheme it was reached over. Elsewhere the saved
  /// address is used.
  static String serverUrlFor({
    required bool web,
    required Uri base,
    String? saved,
  }) => web ? base.origin : (saved ?? '');

  Future<void> start() async {
    WidgetsBinding.instance.addObserver(this);
    final prefs = await SharedPreferences.getInstance();
    savedUrl = serverUrlFor(
      web: kIsWeb,
      base: Uri.base,
      saved: prefs.getString(_urlPref),
    );
    _seenAlerts = (prefs.getStringList(_seenAlertsPref) ?? const []).toSet();
    final key = prefs.getString(_keyPref);
    if (key == null || savedUrl.isEmpty) {
      phase = AppPhase.needsConnection;
      notifyListeners();
      return;
    }
    _useServer(savedUrl, key);
  }

  void _useServer(String url, String key) {
    _client = ApiClient(url, key, _http);
    phase = AppPhase.ready;
    connectError = null;
    notifyListeners();
    _startPolling();
  }

  /// The address with a scheme, https unless the person typed another one.
  static String normalizeUrl(String input) => candidateUrls(input).first;

  /// What to try for an address as typed: as given when it has a scheme. Otherwise https first,
  /// and plain http only for a private address, since the access key travels in every request.
  static List<String> candidateUrls(String input) {
    final trimmed = input.trim().replaceFirst(RegExp(r'/+$'), '');
    if (trimmed.contains('://')) return [trimmed];
    return ['https://$trimmed', if (_isPrivateHost(trimmed)) 'http://$trimmed'];
  }

  static bool _isPrivateHost(String address) {
    final host = Uri.tryParse('//$address')?.host.toLowerCase() ?? '';
    if (host.isEmpty) return false;
    if (!host.contains('.') ||
        host.endsWith('.local') ||
        host.endsWith('.lan') ||
        host.endsWith('.home.arpa')) {
      return true;
    }
    final octets = host.split('.').map(int.tryParse).toList();
    if (octets.length != 4 || octets.any((o) => o == null || o > 255)) {
      return false;
    }
    final [a, b, ...] = octets.cast<int>();
    return a == 10 ||
        a == 127 ||
        (a == 172 && b >= 16 && b <= 31) ||
        (a == 192 && b == 168) ||
        (a == 169 && b == 254);
  }

  /// Returns the reason it failed, or null once connected.
  Future<String?> connect(String url, String key) async {
    final trimmedKey = key.trim();
    var sawForeignServer = false;
    for (final candidate in candidateUrls(url)) {
      try {
        status = await ApiClient(candidate, trimmedKey, _http).status();
      } on ApiException catch (error) {
        if (error.status == 401) return 'That access key is wrong.';
        sawForeignServer |= error is UnexpectedResponse;
        continue;
      }
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_urlPref, candidate);
      await prefs.setString(_keyPref, trimmedKey);
      savedUrl = candidate;
      _useServer(candidate, trimmedKey);
      return null;
    }
    if (sawForeignServer) return 'That address is not a litter box server.';
    return "Can't reach a server at ${url.trim()}.";
  }

  Future<void> disconnect({String? reason}) async {
    _stopPolling();
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyPref);
    _client = null;
    status = null;
    unreachable = false;
    connectError = reason;
    photos.clear();
    phase = AppPhase.needsConnection;
    notifyListeners();
  }

  void _startPolling() {
    _timer?.cancel();
    unawaited(poll());
    _timer = Timer.periodic(_pollEvery, (_) => unawaited(poll()));
  }

  void _stopPolling() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> poll() async {
    final client = _client;
    if (_polling || client == null) return;
    _polling = true;
    try {
      status = await client.status();
      unreachable = false;
      _forgetClearedAlerts();
    } on ApiException catch (error) {
      if (error.status == 401) {
        await disconnect(
          reason: 'That access key no longer works. Enter the current one.',
        );
        return;
      }
      unreachable = true;
    } finally {
      _polling = false;
    }
    if (!_disposed) notifyListeners();
  }

  // An alert that clears and comes back is news again.
  void _forgetClearedAlerts() {
    final active = {for (final alert in status!.alerts) alert.key};
    final kept = _seenAlerts.intersection(active);
    if (kept.length != _seenAlerts.length) {
      _seenAlerts = kept;
      unawaited(_saveSeenAlerts());
    }
  }

  Future<void> _saveSeenAlerts() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(_seenAlertsPref, _seenAlerts.toList());
  }

  /// Call when the person opens the alerts, to clear the dot on the bell.
  Future<void> markAlertsSeen() async {
    final alerts = status?.alerts ?? const [];
    _seenAlerts = {..._seenAlerts, for (final alert in alerts) alert.key};
    notifyListeners();
    await _saveSeenAlerts();
  }

  Future<void> action(String name) async {
    await _connected.action(name);
    await poll();
  }

  Future<void> updateSettings(Map<String, Object> patch) async {
    _useLive(await _connected.updateSettings(patch));
  }

  /// Saves the robot's current litter reading as `full` or `empty`.
  Future<void> calibrateLitter(String point) async {
    _useLive(await _connected.calibrateLitter(point));
  }

  Future<void> clearLitterCalibration() async {
    _useLive(await _connected.clearLitterCalibration());
  }

  void _useLive(ServerStatus live) {
    status = status?.withLiveFrom(live) ?? live;
    notifyListeners();
  }

  Future<Cat> createCat({
    required String name,
    double? referenceWeightLb,
    String? breed,
    DateTime? birthday,
    CatSex? sex,
  }) async {
    final cat = await _connected.createCat(
      name: name,
      referenceWeightLb: referenceWeightLb,
      breed: breed,
      birthday: birthday,
      sex: sex,
    );
    await poll();
    return cat;
  }

  /// See [ApiClient.updateCat] for the fields. Refreshes the cats afterwards.
  Future<Cat> updateCat(int id, Map<String, Object?> patch) async {
    final cat = await _connected.updateCat(id, patch);
    await poll();
    return cat;
  }

  Future<Cat> setCatPhoto(
    int id,
    Uint8List bytes, {
    String contentType = 'image/jpeg',
  }) async {
    final cat = await _connected.putPhoto(id, bytes, contentType: contentType);
    await poll();
    return cat;
  }

  Future<void> removeCatPhoto(int id) async {
    await _connected.deletePhoto(id);
    await poll();
  }

  /// Labels one visit and refreshes the count of visits that still need a cat.
  Future<Visit> labelVisit(int id, VisitLabel label, {int? catId}) async {
    final visit = await _connected.labelVisit(id, label, catId: catId);
    await poll();
    return visit;
  }

  Future<ImportSummary> importWhiskerActivity(
    Uint8List csv, {
    required bool dryRun,
    DateTime? referenceDate,
  }) async {
    final summary = await _connected.importWhiskerActivity(
      csv,
      dryRun: dryRun,
      referenceDate: referenceDate,
    );
    if (!dryRun) await poll();
    return summary;
  }

  Future<List<ImportBatch>> importBatches() => _connected.importBatches();

  Future<void> undoImport(String batchId) async {
    await _connected.undoImport(batchId);
    await poll();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (_client == null) return;
    if (state == AppLifecycleState.resumed) {
      _startPolling();
    } else if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden) {
      _stopPolling();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    WidgetsBinding.instance.removeObserver(this);
    _stopPolling();
    super.dispose();
  }
}
