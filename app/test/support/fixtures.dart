import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/main.dart';

Map<String, Object?> robotJson({
  String status = 'ready',
  String label = 'Ready',
  bool cleaning = false,
  bool locked = false,
}) => {
  'status': status,
  'status_label': label,
  'is_cleaning': cleaning,
  'cat_present': false,
  'drawer_percent': 35,
  'drawer_is_estimate': false,
  'drawer_full': false,
  'litter_percent': 62,
  'clean_cycles': 1284,
  'wifi_rssi': -54,
  'esp_firmware': '1.1.75',
  'pic_firmware': '10500',
  'last_visit_at': '2026-11-08T12:00:00.000+00:00',
  'last_visit_duration_s': 35,
  'last_weight_lb': 9.42,
  'settings': {
    'night_light_mode': 'auto',
    'night_light_brightness': 50,
    'clean_cycle_wait_minutes': 7,
    'keypad_lockout': locked,
    'sleep_enabled': false,
    'sleep_minute': 1320,
    'wake_minute': 360,
  },
};

Map<String, Object?> statusJson({
  bool online = true,
  Map<String, Object?>? robot,
  List<Map<String, Object?>> alerts = const [],
  List<Map<String, Object?>> cats = const [],
  bool demo = false,
  int visitsNeedingCat = 0,
}) => {
  'serial': 'LR4C000001',
  'online': online,
  'broker_connected': true,
  'last_seen': '2026-11-08T12:00:00.000+00:00',
  'robot': robot,
  'alerts': alerts,
  'demo': demo,
  'cats': cats,
  'visits_needing_cat': visitsNeedingCat,
  'latest_visit_needing_cat': null,
};

Map<String, Object?> catJson({
  int id = 1,
  String name = 'Miso',
  int colorIndex = 0,
  bool hasPhoto = false,
  int photoVersion = 0,
  Map<String, Object?> summary = const {},
}) => {
  'id': id,
  'name': name,
  'color_index': colorIndex,
  'breed': null,
  'birthday': null,
  'sex': 'unknown',
  'reference_weight_lb': 10.2,
  'estimated_weight_lb': 10.2,
  'has_photo': hasPhoto,
  'photo_version': photoVersion,
  'archived': false,
  'created_at': '2026-11-01T12:00:00.000+00:00',
  'summary': {
    'latest_weight_lb': 10.1,
    'usual_weight_lb': 10.0,
    'visits_today': 1,
    'visits_per_day': 2.5,
    'avg_visit_s': 90.0,
    'weight_change_pct': 1.0,
    'last_visit_at': '2026-11-08T12:00:00.000+00:00',
    'yesterday_weight_lb': 10.2,
    'yesterday_visits': 2,
    'yesterday_minutes': 3.0,
    'week_visits': 11,
    'common_visit_hour': 19,
    ...summary,
  },
};

/// Answers like the server. Add to [routes] to answer more paths, keyed like
/// `GET /api/cats`; every request that reaches a route is kept in [requests].
class FakeServer {
  FakeServer(this.status);

  Map<String, Object?> status;
  final List<String> posts = [];
  final List<http.Request> requests = [];
  final Map<String, http.Response Function(http.Request request)> routes = {};

  http.Client get client => MockClient((request) async {
    if (request.headers['Authorization'] != 'Bearer key') {
      return http.Response(jsonEncode({'detail': 'Wrong access key'}), 401);
    }
    requests.add(request);
    final route = routes['${request.method} ${request.url.path}'];
    if (route != null) return route(request);
    if (request.url.path == '/api/status') {
      return http.Response(jsonEncode(status), 200);
    }
    if (request.method == 'POST') {
      posts.add(request.url.path);
      return http.Response('{"sent":true}', 202);
    }
    return http.Response('[]', 200);
  });
}

/// Starts the whole app against [server] on a phone-sized window, or the
/// [size] given.
Future<AppController> pumpApp(
  WidgetTester tester,
  FakeServer server, {
  Size size = const Size(400, 900),
  Map<String, Object> prefs = const {},
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  SharedPreferences.setMockInitialValues({
    'server_url': 'http://10.0.0.100:8420',
    'access_key': 'key',
    ...prefs,
  });
  final controller = AppController(httpClient: server.client);
  addTearDown(controller.dispose);
  await tester.runAsync(controller.start);
  await tester.pumpWidget(LitterBoxApp(controller: controller));
  await untilTrue(tester, () => controller.status != null);
  return controller;
}

/// HTTP in these tests completes on real time, which the test clock does not advance.
Future<void> untilTrue(WidgetTester tester, bool Function() done) async {
  await tester.runAsync(() async {
    for (var attempt = 0; attempt < 200 && !done(); attempt++) {
      await Future<void>.delayed(const Duration(milliseconds: 5));
    }
  });
  await tester.pump();
}
