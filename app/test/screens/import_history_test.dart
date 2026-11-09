import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/screens/import_history_card.dart';
import 'package:whisker_local/screens/settings_screen.dart';
import 'package:whisker_local/widgets/common/csv_picker.dart';

import '../support/fixtures.dart';
import 'screen_support.dart';

Map<String, Object?> summaryJson({
  bool dryRun = true,
  String? batchId,
  int visits = 112,
  int? matched,
  int cleanCycles = 112,
  int already = 0,
  int afterLive = 0,
  int unweighed = 17,
  int interrupted = 213,
  int inProgress = 114,
  int unreadable = 0,
}) => {
  'dry_run': dryRun,
  'batch_id': batchId,
  'reference_date': '2026-11-08',
  'rows_read': 667,
  'visits': visits,
  'visits_matched_to_cat': matched,
  'clean_cycles': cleanCycles,
  'first_at': '2026-10-13T12:26:00.000+00:00',
  'last_at': '2026-11-08T23:51:00.000+00:00',
  'already_imported': already,
  'after_live_data': afterLive,
  'live_data_starts_at': null,
  'cat_detections_without_weight': unweighed,
  'cycles_interrupted': interrupted,
  'cycles_in_progress': inProgress,
  'unreadable_rows': unreadable,
};

Map<String, Object?> batchJson({String id = 'aaaaaaaaaaaa'}) => {
  'batch_id': id,
  'imported_at': '2026-11-08T20:00:00.000+00:00',
  'visits': 112,
  'clean_cycles': 112,
  'first_at': '2026-10-13T12:26:00.000+00:00',
  'last_at': '2026-11-08T23:51:00.000+00:00',
};

final _today = DateTime(2026, 11, 8, 20);
final _csv = Uint8List.fromList(utf8.encode('Activity,Timestamp,Value\n'));

class ImportServer {
  ImportServer({this.batches = const []}) {
    server = FakeServer(
      jsonDecode(jsonEncode(statusJson())) as Map<String, Object?>,
    );
    server.routes['GET /api/maintenance'] = (_) =>
        jsonResponse(maintenanceJson());
    server.routes['GET /api/import/batches'] = (_) => jsonResponse(batches);
    server.routes['POST /api/import/whisker-activity'] = (request) {
      final dry = request.url.queryParameters['dry_run'] == 'true';
      if (!dry) {
        batches = [batchJson()];
      }
      return jsonResponse(response(dry));
    };
    server.routes['DELETE /api/import/batches/aaaaaaaaaaaa'] = (_) {
      batches = [];
      return jsonResponse({'batch_id': 'aaaaaaaaaaaa', 'removed': 224});
    };
  }

  late final FakeServer server;
  List<Map<String, Object?>> batches;
  PickedCsv? picked;
  Map<String, Object?> Function(bool dry) response = (dry) =>
      summaryJson(dryRun: dry, batchId: dry ? null : 'aaaaaaaaaaaa');

  List<String> get paths => [
    for (final r in server.requests)
      '${r.method} ${r.url.path}${r.url.hasQuery ? '?${r.url.query}' : ''}',
  ];
}

Future<ImportServer> pumpCard(
  WidgetTester tester, {
  List<Map<String, Object?>> batches = const [],
  String fileName = 'litter_box_activity_2026-11-08.csv',
  Size size = const Size(400, 1200),
  Brightness mode = Brightness.light,
}) async {
  final import = ImportServer(batches: batches);
  import.picked = PickedCsv(_csv, fileName);
  final controller = await connectedController(tester, import.server.client);
  await pumpScreen(
    tester,
    controller,
    SingleChildScrollView(
      child: ImportHistoryCard(
        controller: controller,
        clock: () => _today,
        pickFile: () async => import.picked,
      ),
    ),
    size: size,
    mode: mode,
  );
  await settle(tester);
  return import;
}

Future<void> tapKey(WidgetTester tester, String key) async {
  await tester.tap(find.byKey(ValueKey(key)));
  await tester.pump();
  await settle(tester);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('exportDateFromName', () {
    test('reads the date in the name of an export', () {
      expect(
        exportDateFromName('litter_box_activity_2026-11-08.csv', today: _today),
        DateTime(2026, 11, 8),
      );
    });

    test('ignores names without a real date or with a future one', () {
      expect(exportDateFromName('activity.csv', today: _today), isNull);
      expect(exportDateFromName('a_2026-13-40.csv', today: _today), isNull);
      expect(exportDateFromName('a_2026-02-30.csv', today: _today), isNull);
      expect(exportDateFromName('a_2026-11-09.csv', today: _today), isNull);
    });
  });

  group('importSummaryLines', () {
    test('says what is added and what is left out in plain words', () {
      final lines = importSummaryLines(
        ImportSummary.fromJson(summaryJson(already: 3)),
      );
      expect(lines, [
        '112 visits and 112 clean cycles, Oct 13 to Nov 8',
        '17 skipped, no weight',
        '327 skipped, not finished',
      ]);
    });

    test('says so when there is nothing new', () {
      final lines = importSummaryLines(
        ImportSummary.fromJson(
          summaryJson(visits: 0, cleanCycles: 0, already: 224, unweighed: 0),
        ),
      );
      expect(lines.first, 'Nothing new to import');
    });

    test('uses singular words and mentions rows that were left out', () {
      final lines = importSummaryLines(
        ImportSummary.fromJson(
          summaryJson(
            visits: 1,
            cleanCycles: 0,
            unweighed: 1,
            interrupted: 1,
            inProgress: 0,
            afterLive: 2,
            unreadable: 1,
          ),
        ),
      );
      expect(lines, [
        '1 visit, Oct 13 to Nov 8',
        '1 skipped, no weight',
        '1 skipped, not finished',
        '2 skipped, after live data',
        '1 row unreadable',
      ]);
    });
  });

  group('Import Whisker history', () {
    testWidgets('starts empty with a way to choose a file', (tester) async {
      await pumpCard(tester);
      expect(find.text('Import Whisker history'), findsOneWidget);
      expect(find.text('Nothing imported yet'), findsOneWidget);
      expect(find.text('Choose file'), findsOneWidget);
      expect(find.text('Undo last import'), findsNothing);
    });

    testWidgets('shows the counts first and writes only after a confirm', (
      tester,
    ) async {
      final import = await pumpCard(tester);
      await tapKey(tester, 'import-choose');

      expect(import.paths.where((p) => p.startsWith('POST')), [
        'POST /api/import/whisker-activity?dry_run=true&reference_date=2026-11-08',
      ]);
      expect(
        find.text('112 visits and 112 clean cycles, Oct 13 to Nov 8'),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('import-confirm')), findsOneWidget);

      await tapKey(tester, 'import-confirm');
      final posts = import.paths.where((p) => p.startsWith('POST')).toList();
      expect(
        posts.last,
        'POST /api/import/whisker-activity?reference_date=2026-11-08',
      );
      expect(
        find.text('Imported 112 visits and 112 clean cycles'),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('import-last')), findsOneWidget);
      expect(find.text('112 visits, Oct 13 to Nov 8'), findsOneWidget);
    });

    testWidgets('cancelling after the counts writes nothing', (tester) async {
      final import = await pumpCard(tester);
      await tapKey(tester, 'import-choose');
      await tapKey(tester, 'import-cancel');

      expect(import.paths.where((p) => p.startsWith('POST')), hasLength(1));
      expect(find.byKey(const ValueKey('import-choose')), findsOneWidget);
      expect(find.text('Nothing imported yet'), findsOneWidget);
    });

    testWidgets('closing the chooser changes nothing', (tester) async {
      final import = await pumpCard(tester);
      import.picked = null;
      await tapKey(tester, 'import-choose');
      expect(import.paths.where((p) => p.startsWith('POST')), isEmpty);
      expect(find.byKey(const ValueKey('import-choose')), findsOneWidget);
    });

    testWidgets('a name without a date sends no reference date', (
      tester,
    ) async {
      final import = await pumpCard(tester);
      import.picked = PickedCsv(_csv, 'export.csv');
      await tapKey(tester, 'import-choose');
      expect(
        import.paths.where((p) => p.startsWith('POST')).single,
        'POST /api/import/whisker-activity?dry_run=true',
      );
    });

    testWidgets('a file with nothing new offers no import', (tester) async {
      final import = await pumpCard(tester);
      import.response = (dry) =>
          summaryJson(dryRun: dry, visits: 0, cleanCycles: 0, already: 224);
      await tapKey(tester, 'import-choose');
      expect(find.text('Nothing new to import'), findsOneWidget);
      expect(find.byKey(const ValueKey('import-confirm')), findsNothing);
      expect(find.text('Close'), findsOneWidget);
    });

    testWidgets('shows the server\'s message when the file is refused', (
      tester,
    ) async {
      final import = await pumpCard(tester);
      import.server.routes['POST /api/import/whisker-activity'] = (_) =>
          failure('This is not a Whisker activity export.', 422);
      await tapKey(tester, 'import-choose');
      expect(
        find.text('This is not a Whisker activity export.'),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('import-choose')), findsOneWidget);
    });

    testWidgets('keeps the counts and says so when the write fails', (
      tester,
    ) async {
      final import = await pumpCard(tester);
      await tapKey(tester, 'import-choose');
      import.server.routes['POST /api/import/whisker-activity'] = (_) =>
          failure('Something went wrong.', 500);
      await tapKey(tester, 'import-confirm');
      expect(find.text('Something went wrong.'), findsOneWidget);
      expect(find.byKey(const ValueKey('import-confirm')), findsOneWidget);
    });

    testWidgets('refuses a file that is too big without sending it', (
      tester,
    ) async {
      final import = await pumpCard(tester);
      import.picked = PickedCsv(Uint8List(maxImportBytes + 1), 'big.csv');
      await tapKey(tester, 'import-choose');
      expect(find.text('File too big for a Whisker export'), findsOneWidget);
      expect(import.paths.where((p) => p.startsWith('POST')), isEmpty);
    });

    testWidgets('earlier imports that do not load can be retried', (
      tester,
    ) async {
      final import = ImportServer();
      import.server.routes['GET /api/import/batches'] = (_) =>
          failure('Busy.', 503);
      final controller = await connectedController(
        tester,
        import.server.client,
      );
      await pumpScreen(
        tester,
        controller,
        ImportHistoryCard(controller: controller, clock: () => _today),
      );
      await settle(tester);
      expect(
        find.textContaining('Earlier imports did not load'),
        findsOneWidget,
      );

      import.server.routes['GET /api/import/batches'] = (_) =>
          jsonResponse([batchJson()]);
      await tapKey(tester, 'import-retry');
      expect(find.byKey(const ValueKey('import-last')), findsOneWidget);
    });

    testWidgets('undoes the last import after a confirm', (tester) async {
      final import = await pumpCard(tester, batches: [batchJson()]);
      expect(find.text('112 visits, Oct 13 to Nov 8'), findsOneWidget);

      await tapKey(tester, 'import-undo');
      expect(find.text('Undo last import?'), findsOneWidget);
      expect(import.paths.where((p) => p.startsWith('DELETE')), isEmpty);

      await tester.tap(find.text('Undo import'));
      await tester.pump();
      await settle(tester);
      expect(
        import.paths.where((p) => p.startsWith('DELETE')).single,
        'DELETE /api/import/batches/aaaaaaaaaaaa',
      );
      expect(find.text('Import removed'), findsOneWidget);
      expect(find.text('Nothing imported yet'), findsOneWidget);
    });

    testWidgets('keeping the import when the confirm is cancelled', (
      tester,
    ) async {
      final import = await pumpCard(tester, batches: [batchJson()]);
      await tapKey(tester, 'import-undo');
      await tester.tap(find.text('Cancel'));
      await tester.pump();
      expect(import.paths.where((p) => p.startsWith('DELETE')), isEmpty);
      expect(find.byKey(const ValueKey('import-last')), findsOneWidget);
    });

    testWidgets('lays out on a narrow phone in dark mode', (tester) async {
      await pumpCard(
        tester,
        batches: [batchJson()],
        size: const Size(320, 1000),
        mode: Brightness.dark,
      );
      expect(tester.takeException(), isNull);
      expect(find.text('Undo last import'), findsOneWidget);
    });
  });

  testWidgets('Settings has the import entry', (tester) async {
    final import = ImportServer();
    final controller = await connectedController(tester, import.server.client);
    await pumpScreen(
      tester,
      controller,
      SettingsScreen(controller: controller, version: () async => '1.0.0'),
      size: const Size(400, 3000),
    );
    await settle(tester);
    expect(find.text('Import Whisker history'), findsOneWidget);
    expect(find.text('Choose file'), findsOneWidget);
  });
}
