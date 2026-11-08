import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/theme.dart';
import 'package:whisker_local/widgets/common/common.dart';

import '../support/fixtures.dart';

http.Response jsonResponse(Object? body, [int status = 200]) =>
    http.Response(jsonEncode(body), status);

http.Response failure(String detail, int status) =>
    jsonResponse({'detail': detail}, status);

/// A connected controller for a screen pumped alone, without the shell.
Future<AppController> connectedController(
  WidgetTester tester,
  http.Client client,
) async {
  SharedPreferences.setMockInitialValues({
    'server_url': 'http://box',
    'access_key': 'key',
  });
  final controller = AppController(httpClient: client);
  addTearDown(controller.dispose);
  await tester.runAsync(controller.start);
  await untilTrue(tester, () => controller.status != null);
  return controller;
}

Future<void> pumpScreen(
  WidgetTester tester,
  AppController controller,
  Widget screen, {
  Size size = const Size(400, 1600),
  Brightness mode = Brightness.light,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    AppScope(
      controller: controller,
      child: MaterialApp(
        theme: buildTheme(mode),
        home: Scaffold(body: screen),
      ),
    ),
  );
  await tester.pump();
}

/// Lets responses that complete on real time reach the widgets.
Future<void> settle(WidgetTester tester) async {
  await tester.runAsync(
    () => Future<void>.delayed(const Duration(milliseconds: 40)),
  );
  await tester.pump();
  await tester.pump();
}

Map<String, Object?> maintenanceJson({
  int? filterDays = 12,
  int? globeDays = -3,
}) => {
  'today': '2026-11-08',
  'items': [
    {
      'kind': 'filter',
      'label': 'Carbon filter',
      'interval_days': 30,
      'last_done': filterDays == null ? null : '2026-10-21',
      'due': filterDays == null ? null : '2026-11-20',
      'days_until_due': filterDays,
      'is_due': filterDays != null && filterDays <= 0,
    },
    {
      'kind': 'deep_clean',
      'label': 'Deep clean',
      'interval_days': 90,
      'last_done': '2026-09-10',
      'due': '2026-12-19',
      'days_until_due': 41,
      'is_due': false,
    },
    {
      'kind': 'globe_clean',
      'label': 'Globe clean',
      'interval_days': 60,
      'last_done': '2026-09-10',
      'due': '2026-11-05',
      'days_until_due': globeDays,
      'is_due': globeDays != null && globeDays <= 0,
    },
  ],
  'log': <Object?>[],
};
