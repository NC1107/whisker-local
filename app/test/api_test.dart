import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/controller.dart';

import 'support/fixtures.dart';

class Recorder {
  final List<http.Request> requests = [];
  http.Response Function(http.Request request) answer = (_) =>
      http.Response('{}', 200);

  ApiClient get api => ApiClient(
    'https://box.lan:8420',
    'secret',
    MockClient((request) async {
      requests.add(request);
      return answer(request);
    }),
  );

  http.Request get last => requests.last;
  Object? get body => jsonDecode(last.body);
}

http.Response json(Object? body, [int status = 200]) =>
    http.Response(jsonEncode(body), status);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late Recorder server;
  setUp(() => server = Recorder());

  group('status', () {
    test('reads everything the home screen needs', () async {
      server.answer = (_) => json({
        ...statusJson(
          robot: {
            ...robotJson(),
            'drawer_full_lockout': true,
            'drawer_last_moved': '2026-11-08T10:00:00.000+00:00',
            'bonnet_removed': true,
            'litter_level_mm': 123.5,
            'litter_source': 'calibrated',
            'litter_calibrated': true,
            'litter_calibration': {'full_mm': 100.0, 'empty_mm': null},
            'hopper': {
              'status': 'ok',
              'level_percent': 40,
              'level_source': 'estimate',
              'fill_raw': 12.5,
              'connected': true,
              'empty': false,
              'last_dispensed': '2026-11-08T09:00:00.000+00:00',
            },
            'empty_cycles': 3,
          },
          cats: [
            catJson(),
            catJson(id: 2, name: 'Rio', colorIndex: 1),
          ],
          demo: true,
          visitsNeedingCat: 2,
        ),
        'latest_visit_needing_cat': {
          'id': 9,
          'at': '2026-11-08T12:00:00.000+00:00',
          'weight_lb': 9.6,
          'duration_s': 40,
          'closest_cat_id': 2,
        },
      });
      final status = await server.api.status();
      final robot = status.robot!;
      expect(status.demo, isTrue);
      expect(status.cats.map((cat) => cat.name), ['Miso', 'Rio']);
      expect(status.catById(2)!.colorIndex, 1);
      expect(status.catById(7), isNull);
      expect(status.visitsNeedingCat, 2);
      expect(status.latestVisitNeedingCat!.closestCatId, 2);
      expect(status.latestVisitNeedingCat!.weightLb, 9.6);
      expect(robot.drawerFullLockout, isTrue);
      expect(robot.drawerLastMoved, isNotNull);
      expect(robot.bonnetRemoved, isTrue);
      expect(robot.litterLevelMm, 123.5);
      expect(robot.litterSource, LitterSource.calibrated);
      expect(robot.litterCalibrated, isTrue);
      expect(robot.litterCalibration.fullMm, 100.0);
      expect(robot.litterCalibration.emptyMm, isNull);
      expect(robot.hopper!.levelPercent, 40);
      expect(robot.hopper!.levelSource, 'estimate');
      expect(robot.emptyCycles, 3);
      expect(server.last.headers['Authorization'], 'Bearer secret');
      expect(server.last.url.toString(), 'https://box.lan:8420/api/status');
    });

    test('an older server without the new fields still reads', () async {
      server.answer = (_) => json({
        'serial': 'S',
        'online': true,
        'broker_connected': true,
        'last_seen': null,
        'robot': robotJson(),
        'alerts': <Object>[],
      });
      final status = await server.api.status();
      expect(status.demo, isFalse);
      expect(status.cats, isEmpty);
      expect(status.robot!.hopper, isNull);
      expect(status.robot!.litterSource, isNull);
    });

    test('litter sources map from the wire names', () {
      expect(LitterSource.parse('default'), LitterSource.fallback);
      expect(LitterSource.parse('reported'), LitterSource.reported);
      expect(LitterSource.parse('nonsense'), isNull);
    });

    test('settings answers keep the cats of the status they replace', () {
      final before = ServerStatus.fromJson(
        statusJson(cats: [catJson()], demo: true, visitsNeedingCat: 3),
      );
      final live = ServerStatus.fromJson({
        'serial': 'S',
        'online': false,
        'broker_connected': true,
        'last_seen': null,
        'robot': null,
        'alerts': <Object>[],
      });
      final merged = before.withLiveFrom(live);
      expect(merged.online, isFalse);
      expect(merged.demo, isTrue);
      expect(merged.cats, hasLength(1));
      expect(merged.visitsNeedingCat, 3);
    });
  });

  group('cats', () {
    test('parse the profile and the summary', () async {
      server.answer = (_) => json([
        {
          ...catJson(hasPhoto: true, photoVersion: 4),
          'breed': 'Tabby',
          'birthday': '2020-03-04',
          'sex': 'spayed_female',
        },
      ]);
      final cat = (await server.api.cats(includeArchived: true)).single;
      expect(server.last.url.queryParameters['include_archived'], 'true');
      expect(cat.breed, 'Tabby');
      expect(cat.birthday, DateTime(2020, 3, 4));
      expect(cat.sex, CatSex.spayedFemale);
      expect(cat.sex.label, 'Spayed female');
      expect(cat.hasPhoto, isTrue);
      expect(cat.photoVersion, 4);
      expect(cat.summary.yesterdayVisits, 2);
      expect(cat.summary.commonVisitHour, 19);
      expect(cat.summary.weightChangePct, 1.0);
    });

    test('a null summary field stays null', () {
      final cat = Cat.fromJson(
        catJson(summary: {'latest_weight_lb': null, 'common_visit_hour': null}),
      );
      expect(cat.summary.latestWeightLb, isNull);
      expect(cat.summary.commonVisitHour, isNull);
    });

    test('a day with no data is not turned into zero visits', () {
      final cat = Cat.fromJson(
        catJson(
          summary: {
            'yesterday_visits': null,
            'week_visits': null,
            'yesterday_minutes': null,
          },
        ),
      );
      expect(cat.summary.yesterdayVisits, isNull);
      expect(cat.summary.weekVisits, isNull);
      expect(cat.summary.yesterdayMinutes, isNull);
    });

    test('a household point and rate can be missing', () {
      final group = InsightsGroup.fromJson({
        'visits': 0,
        'visits_per_day': null,
        'hours': List.filled(24, 0),
        'series': [
          {'date': '2026-11-06', 'visits': null, 'avg_visit_s': null},
        ],
        'previous': {'visits': 0},
        'change': <String, Object?>{},
      });
      expect(group.visitsPerDay, isNull);
      expect(group.series.single.visits, isNull);
    });

    test('an unknown sex is unknown', () {
      expect(CatSex.parse('robot'), CatSex.unknown);
    });

    test('the old weight name still fills the reference weight', () {
      final json = catJson()
        ..remove('reference_weight_lb')
        ..['estimated_weight_lb'] = 8.5;
      expect(Cat.fromJson(json).referenceWeightLb, 8.5);
    });

    test('creating a cat sends only what was given', () async {
      server.answer = (_) => json(catJson(), 201);
      await server.api.createCat(name: 'Miso');
      expect(server.last.method, 'POST');
      expect(server.last.url.path, '/api/cats');
      expect(server.body, {'name': 'Miso'});

      await server.api.createCat(
        name: 'Miso',
        referenceWeightLb: 10.5,
        breed: 'Tabby',
        birthday: DateTime(2021, 2, 3),
        sex: CatSex.neuteredMale,
      );
      expect(server.body, {
        'name': 'Miso',
        'reference_weight_lb': 10.5,
        'breed': 'Tabby',
        'birthday': '2021-02-03',
        'sex': 'neutered_male',
      });
    });

    test('updating can clear a field with null', () async {
      server.answer = (_) => json(catJson());
      await server.api.updateCat(1, {'breed': null, 'name': 'Rio'});
      expect(server.last.method, 'PATCH');
      expect(server.last.url.path, '/api/cats/1');
      expect(server.body, {'breed': null, 'name': 'Rio'});
    });

    test('a name that is taken surfaces the server message', () async {
      server.answer = (_) =>
          json({'detail': 'You already have a cat named Miso.'}, 409);
      await expectLater(
        server.api.createCat(name: 'Miso'),
        throwsA(
          isA<ApiException>()
              .having((error) => error.status, 'status', 409)
              .having(
                (error) => error.message,
                'message',
                'You already have a cat named Miso.',
              ),
        ),
      );
    });

    test('suggestions list the clusters lightest first', () async {
      server.answer = (_) => json({
        'enough_data': true,
        'visits_considered': 14,
        'days_of_data': 5.5,
        'min_visits': 10,
        'min_days': 3,
        'clusters': [
          {'center_lb': 9.1, 'low_lb': 8.8, 'high_lb': 9.4, 'visits': 6},
        ],
      });
      final suggestions = await server.api.catSuggestions();
      expect(server.last.url.path, '/api/cats/suggestions');
      expect(suggestions.enoughData, isTrue);
      expect(suggestions.minDays, 3);
      expect(suggestions.clusters.single.centerLb, 9.1);
    });

    test('the series keeps empty days', () async {
      server.answer = (_) => json({
        'cat_id': 1,
        'timezone': 'UTC',
        'start': '2026-11-07',
        'end': '2026-11-08',
        'days': [
          {
            'date': '2026-11-07',
            'visits': 0,
            'median_weight_lb': null,
            'avg_visit_s': null,
          },
          {
            'date': '2026-11-08',
            'visits': 2,
            'median_weight_lb': 10.1,
            'avg_visit_s': 80.0,
          },
        ],
      });
      final series = await server.api.catSeries(
        1,
        start: DateTime(2026, 11, 7),
        end: DateTime(2026, 11, 8),
      );
      expect(server.last.url.queryParameters, {
        'start': '2026-11-07',
        'end': '2026-11-08',
      });
      expect(series.days.first.medianWeightLb, isNull);
      expect(series.days.last.visits, 2);
    });
  });

  group('photos', () {
    test('are fetched with the key and returned as bytes', () async {
      server.answer = (_) => http.Response.bytes([1, 2, 3], 200);
      final bytes = await server.api.photo(4);
      expect(bytes, [1, 2, 3]);
      expect(server.last.url.path, '/api/cats/4/photo');
      expect(server.last.headers['Authorization'], 'Bearer secret');
    });

    test('are uploaded as the raw picture with its type', () async {
      server.answer = (_) => json(catJson(hasPhoto: true, photoVersion: 2));
      final cat = await server.api.putPhoto(
        1,
        Uint8List.fromList([9, 8, 7]),
        contentType: 'image/png',
      );
      expect(server.last.method, 'PUT');
      expect(server.last.bodyBytes, [9, 8, 7]);
      expect(server.last.headers['Content-Type'], 'image/png');
      expect(server.last.headers['Authorization'], 'Bearer secret');
      expect(cat.photoVersion, 2);
    });

    test('are removed with a delete', () async {
      server.answer = (_) => http.Response('', 204);
      await server.api.deletePhoto(1);
      expect(server.last.method, 'DELETE');
      expect(server.last.url.path, '/api/cats/1/photo');
    });
  });

  group('visits and events', () {
    Map<String, Object?> visit({String label = 'unknown', int? catId}) => {
      'id': 5,
      'cursor': 'c5',
      'at': '2026-11-08T12:00:00.000+00:00',
      'kind': 'visit',
      'detail': {
        'started': '2026-11-08T11:59:00.000+00:00',
        'duration_s': 60,
        'weight_lb': 9.6,
      },
      'label': label,
      'cat_id': catId,
      'label_source': 'user',
    };

    test('visits ask for the days and the filters', () async {
      server.answer = (_) => json([visit(label: 'cat', catId: 2)]);
      final visits = await server.api.visits(days: 7, catId: 2);
      expect(server.last.url.queryParameters, {'days': '7', 'cat_id': '2'});
      final found = visits.single;
      expect(found.label, VisitLabel.cat);
      expect(found.catId, 2);
      expect(found.userLabelled, isTrue);
      expect(found.durationS, 60);
      expect(found.weightLb, 9.6);
      expect(found.startedAt, isNotNull);

      await server.api.visits(needsCat: true);
      expect(server.last.url.queryParameters, {
        'days': '30',
        'needs_cat': 'true',
      });
    });

    test('labelling sends the cat only for a cat', () async {
      server.answer = (_) => json(visit(label: 'cat', catId: 2));
      await server.api.labelVisit(5, VisitLabel.cat, catId: 2);
      expect(server.last.url.path, '/api/visits/5/label');
      expect(server.body, {'label': 'cat', 'cat_id': 2});

      await server.api.labelVisit(5, VisitLabel.notCat);
      expect(server.body, {'label': 'not_cat'});

      await server.api.labelVisit(5, VisitLabel.unknown);
      expect(server.body, {'label': 'unknown'});
    });

    test('events repeat the kind parameter', () async {
      server.answer = (_) => json([
        {
          'id': 1,
          'cursor': 'c1',
          'at': '2026-11-08T12:00:00.000+00:00',
          'kind': 'alert',
          'detail': {
            'key': 'bonnet',
            'raised': false,
            'urgent': true,
            'message': 'Bonnet back on.',
          },
        },
      ]);
      final events = await server.api.events(
        limit: 20,
        before: 'c9',
        kinds: ['alert', 'fault'],
      );
      expect(server.last.url.queryParametersAll['kind'], ['alert', 'fault']);
      expect(server.last.url.queryParameters['limit'], '20');
      expect(server.last.url.queryParameters['before'], 'c9');
      final event = events.single;
      expect(event.id, 1);
      expect(event.raised, isFalse);
      expect(event.urgent, isTrue);
      expect(event.alertKey, 'bonnet');
      expect(event.message, 'Bonnet back on.');
    });

    test('events without kinds send no kind parameter', () async {
      server.answer = (_) => json(<Object>[]);
      await server.api.events();
      expect(server.last.url.queryParametersAll.containsKey('kind'), isFalse);
      expect(server.last.url.queryParameters['limit'], '100');
    });
  });

  group('insights', () {
    test('one cat for a week', () async {
      server.answer = (_) => json({
        'cat_id': 1,
        'period': 'week',
        'offset': 0,
        'timezone': 'UTC',
        'start': '2026-11-01',
        'end': '2026-11-07',
        'range_label': 'November 1 to November 7',
        'has_earlier': true,
        'points': ['2026-11-01', '2026-11-02'],
        'weight': {
          'series': [10.1, null],
          'average': 10.1,
        },
        'visits': {
          'series': [2, 0],
          'average': 1.0,
        },
        'minutes': {
          'series': [3.5, 0],
          'average': 1.75,
        },
        'time_of_visit': [
          {'day': 0, 'hour': 19.5, 'duration_s': 60},
        ],
        'overview': catJson()['summary'],
        'summary': [
          '2 visits yesterday, about average.',
          'Weight is in range.',
        ],
      });
      final insights = await server.api.catInsights(
        1,
        period: InsightPeriod.week,
        offset: 2,
      );
      expect(server.last.url.path, '/api/cats/1/insights');
      expect(server.last.url.queryParameters, {
        'period': 'week',
        'offset': '2',
      });
      expect(insights.rangeLabel, 'November 1 to November 7');
      expect(insights.hasEarlier, isTrue);
      expect(insights.weight.values, [10.1, null]);
      expect(insights.weight.average, 10.1);
      expect(insights.visits.values, [2, 0]);
      expect(insights.minutes.values.first, 3.5);
      expect(insights.timeOfVisit.single.hour, 19.5);
      expect(insights.overview.yesterdayVisits, 2);
      expect(insights.summary, hasLength(2));
      expect(insights.points.first, DateTime(2026, 11, 1));
    });

    test('all cats together', () async {
      Map<String, Object?> group({int? catId}) => {
        if (catId != null) ...{
          'cat_id': catId,
          'name': 'Miso',
          'color_index': 0,
          'archived': false,
        },
        'visits': 10,
        'visits_per_day': 1.43,
        'avg_visit_s': 75.0,
        if (catId == null) ...{
          'clean_cycles': 12,
          'clean_cycles_per_day': 1.71,
        },
        'hours': List.filled(24, 0)..[19] = 4,
        'previous': {
          'visits': 8,
          'visits_per_day': 1.14,
          'avg_visit_s': null,
          if (catId == null) ...{
            'clean_cycles': 9,
            'clean_cycles_per_day': 1.29,
          },
        },
        'change': {
          'visits_pct': 25.0,
          'avg_visit_pct': null,
          if (catId == null) 'clean_cycles_pct': 33.3,
        },
        'series': [
          {
            'date': '2026-11-02',
            'visits': 3,
            'avg_visit_s': 80.0,
            if (catId == null) 'clean_cycles': 4,
          },
        ],
      };
      server.answer = (_) => json({
        'timezone': 'UTC',
        'start': '2026-11-02',
        'end': '2026-11-08',
        'days': 7,
        'granularity': 'day',
        'previous_start': '2026-10-26',
        'previous_end': '2026-11-01',
        'overall': group(),
        'cats': [group(catId: 1)],
        'unassigned': group(catId: null),
      });
      final insights = await server.api.insights(days: 7);
      expect(server.last.url.queryParameters, {'days': '7'});
      expect(insights.granularity, Granularity.day);
      expect(insights.overall.cleanCycles, 12);
      expect(insights.overall.hours[19], 4);
      expect(insights.overall.change.cleanCyclesPct, 33.3);
      expect(insights.overall.previousAvgVisitS, isNull);
      expect(insights.overall.series.single.cleanCycles, 4);
      final cat = insights.cats.single;
      expect(cat.catId, 1);
      expect(cat.name, 'Miso');
      expect(cat.cleanCycles, isNull);
      expect(cat.change.visitsPct, 25.0);
    });

    test('a range is sent as dates', () async {
      server.answer = (_) => json({});
      await expectLater(
        server.api.insights(
          start: DateTime(2026, 1, 1),
          end: DateTime(2026, 6, 30),
          granularity: Granularity.month,
        ),
        throwsA(isA<UnexpectedResponse>()),
      );
      expect(server.last.url.queryParameters, {
        'start': '2026-01-01',
        'end': '2026-06-30',
        'granularity': 'month',
      });
    });
  });

  group('maintenance and alert settings', () {
    test('maintenance lists the jobs and the log', () async {
      server.answer = (_) => json({
        'today': '2026-11-08',
        'items': [
          {
            'kind': 'filter',
            'label': 'Carbon filter',
            'interval_days': 30,
            'last_done': '2026-10-20',
            'due': '2026-11-19',
            'days_until_due': 11,
            'is_due': false,
          },
          {
            'kind': 'deep_clean',
            'label': 'Deep clean',
            'interval_days': 90,
            'last_done': null,
            'due': null,
            'days_until_due': null,
            'is_due': false,
          },
        ],
        'log': [
          {
            'id': 3,
            'kind': 'filter',
            'label': 'Carbon filter',
            'done_on': '2026-10-20',
            'recorded_at': '2026-10-20T08:00:00.000+00:00',
          },
        ],
      });
      final maintenance = await server.api.maintenance(limit: 5);
      expect(server.last.url.queryParameters, {'limit': '5'});
      expect(maintenance.items.first.daysUntilDue, 11);
      expect(maintenance.items.last.lastDone, isNull);
      expect(maintenance.log.single.doneOn, DateTime(2026, 10, 20));
    });

    test('logging a job sends the date only when given', () async {
      server.answer = (_) => json({
        'id': 4,
        'kind': 'filter',
        'label': 'Carbon filter',
        'done_on': '2026-11-08',
        'recorded_at': '2026-11-08T08:00:00.000+00:00',
      }, 201);
      await server.api.logMaintenance('filter');
      expect(server.body, {'kind': 'filter'});
      await server.api.logMaintenance('filter', doneOn: DateTime(2026, 11, 1));
      expect(server.body, {'kind': 'filter', 'done_on': '2026-11-01'});
    });

    test('an entry is deleted by id', () async {
      server.answer = (_) => http.Response('', 204);
      await server.api.deleteMaintenance(4);
      expect(server.last.method, 'DELETE');
      expect(server.last.url.path, '/api/maintenance/4');
    });

    test('alert thresholds read, change and reset', () async {
      final answer = {
        'settings': {
          'drawer_level_percent': 70,
          'litter_low_percent': 30,
          'no_visit_hours': 24,
          'filter_days': 30,
          'deep_clean_days': 90,
          'globe_clean_days': 60,
        },
        'defaults': {
          'drawer_level_percent': 80,
          'litter_low_percent': 30,
          'no_visit_hours': 24,
          'filter_days': 30,
          'deep_clean_days': 90,
          'globe_clean_days': 60,
        },
      };
      server.answer = (_) => json(answer);
      final read = await server.api.alertSettings();
      expect(read.drawerLevelPercent, 70);
      expect(read.defaults['drawer_level_percent'], 80);

      await server.api.updateAlertSettings({'drawer_level_percent': 70});
      expect(server.last.method, 'PATCH');
      expect(server.body, {'drawer_level_percent': 70});

      await server.api.resetAlertSettings();
      expect(server.last.method, 'DELETE');
      expect(server.last.url.path, '/api/alert-settings');
    });
  });

  group('litter and exports', () {
    test('calibrating names the point', () async {
      server.answer = (_) => json(statusJson(robot: robotJson()));
      await server.api.calibrateLitter('full');
      expect(server.last.method, 'POST');
      expect(server.last.url.path, '/api/actions/calibrate-litter');
      expect(server.body, {'point': 'full'});
    });

    test('clearing the calibration deletes it', () async {
      server.answer = (_) => json(statusJson(robot: robotJson()));
      await server.api.clearLitterCalibration();
      expect(server.last.method, 'DELETE');
      expect(server.last.url.path, '/api/actions/calibrate-litter');
    });

    test('the exports come back as bytes with the key', () async {
      server.answer = (_) => http.Response.bytes([65, 66], 200);
      expect(
        await server.api.exportVisitsCsv(
          start: DateTime(2026, 11, 1),
          end: DateTime(2026, 11, 8),
        ),
        [65, 66],
      );
      expect(server.last.url.path, '/api/export/visits.csv');
      expect(server.last.url.queryParameters, {
        'start': '2026-11-01',
        'end': '2026-11-08',
      });
      expect(server.last.headers['Authorization'], 'Bearer secret');

      await server.api.exportDatabase();
      expect(server.last.url.path, '/api/export/database');
    });
  });

  group('controller', () {
    test('a settings change keeps the cats on screen', () async {
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://box',
        'access_key': 'key',
      });
      final fake = FakeServer(
        statusJson(robot: robotJson(), cats: [catJson()], demo: true),
      );
      fake.routes['PATCH /api/settings'] = (_) => json({
        'serial': 'LR4C000001',
        'online': true,
        'broker_connected': true,
        'last_seen': null,
        'robot': robotJson(locked: true),
        'alerts': <Object>[],
      });
      final controller = AppController(httpClient: fake.client);
      addTearDown(controller.dispose);
      await controller.start();
      while (controller.status == null) {
        await Future<void>.delayed(const Duration(milliseconds: 5));
      }
      await controller.updateSettings({'keypad_lockout': true});
      expect(controller.status!.robot!.settings.keypadLockout, isTrue);
      expect(controller.status!.cats, hasLength(1));
      expect(controller.status!.demo, isTrue);
    });

    test(
      'labelling a visit refreshes the count of visits needing a cat',
      () async {
        SharedPreferences.setMockInitialValues({
          'server_url': 'http://box',
          'access_key': 'key',
        });
        final fake = FakeServer(statusJson(visitsNeedingCat: 1));
        fake.routes['POST /api/visits/5/label'] = (_) {
          fake.status = statusJson(visitsNeedingCat: 0);
          return json({
            'id': 5,
            'cursor': 'c5',
            'at': '2026-11-08T12:00:00.000+00:00',
            'kind': 'visit',
            'detail': <String, Object?>{},
            'label': 'cat',
            'cat_id': 1,
            'label_source': 'user',
          });
        };
        final controller = AppController(httpClient: fake.client);
        addTearDown(controller.dispose);
        await controller.start();
        while (controller.status == null) {
          await Future<void>.delayed(const Duration(milliseconds: 5));
        }
        expect(controller.status!.visitsNeedingCat, 1);
        await controller.labelVisit(5, VisitLabel.cat, catId: 1);
        expect(controller.status!.visitsNeedingCat, 0);
      },
    );

    test('adding a cat shows it in the list at once', () async {
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://box',
        'access_key': 'key',
      });
      final fake = FakeServer(statusJson());
      fake.routes['POST /api/cats'] = (_) {
        fake.status = statusJson(cats: [catJson()]);
        return json(catJson(), 201);
      };
      final controller = AppController(httpClient: fake.client);
      addTearDown(controller.dispose);
      await controller.start();
      while (controller.status == null) {
        await Future<void>.delayed(const Duration(milliseconds: 5));
      }
      expect(controller.cats, isEmpty);
      await controller.createCat(name: 'Miso');
      expect(controller.cats.single.name, 'Miso');
    });
  });

  testWidgets('a press waits as long as the server waits for the robot', (
    tester,
  ) async {
    final api = ApiClient(
      'https://box.lan:8420',
      'secret',
      MockClient((_) async {
        await Future<void>.delayed(const Duration(seconds: 25));
        return http.Response('{"sent": true}', 200);
      }),
    );
    Object? failure;
    var finished = false;
    api
        .action('clean')
        .then(
          (_) => finished = true,
          onError: (Object e) {
            failure = e;
          },
        );

    await tester.pump(const Duration(seconds: 26));

    expect(failure, isNull);
    expect(finished, isTrue);
  });
}
