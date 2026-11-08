import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';

enum AppPhase { starting, needsConnection, ready }

class AppController extends ChangeNotifier with WidgetsBindingObserver {
  AppController({http.Client? httpClient}) : _http = httpClient ?? http.Client();

  static const _pollEvery = Duration(seconds: 3);
  static const _urlPref = 'server_url';
  static const _keyPref = 'access_key';

  final http.Client _http;

  AppPhase phase = AppPhase.starting;
  ServerStatus? status;
  bool unreachable = false;
  String savedUrl = '';
  String? connectError;

  ApiClient? _client;
  Timer? _timer;
  bool _polling = false;

  /// Non-null once [phase] is ready.
  ApiClient get api => _client!;

  Future<void> start() async {
    WidgetsBinding.instance.addObserver(this);
    final prefs = await SharedPreferences.getInstance();
    savedUrl = prefs.getString(_urlPref) ?? (kIsWeb ? Uri.base.origin : '');
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

  static String normalizeUrl(String input) {
    final trimmed = input.trim().replaceFirst(RegExp(r'/+$'), '');
    return trimmed.contains('://') ? trimmed : 'http://$trimmed';
  }

  /// Returns the reason it failed, or null once connected.
  Future<String?> connect(String url, String key) async {
    final normalized = normalizeUrl(url);
    final candidate = ApiClient(normalized, key.trim(), _http);
    try {
      status = await candidate.status();
    } on ApiException catch (error) {
      return error.status == 401 ? 'That access key is wrong.' : "Can't reach a server at $normalized.";
    }
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_urlPref, normalized);
    await prefs.setString(_keyPref, key.trim());
    savedUrl = normalized;
    _useServer(normalized, key.trim());
    return null;
  }

  Future<void> disconnect({String? reason}) async {
    _stopPolling();
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyPref);
    _client = null;
    status = null;
    unreachable = false;
    connectError = reason;
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
    } on ApiException catch (error) {
      if (error.status == 401) {
        _polling = false;
        await disconnect(reason: 'That access key no longer works. Enter the current one.');
        return;
      }
      unreachable = true;
    }
    _polling = false;
    notifyListeners();
  }

  Future<void> action(String name) async {
    await api.action(name);
    await poll();
  }

  Future<void> updateSettings(Map<String, Object> patch) async {
    status = await api.updateSettings(patch);
    notifyListeners();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (_client == null) return;
    if (state == AppLifecycleState.resumed) {
      _startPolling();
    } else if (state == AppLifecycleState.paused || state == AppLifecycleState.hidden) {
      _stopPolling();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _stopPolling();
    super.dispose();
  }
}
