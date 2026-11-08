import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/main.dart';
import 'package:whisker_local/screens/add_cat_screen.dart';
import 'package:whisker_local/screens/cats_screen.dart';
import 'package:whisker_local/screens/who_was_this_sheet.dart';
import 'package:whisker_local/shell.dart';
import 'package:whisker_local/theme.dart';
import 'package:whisker_local/widgets/common/common.dart';

import 'support/fixtures.dart';

const phone = Size(400, 1400);

Map<String, Object?> pendingVisit({
  int id = 7,
  double? weight = 9.6,
  int? closest = 1,
}) => {
  'id': id,
  'at': DateTime.now().toIso8601String(),
  'weight_lb': weight,
  'duration_s': 35,
  'closest_cat_id': closest,
};

Map<String, Object?> twoCats({int needing = 0, Map<String, Object?>? latest}) =>
    statusJson(
      robot: robotJson(),
      visitsNeedingCat: needing,
      cats: [
        catJson(
          name: 'Miso',
          summary: {'latest_weight_lb': 9.9, 'usual_weight_lb': 10.1},
        ),
        catJson(
          id: 2,
          name: 'Pepper',
          colorIndex: 1,
          summary: {'latest_weight_lb': 8.7, 'usual_weight_lb': 9.0},
        ),
      ],
    )..['latest_visit_needing_cat'] = latest;

Finder inSheet(String text) => find.descendant(
  of: find.byType(WhoWasThisSheet),
  matching: find.text(text),
);

Finder hint(String text) => find.byWidgetPredicate(
  (widget) => widget is TextField && widget.decoration?.hintText == text,
);

Map<String, Object?> suggestions({bool enough = true}) => {
  'enough_data': enough,
  'visits_considered': 55,
  'days_of_data': 9.0,
  'min_visits': 20,
  'min_days': 3.0,
  'clusters': enough
      ? [
          {'center_lb': 10.1, 'low_lb': 9.8, 'high_lb': 10.4, 'visits': 31},
          {'center_lb': 9.0, 'low_lb': 8.7, 'high_lb': 9.3, 'visits': 1},
        ]
      : [],
};

http.Response json(Object? body, [int status = 200]) => http.Response(
  jsonEncode(body),
  status,
  headers: {'content-type': 'application/json'},
);

FakeServer serverWith(Map<String, Object?> status) {
  final server = FakeServer(status);
  server.routes['GET /api/cats/suggestions'] = (_) => json(suggestions());
  return server;
}

Future<void> openCatsTab(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('tab-cats')));
  await tester.pumpAndSettle();
}

Future<void> scrollTo(WidgetTester tester, Finder target) async {
  await tester.ensureVisible(target);
  await tester.pumpAndSettle();
}

NavigatorState navigatorOf(WidgetTester tester) =>
    tester.state<NavigatorState>(find.byType(Navigator).first);

http.Request lastRequest(FakeServer server, String method, String path) =>
    server.requests.lastWhere((r) => r.method == method && r.url.path == path);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('describing a cat', () {
    test('age reads in years and months', () {
      final now = DateTime(2026, 11, 8);
      expect(ageText(DateTime(2022, 4, 8), now), '4 yrs 7 mos');
      expect(ageText(DateTime(2025, 8, 8), now), '1 yr 3 mos');
      expect(ageText(DateTime(2025, 11, 8), now), '1 yr');
      expect(ageText(DateTime(2025, 10, 8), now), '1 yr 1 mo');
      expect(ageText(DateTime(2026, 4, 20), now), '6 mos');
      expect(ageText(DateTime(2026, 10, 8), now), '1 mo');
      expect(ageText(DateTime(2026, 11, 1), now), 'Under 1 mo');
      expect(ageText(DateTime(2027, 1, 1), now), isNull);
    });

    test('the line under the name leaves out what is not known', () {
      final now = DateTime(2026, 11, 8);
      Cat cat(Map<String, Object?> fields) =>
          Cat.fromJson(catJson()..addAll(fields));
      expect(catAbout(cat({}), now), '');
      expect(
        catAbout(
          cat({
            'breed': 'American Shorthair',
            'sex': 'spayed_female',
            'birthday': '2022-04-08',
          }),
          now,
        ),
        'American Shorthair, female, 4 yrs 7 mos',
      );
      expect(catAbout(cat({'breed': '  '}), now), '');
      expect(catAbout(cat({'sex': 'male'}), now), 'male');
    });

    test('a visit reads as weight, time spent and when', () {
      final now = DateTime(2026, 11, 8, 12);
      UnlabelledVisit visit(Map<String, Object?> fields) =>
          UnlabelledVisit.fromJson({
            'id': 1,
            'at': DateTime(2026, 11, 8, 3, 42).toIso8601String(),
            'weight_lb': 9.6,
            'duration_s': 35,
            ...fields,
          });
      expect(visitSummary(visit({}), now), '9.6 lb for 35 s, today at 3:42 AM');
      expect(
        visitSummary(visit({'duration_s': null}), now),
        '9.6 lb, today at 3:42 AM',
      );
      expect(
        visitSummary(visit({'weight_lb': null}), now),
        'A visit of 35 s, today at 3:42 AM',
      );
      expect(
        visitSummary(
          visit({'at': DateTime(2026, 11, 7, 20, 5).toIso8601String()}),
          now,
        ),
        '9.6 lb for 35 s, yesterday at 8:05 PM',
      );
      expect(
        visitSummary(
          visit({'at': DateTime(2026, 11, 2, 20, 5).toIso8601String()}),
          now,
        ),
        '9.6 lb for 35 s, Nov 2 at 8:05 PM',
      );
    });
  });

  group('the cats list', () {
    testWidgets('shows a card for each cat with what the API knows', (
      tester,
    ) async {
      final server = serverWith(twoCats());
      server.status['cats'] = [
        catJson(name: 'Miso', summary: {'latest_weight_lb': 9.9})..addAll({
          'breed': 'American Shorthair',
          'sex': 'spayed_female',
          'birthday': '2022-04-08',
        }),
        catJson(id: 2, name: 'Pepper', summary: {'latest_weight_lb': null}),
      ];
      await pumpApp(tester, server, size: phone);
      await openCatsTab(tester);

      expect(find.text('Miso'), findsOneWidget);
      expect(find.text('Pepper'), findsOneWidget);
      expect(find.textContaining('American Shorthair, female'), findsOneWidget);
      expect(find.text('Last weight: 9.9 lb'), findsOneWidget);
      expect(find.text('No visits yet'), findsOneWidget);
      expect(find.text('Add a cat'), findsOneWidget);
      expect(find.textContaining('needs a cat'), findsNothing);
    });

    testWidgets('says so when there are no cats and still offers to add', (
      tester,
    ) async {
      await pumpApp(tester, serverWith(statusJson(robot: null)), size: phone);
      await openCatsTab(tester);

      expect(find.text('No cats yet'), findsOneWidget);
      expect(find.text('Add a cat'), findsOneWidget);
    });

    testWidgets('sits cards side by side on a wide window', (tester) async {
      await pumpApp(tester, serverWith(twoCats()), size: const Size(1280, 900));
      await tester.tap(find.byKey(const ValueKey('tab-cats')));
      await tester.pumpAndSettle();

      final miso = tester.getTopLeft(find.text('Miso'));
      final pepper = tester.getTopLeft(find.text('Pepper'));
      expect(pepper.dy, miso.dy);
      expect(pepper.dx, greaterThan(miso.dx));
      expect(find.text('Cats'), findsWidgets);
    });

    testWidgets('warns when the server cannot be reached', (tester) async {
      final server = serverWith(twoCats());
      final controller = await pumpApp(tester, server, size: phone);
      await openCatsTab(tester);
      controller.unreachable = true;
      controller.notifyListeners();
      await tester.pump();

      expect(find.textContaining("Can't reach the server"), findsOneWidget);
      expect(find.text('Miso'), findsOneWidget);
    });

    testWidgets('lists archived cats and restores one', (tester) async {
      final server = serverWith(twoCats());
      final archived = catJson(id: 3, name: 'Biscuit')..['archived'] = true;
      server.routes['GET /api/cats'] = (_) => json([archived]);
      server.routes['PATCH /api/cats/3'] = (_) => json(catJson(id: 3));
      await pumpApp(tester, server, size: phone);
      await openCatsTab(tester);
      await tester.pumpAndSettle();

      expect(find.text('Archived'), findsOneWidget);
      await scrollTo(tester, find.text('Restore Biscuit'));
      await tester.tap(find.text('Restore Biscuit'));
      await tester.pumpAndSettle();

      expect(jsonDecode(lastRequest(server, 'PATCH', '/api/cats/3').body), {
        'archived': false,
      });
    });

    testWidgets('shows why a cat could not be restored', (tester) async {
      final server = serverWith(twoCats());
      final archived = catJson(id: 3, name: 'Biscuit')..['archived'] = true;
      server.routes['GET /api/cats'] = (_) => json([archived]);
      server.routes['PATCH /api/cats/3'] = (_) =>
          json({'detail': 'A cat called Biscuit already exists'}, 409);
      await pumpApp(tester, server, size: phone);
      await openCatsTab(tester);
      await tester.pumpAndSettle();

      await scrollTo(tester, find.text('Restore Biscuit'));
      await tester.tap(find.text('Restore Biscuit'));
      await tester.pumpAndSettle();

      expect(find.text('A cat called Biscuit already exists'), findsOneWidget);
    });
  });

  group('who was this', () {
    Future<FakeServer> openSheet(
      WidgetTester tester, {
      Map<String, Object?>? latest,
      int needing = 1,
    }) async {
      final server = serverWith(twoCats(needing: needing, latest: latest));
      await pumpApp(tester, server, size: phone);
      await openCatsTab(tester);
      await tester.tap(find.text('Choose'));
      await tester.pumpAndSettle();
      return server;
    }

    testWidgets('the strip names the visit and opens the sheet', (
      tester,
    ) async {
      await openSheet(tester, latest: pendingVisit());

      expect(find.text('Who was this?'), findsOneWidget);
      expect(find.textContaining('9.6 lb for 35 s, today at'), findsOneWidget);
      expect(find.text('Closest match'), findsOneWidget);
      expect(find.text('Usually 10.1 lb'), findsOneWidget);
      expect(find.text('Usually 9.0 lb'), findsOneWidget);
      expect(find.text('Not a cat'), findsOneWidget);
    });

    testWidgets('picking a cat labels the visit and closes at the end', (
      tester,
    ) async {
      final server = await openSheet(tester, latest: pendingVisit());
      server.routes['POST /api/visits/7/label'] = (_) {
        server.status = twoCats();
        return json({
          'id': 7,
          'cursor': 'c',
          'at': DateTime.now().toIso8601String(),
          'label': 'cat',
          'label_source': 'user',
          'cat_id': 2,
          'detail': {},
        });
      };
      await tester.tap(inSheet('Pepper'));
      await tester.pumpAndSettle();

      expect(
        jsonDecode(lastRequest(server, 'POST', '/api/visits/7/label').body),
        {'label': 'cat', 'cat_id': 2},
      );
      expect(find.text('Who was this?'), findsNothing);
    });

    testWidgets('not a cat sends the label without a cat', (tester) async {
      final server = await openSheet(tester, latest: pendingVisit());
      server.routes['POST /api/visits/7/label'] = (_) {
        server.status = twoCats();
        return json({
          'id': 7,
          'cursor': 'c',
          'at': DateTime.now().toIso8601String(),
          'label': 'not_cat',
          'label_source': 'user',
          'detail': {},
        });
      };
      await tester.tap(find.text('Not a cat'));
      await tester.pumpAndSettle();

      expect(
        jsonDecode(lastRequest(server, 'POST', '/api/visits/7/label').body),
        {'label': 'not_cat'},
      );
    });

    testWidgets('moves on to the next visit while more are waiting', (
      tester,
    ) async {
      final server = await openSheet(
        tester,
        latest: pendingVisit(),
        needing: 2,
      );
      expect(find.text('1 more after this one'), findsOneWidget);
      server.routes['POST /api/visits/7/label'] = (_) {
        server.status = twoCats(
          needing: 1,
          latest: pendingVisit(id: 6, weight: 8.8, closest: 2),
        );
        return json({
          'id': 7,
          'cursor': 'c',
          'at': DateTime.now().toIso8601String(),
          'label': 'cat',
          'label_source': 'user',
          'cat_id': 1,
          'detail': {},
        });
      };
      await tester.tap(inSheet('Miso'));
      await tester.pumpAndSettle();

      expect(find.text('Who was this?'), findsOneWidget);
      expect(find.textContaining('8.8 lb for 35 s'), findsOneWidget);
      expect(find.text('1 more after this one'), findsNothing);
    });

    testWidgets('a failure shows in the sheet and the sheet stays open', (
      tester,
    ) async {
      final server = await openSheet(tester, latest: pendingVisit());
      server.routes['POST /api/visits/7/label'] = (_) =>
          json({'detail': 'That visit is gone'}, 404);
      await tester.tap(inSheet('Miso'));
      await tester.pumpAndSettle();

      expect(find.text('That visit is gone'), findsOneWidget);
      expect(find.text('Who was this?'), findsOneWidget);
    });

    testWidgets('cancel closes without labelling', (tester) async {
      final server = await openSheet(tester, latest: pendingVisit());
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();

      expect(find.text('Who was this?'), findsNothing);
      expect(
        server.requests.where((r) => r.url.path.endsWith('/label')),
        isEmpty,
      );
    });

    testWidgets('closes when the visit gets a cat elsewhere', (tester) async {
      final server = await openSheet(tester, latest: pendingVisit());
      server.status = twoCats();
      final controller = AppScope.maybeOf(
        tester.element(find.text('Who was this?')),
      )!;
      await tester.runAsync(controller.poll);
      await tester.pumpAndSettle();

      expect(find.text('Who was this?'), findsNothing);
    });

    testWidgets('does not open when nothing needs a cat', (tester) async {
      final controller = await pumpApp(
        tester,
        serverWith(twoCats()),
        size: phone,
      );
      await openCatsTab(tester);
      await showWhoWasThis(tester.element(find.text('Add a cat')), controller);
      await tester.pumpAndSettle();

      expect(find.text('Who was this?'), findsNothing);
    });

    testWidgets('has no cat to choose when there are none', (tester) async {
      final server = serverWith(
        statusJson(robot: robotJson(), visitsNeedingCat: 1)
          ..['latest_visit_needing_cat'] = pendingVisit(closest: null),
      );
      final controller = await pumpApp(tester, server, size: phone);
      unawaited(showWhoWasThis(navigatorOf(tester).context, controller));
      await tester.pumpAndSettle();

      expect(find.text('Not a cat'), findsOneWidget);
      expect(find.text('Closest match'), findsNothing);
    });
  });

  group('the profile', () {
    Future<FakeServer> openProfile(
      WidgetTester tester, {
      Size size = phone,
    }) async {
      final server = serverWith(twoCats());
      server.status['cats'] = [
        catJson(
          name: 'Miso',
          summary: {
            'yesterday_weight_lb': 10.3,
            'yesterday_visits': 2,
            'last_visit_at': '2026-11-05T19:02:00',
          },
        )..['sex'] = 'spayed_female',
        catJson(
            id: 2,
            name: 'Pepper',
            colorIndex: 1,
            summary: {
              'yesterday_weight_lb': null,
              'yesterday_visits': 0,
              'last_visit_at': null,
            },
          )
          ..['reference_weight_lb'] = null
          ..['estimated_weight_lb'] = null,
      ];
      await pumpApp(tester, server, size: size);
      await openCatsTab(tester);
      await tester.tap(find.text('Miso'));
      await tester.pumpAndSettle();
      return server;
    }

    testWidgets('shows yesterday and the facts', (tester) async {
      await openProfile(tester);

      expect(find.text("Yesterday's weight"), findsOneWidget);
      expect(find.text('10.3 lb'), findsOneWidget);
      expect(find.text('2 visits'), findsOneWidget);
      expect(find.text('Spayed female'), findsOneWidget);
      expect(find.text('Reference weight'), findsOneWidget);
      expect(
        find.textContaining('Last visit: 11/5/2026 7:02 PM'),
        findsOneWidget,
      );
      expect(find.bySemanticsLabel('Back to cats'), findsOneWidget);
    });

    testWidgets('switching cats shows the other cat, even with no data', (
      tester,
    ) async {
      await openProfile(tester);
      await tester.tap(find.widgetWithText(InkWell, 'Pepper'));
      await tester.pumpAndSettle();

      expect(find.text('No visits yet'), findsOneWidget);
      expect(find.text('No visits'), findsNWidgets(2));
      expect(find.text('Not set'), findsOneWidget);
      expect(find.text('Unknown'), findsOneWidget);
    });

    testWidgets('the back button returns to the list', (tester) async {
      await openProfile(tester);
      await tester.tap(find.bySemanticsLabel('Back to cats'));
      await tester.pumpAndSettle();

      expect(find.text('Edit profile'), findsNothing);
      expect(find.text('Add a cat'), findsOneWidget);
    });

    testWidgets('view more goes to the insights tab', (tester) async {
      await openProfile(tester);
      await tester.tap(find.text('Weight'));
      await tester.pumpAndSettle();

      expect(find.text('Edit profile'), findsNothing);
      expect(
        tester
            .widget<Text>(
              find.descendant(
                of: find.byKey(const ValueKey('tab-insights')),
                matching: find.text('Insights'),
              ),
            )
            .style
            ?.fontWeight,
        FontWeight.w700,
      );
    });

    testWidgets('keeps a readable width on a wide window', (tester) async {
      await openProfile(tester, size: const Size(1280, 900));

      expect(find.text('Edit profile'), findsOneWidget);
      final card = tester.getSize(find.text("Yesterday's weight"));
      expect(card.width, lessThan(400));
      final identity = tester.getRect(find.text('Edit profile'));
      expect(identity.center.dx, closeTo(640, 1));
    });

    testWidgets('the edit button opens the form for this cat', (tester) async {
      await openProfile(tester);
      await tester.tap(find.text('Edit profile'));
      await tester.pumpAndSettle();

      expect(find.text('Edit profile'), findsOneWidget);
      expect(
        tester
            .widget<TextField>(hint('What do you call them?'))
            .controller!
            .text,
        'Miso',
      );
    });

    testWidgets('leaves when the cat is archived on another device', (
      tester,
    ) async {
      final server = await openProfile(tester);
      server.status['cats'] = [(server.status['cats']! as List).last];
      final controller = AppScope.maybeOf(
        tester.element(find.text('Edit profile')),
      )!;
      await tester.runAsync(controller.poll);
      await tester.pumpAndSettle();

      expect(find.text('Edit profile'), findsNothing);
      expect(find.text('Add a cat'), findsOneWidget);
    });
  });

  group('the cat form', () {
    Future<(FakeServer, AppController)> openForm(
      WidgetTester tester, {
      Cat? cat,
      PhotoChooser? choosePhoto,
      FakeServer? server,
    }) async {
      final fake = server ?? serverWith(twoCats());
      final controller = await pumpApp(tester, fake, size: phone);
      unawaited(
        navigatorOf(tester).push(
          MaterialPageRoute<void>(
            builder: (_) => AddCatScreen(
              controller: controller,
              cat: cat,
              choosePhoto: choosePhoto ?? () async => null,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      return (fake, controller);
    }

    Future<void> tapSave(WidgetTester tester) async {
      await scrollTo(tester, find.text('Save'));
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();
    }

    testWidgets('asks for a name before saving', (tester) async {
      final (server, _) = await openForm(tester);
      await tapSave(tester);

      expect(find.text('Enter a name.'), findsOneWidget);
      expect(server.requests.where((r) => r.method == 'POST'), isEmpty);
    });

    testWidgets('rejects a weight the server would refuse', (tester) async {
      final (server, _) = await openForm(tester);
      await tester.enterText(hint('What do you call them?'), 'Miso');
      await tester.enterText(hint('10.1'), '1');
      await tapSave(tester);

      expect(find.text('Enter a weight between 2 and 40 lb.'), findsOneWidget);
      expect(server.requests.where((r) => r.method == 'POST'), isEmpty);
    });

    testWidgets('creates a cat from what was entered', (tester) async {
      final server = serverWith(twoCats());
      server.routes['POST /api/cats'] = (_) => json(catJson(id: 3), 201);
      final (_, _) = await openForm(tester, server: server);
      await tester.enterText(hint('What do you call them?'), '  Rye ');
      await tester.enterText(hint('American Shorthair'), 'Maine Coon');
      await tester.enterText(hint('10.1'), '11,5');
      await scrollTo(tester, find.text('Spayed female'));
      await tester.tap(find.text('Spayed female'));
      await tester.pump();
      await tapSave(tester);

      expect(jsonDecode(lastRequest(server, 'POST', '/api/cats').body), {
        'name': 'Rye',
        'breed': 'Maine Coon',
        'reference_weight_lb': 11.5,
        'sex': 'spayed_female',
      });
      expect(find.text('Add a cat'), findsNothing);
    });

    testWidgets('sends a chosen birthday as a date', (tester) async {
      final server = serverWith(twoCats());
      server.routes['POST /api/cats'] = (_) => json(catJson(id: 3), 201);
      await openForm(tester, server: server);
      await tester.enterText(hint('What do you call them?'), 'Rye');
      await scrollTo(tester, find.text('Choose a date'));
      await tester.tap(find.text('Choose a date'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('OK'));
      await tester.pumpAndSettle();
      expect(find.text('Choose a date'), findsNothing);
      await tapSave(tester);

      final body = jsonDecode(
        lastRequest(server, 'POST', '/api/cats').body,
      ) as Map<String, dynamic>;
      expect(body['birthday'], matches(RegExp(r'^\d{4}-\d{2}-\d{2}$')));
    });

    testWidgets('a name that is taken shows next to the save button', (
      tester,
    ) async {
      final server = serverWith(twoCats());
      server.routes['POST /api/cats'] = (_) =>
          json({'detail': 'A cat called Miso already exists'}, 409);
      await openForm(tester, server: server);
      await tester.enterText(hint('What do you call them?'), 'Miso');
      await tapSave(tester);

      expect(find.text('A cat called Miso already exists'), findsOneWidget);
      expect(find.text('Add a cat'), findsWidgets);
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();
      expect(server.requests.where((r) => r.method == 'POST').length, 2);
    });

    testWidgets('picking a seen weight group fills the weight', (tester) async {
      await openForm(tester);
      expect(find.text('About 10.1 lb'), findsOneWidget);
      expect(find.text('31 visits'), findsOneWidget);
      expect(find.text('1 visit'), findsOneWidget);

      await tester.tap(find.text('About 9.0 lb'));
      await tester.pump();

      expect(tester.widget<TextField>(hint('10.1')).controller!.text, '9.0');
    });

    testWidgets('says there is nothing to suggest yet', (tester) async {
      final server = FakeServer(twoCats());
      server.routes['GET /api/cats/suggestions'] = (_) =>
          json(suggestions(enough: false));
      await openForm(tester, server: server);

      expect(find.textContaining('not seen enough'), findsOneWidget);
      expect(find.textContaining('About'), findsNothing);
    });

    testWidgets('a failed suggestion load can be retried', (tester) async {
      final server = FakeServer(twoCats());
      server.routes['GET /api/cats/suggestions'] = (_) =>
          json({'detail': 'Database is busy'}, 500);
      await openForm(tester, server: server);

      expect(
        find.textContaining("Couldn't load suggested weights."),
        findsOneWidget,
      );
      expect(find.textContaining('Database is busy'), findsOneWidget);

      server.routes['GET /api/cats/suggestions'] = (_) => json(suggestions());
      await scrollTo(tester, find.text('Try again'));
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();

      expect(find.text('About 10.1 lb'), findsOneWidget);
    });

    testWidgets('uploads the chosen photo after making the cat', (
      tester,
    ) async {
      final server = serverWith(twoCats());
      server.routes['POST /api/cats'] = (_) => json(catJson(id: 3), 201);
      server.routes['PUT /api/cats/3/photo'] = (_) =>
          json(catJson(id: 3, hasPhoto: true));
      final bytes = Uint8List.fromList([1, 2, 3]);
      await openForm(
        tester,
        server: server,
        choosePhoto: () async => PickedPhoto(bytes, 'image/png'),
      );
      await tester.enterText(hint('What do you call them?'), 'Rye');
      await tester.tap(find.byKey(const ValueKey('photo-circle')));
      await tester.pump();
      expect(find.text('Remove photo'), findsOneWidget);
      await tapSave(tester);

      final put = lastRequest(server, 'PUT', '/api/cats/3/photo');
      expect(put.bodyBytes, bytes);
      expect(put.headers['Content-Type'], 'image/png');
    });

    testWidgets('a photo that fails keeps the cat and retries only the photo', (
      tester,
    ) async {
      final server = serverWith(twoCats());
      server.routes['POST /api/cats'] = (_) => json(catJson(id: 3), 201);
      server.routes['PUT /api/cats/3/photo'] = (_) =>
          json({'detail': 'That image is too large'}, 413);
      await openForm(
        tester,
        server: server,
        choosePhoto: () async => PickedPhoto(Uint8List(3), 'image/jpeg'),
      );
      await tester.enterText(hint('What do you call them?'), 'Miso');
      await tester.tap(find.byKey(const ValueKey('photo-circle')));
      await tester.pump();
      await tapSave(tester);

      expect(
        find.textContaining('Miso is saved, but the photo did not upload.'),
        findsOneWidget,
      );
      await tapSave(tester);
      expect(server.requests.where((r) => r.method == 'POST').length, 1);
      expect(server.requests.where((r) => r.method == 'PUT').length, 2);
    });

    testWidgets('removing the photo before saving drops it again', (
      tester,
    ) async {
      await openForm(
        tester,
        choosePhoto: () async => PickedPhoto(Uint8List(3), 'image/jpeg'),
      );
      await tester.tap(find.byKey(const ValueKey('photo-circle')));
      await tester.pump();
      await tester.tap(find.text('Remove photo'));
      await tester.pump();

      expect(find.text('Add photo'), findsOneWidget);
      expect(find.text('Remove photo'), findsNothing);
    });

    testWidgets('editing sends only what changed and clears what was emptied', (
      tester,
    ) async {
      final server = serverWith(twoCats());
      server.routes['PATCH /api/cats/1'] = (_) => json(catJson());
      final cat = Cat.fromJson(
        catJson()
          ..['breed'] = 'Tabby'
          ..['birthday'] = '2022-04-08',
      );
      await openForm(tester, server: server, cat: cat);
      await tester.enterText(hint('American Shorthair'), '');
      await tester.enterText(hint('10.1'), '');
      await tester.enterText(hint('What do you call them?'), 'Miso Jr');
      await scrollTo(
        tester,
        find.byTooltip('Clear birthday').evaluate().isEmpty
            ? find.bySemanticsLabel('Clear birthday')
            : find.byTooltip('Clear birthday'),
      );
      await tester.tap(find.bySemanticsLabel('Clear birthday'));
      await tester.pump();
      await tapSave(tester);

      expect(jsonDecode(lastRequest(server, 'PATCH', '/api/cats/1').body), {
        'name': 'Miso Jr',
        'breed': null,
        'birthday': null,
        'reference_weight_lb': null,
      });
    });

    testWidgets('saving an unchanged cat sends nothing', (tester) async {
      final server = serverWith(twoCats());
      await openForm(tester, server: server, cat: Cat.fromJson(catJson()));
      await tapSave(tester);

      expect(server.requests.where((r) => r.method == 'PATCH'), isEmpty);
      expect(find.text('Edit profile'), findsNothing);
    });

    testWidgets('removing an existing photo deletes it', (tester) async {
      final server = serverWith(twoCats());
      server.routes['DELETE /api/cats/1/photo'] = (_) => http.Response('', 204);
      await openForm(
        tester,
        server: server,
        cat: Cat.fromJson(catJson(hasPhoto: true)),
      );
      await tester.tap(find.text('Remove photo'));
      await tester.pump();
      await tapSave(tester);

      expect(
        server.requests.where(
          (r) => r.method == 'DELETE' && r.url.path == '/api/cats/1/photo',
        ),
        hasLength(1),
      );
    });

    testWidgets('archiving asks first', (tester) async {
      final server = serverWith(twoCats());
      server.routes['PATCH /api/cats/1'] = (_) =>
          json(catJson()..['archived'] = true);
      await openForm(tester, server: server, cat: Cat.fromJson(catJson()));
      await scrollTo(tester, find.text('Archive this cat'));
      await tester.tap(find.text('Archive this cat'));
      await tester.pumpAndSettle();

      expect(find.text('Archive Miso?'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(server.requests.where((r) => r.method == 'PATCH'), isEmpty);

      await tester.tap(find.text('Archive this cat'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Archive'));
      await tester.pumpAndSettle();

      expect(jsonDecode(lastRequest(server, 'PATCH', '/api/cats/1').body), {
        'archived': true,
      });
      expect(find.text('Edit profile'), findsNothing);
    });

    testWidgets('a new cat has no archive button', (tester) async {
      await openForm(tester);
      expect(find.text('Archive this cat'), findsNothing);
    });

    testWidgets('ignores answers that arrive after the form is closed', (
      tester,
    ) async {
      final gate = Completer<void>();
      final server = serverWith(twoCats());
      final inner = server.client;
      final slow = MockClient((request) async {
        if (request.url.path == '/api/cats/suggestions') await gate.future;
        final copy = http.Request(request.method, request.url)
          ..headers.addAll(request.headers)
          ..bodyBytes = request.bodyBytes;
        return http.Response.fromStream(await inner.send(copy));
      });
      tester.view.physicalSize = phone;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      SharedPreferences.setMockInitialValues({
        'server_url': 'http://box',
        'access_key': 'key',
      });
      final controller = AppController(httpClient: slow);
      addTearDown(controller.dispose);
      await tester.runAsync(controller.start);
      await tester.pumpWidget(LitterBoxApp(controller: controller));
      await untilTrue(tester, () => controller.status != null);

      unawaited(
        navigatorOf(tester).push(
          MaterialPageRoute<void>(
            builder: (_) => AddCatScreen(controller: controller),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Add a cat'), findsWidgets);
      navigatorOf(tester).pop();
      await tester.pumpAndSettle();

      gate.complete();
      await tester.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 50)),
      );
      await tester.pump();
      expect(tester.takeException(), isNull);
    });
  });

  group('the shell', () {
    testWidgets('opens the cat list from the Cats tab', (tester) async {
      await pumpApp(tester, serverWith(twoCats()), size: phone);
      await openCatsTab(tester);
      expect(
        ShellScope.of(tester.element(find.byType(CatsScreen))).current,
        AppTab.cats,
      );
    });
  });

  for (final mode in Brightness.values) {
    testWidgets('lists and the sheet draw in the ${mode.name} palette', (
      tester,
    ) async {
      tester.platformDispatcher.platformBrightnessTestValue = mode;
      addTearDown(tester.platformDispatcher.clearAllTestValues);
      await pumpApp(
        tester,
        serverWith(twoCats(needing: 1, latest: pendingVisit())),
        size: phone,
      );
      await openCatsTab(tester);
      await tester.tap(find.text('Choose'));
      await tester.pumpAndSettle();

      final context = tester.element(find.text('Who was this?'));
      expect(context.palette.isDark, mode == Brightness.dark);
      expect(tester.takeException(), isNull);
    });
  }
}
