import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/api.dart';
import 'package:whisker_local/controller.dart';
import 'package:whisker_local/photos.dart';
import 'package:whisker_local/theme.dart';
import 'package:whisker_local/widgets/common/common.dart';

import '../support/fixtures.dart';

final pixel = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/'
  'iZk9HQAAAABJRU5ErkJggg==',
);

Cat cat(Map<String, Object?> json) => Cat.fromJson(catJson()..addAll(json));

Widget host(Widget child, {AppController? controller, Brightness? mode}) {
  final app = MaterialApp(
    theme: buildTheme(mode ?? Brightness.light),
    home: Scaffold(body: Center(child: child)),
  );
  return controller == null
      ? app
      : AppScope(controller: controller, child: app);
}

Future<AppController> connected(WidgetTester tester, FakeServer server) async {
  SharedPreferences.setMockInitialValues({
    'server_url': 'http://box',
    'access_key': 'key',
  });
  final controller = AppController(httpClient: server.client);
  addTearDown(controller.dispose);
  await tester.runAsync(controller.start);
  await untilTrue(tester, () => controller.status != null);
  return controller;
}

void main() {
  group('CatAvatar', () {
    testWidgets('falls back to a colored initial without a photo', (
      tester,
    ) async {
      await tester.pumpWidget(host(CatAvatar(cat: cat({'name': 'miso'}))));
      expect(find.text('M'), findsOneWidget);
      final color = lightPalette.catColor(0);
      final circle = tester.widget<Container>(
        find
            .ancestor(of: find.text('M'), matching: find.byType(Container))
            .first,
      );
      expect(circle.color, color.background);
      expect(tester.getSize(find.byType(CatAvatar)), const Size(52, 52));
    });

    testWidgets('uses the next color per cat and wraps around', (tester) async {
      expect(
        lightPalette.catColor(1).background,
        isNot(lightPalette.catColor(0).background),
      );
      expect(
        lightPalette.catColor(lightPalette.catColors.length).background,
        lightPalette.catColor(0).background,
      );
      await tester.pumpWidget(
        host(CatAvatar(cat: cat({'color_index': 1, 'name': 'Rio'}))),
      );
      final circle = tester.widget<Container>(
        find
            .ancestor(of: find.text('R'), matching: find.byType(Container))
            .first,
      );
      expect(circle.color, lightPalette.catColor(1).background);
    });

    testWidgets('dark mode draws the dark cat colors', (tester) async {
      await tester.pumpWidget(
        host(CatAvatar(cat: cat({})), mode: Brightness.dark),
      );
      final circle = tester.widget<Container>(
        find
            .ancestor(of: find.text('M'), matching: find.byType(Container))
            .first,
      );
      expect(circle.color, darkPalette.catColor(0).background);
    });

    testWidgets('loads the photo with the access key, once per version', (
      tester,
    ) async {
      final server = FakeServer(statusJson());
      var downloads = 0;
      String? sentKey;
      server.routes['GET /api/cats/1/photo'] = (request) {
        downloads++;
        sentKey = request.headers['Authorization'];
        return http.Response.bytes(pixel, 200);
      };
      final controller = await connected(tester, server);
      final withPhoto = cat({'has_photo': true, 'photo_version': 3});

      await tester.pumpWidget(
        host(
          Column(
            children: [
              CatAvatar(cat: withPhoto),
              CatAvatar(cat: withPhoto, size: 30),
              CatBanner(cat: withPhoto),
            ],
          ),
          controller: controller,
        ),
      );
      await untilTrue(tester, () => find.byType(Image).evaluate().isNotEmpty);
      await tester.pump();

      expect(find.byType(Image), findsNWidgets(3));
      expect(downloads, 1);
      expect(sentKey, 'Bearer key');
      expect(find.text('M'), findsNothing);
    });

    testWidgets('fetches again when the photo changes', (tester) async {
      final server = FakeServer(statusJson());
      var downloads = 0;
      server.routes['GET /api/cats/1/photo'] = (_) {
        downloads++;
        return http.Response.bytes(pixel, 200);
      };
      final controller = await connected(tester, server);

      await tester.pumpWidget(
        host(
          CatAvatar(cat: cat({'has_photo': true, 'photo_version': 1})),
          controller: controller,
        ),
      );
      await untilTrue(tester, () => downloads == 1);
      await tester.pumpWidget(
        host(
          CatAvatar(cat: cat({'has_photo': true, 'photo_version': 2})),
          controller: controller,
        ),
      );
      await untilTrue(tester, () => downloads == 2);
      expect(downloads, 2);
    });

    testWidgets('keeps the initial when the photo cannot be loaded', (
      tester,
    ) async {
      final server = FakeServer(statusJson());
      server.routes['GET /api/cats/1/photo'] = (_) =>
          http.Response('{"detail":"No photo."}', 404);
      final controller = await connected(tester, server);
      await tester.pumpWidget(
        host(
          CatAvatar(cat: cat({'has_photo': true, 'photo_version': 1})),
          controller: controller,
        ),
      );
      await untilTrue(
        tester,
        () => server.requests.any(
          (request) => request.url.path.endsWith('/photo'),
        ),
      );
      await tester.pump();
      expect(find.text('M'), findsOneWidget);
      expect(find.byType(Image), findsNothing);
    });

    testWidgets('a cat without a photo never asks for one', (tester) async {
      final server = FakeServer(statusJson());
      final controller = await connected(tester, server);
      await tester.pumpWidget(
        host(CatAvatar(cat: cat({})), controller: controller),
      );
      await tester.pump();
      expect(
        server.requests.where((request) => request.url.path.contains('photo')),
        isEmpty,
      );
    });

    test('the cache keeps the newest photos and drops old versions', () async {
      final fetched = <int>[];
      final cache = PhotoCache((id) async {
        fetched.add(id);
        return Uint8List.fromList([id]);
      }, capacity: 2);
      Cat withPhoto(int id, int version) =>
          cat({'id': id, 'has_photo': true, 'photo_version': version});

      await cache.load(withPhoto(1, 1));
      await cache.load(withPhoto(2, 1));
      await cache.load(withPhoto(3, 1));
      expect(cache.peek(withPhoto(1, 1)), isNull);
      expect(cache.peek(withPhoto(3, 1)), isNotNull);

      await cache.load(withPhoto(3, 2));
      expect(cache.peek(withPhoto(3, 1)), isNull);
      expect(fetched, [1, 2, 3, 3]);
    });
  });

  group('CatBanner', () {
    testWidgets('fills the width with the initial and the given height', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(SizedBox(width: 358, child: CatBanner(cat: cat({'name': 'Zed'})))),
      );
      expect(tester.getSize(find.byType(CatBanner)), const Size(358, 150));
      expect(find.text('Z'), findsOneWidget);
    });
  });

  group('gauges', () {
    test('litter words follow the level', () {
      expect(litterWord(null), 'Unknown');
      expect(litterWord(5), 'Empty');
      expect(litterWord(20), 'Low');
      expect(litterWord(50), 'Optimal');
      expect(litterWord(95), 'Full');
      expect(litterWord(35, lowAt: 40), 'Low');
    });

    testWidgets('waste shows its percentage and describes itself', (
      tester,
    ) async {
      await tester.pumpWidget(host(const WasteGauge(percent: 55)));
      expect(find.text('Waste'), findsOneWidget);
      expect(find.text('55%'), findsOneWidget);
      expect(
        find.bySemanticsLabel('Waste drawer 55 percent full'),
        findsOneWidget,
      );
    });

    testWidgets('a full drawer reads Full in the warning color', (
      tester,
    ) async {
      await tester.pumpWidget(host(const WasteGauge(percent: 80, full: true)));
      expect(find.text('Full'), findsOneWidget);
      expect(find.bySemanticsLabel('Waste drawer full'), findsOneWidget);
      final fill = tester.widget<ColoredBox>(
        find.descendant(
          of: find.byType(FractionallySizedBox),
          matching: find.byType(ColoredBox),
        ),
      );
      expect(fill.color, lightPalette.warn);
    });

    testWidgets('a drawer estimate says so', (tester) async {
      await tester.pumpWidget(
        host(const WasteGauge(percent: 0, estimate: true)),
      );
      expect(find.text('About 0%'), findsOneWidget);
    });

    testWidgets('litter reads as a word', (tester) async {
      await tester.pumpWidget(host(const LitterGauge(percent: 62)));
      expect(find.text('Litter'), findsOneWidget);
      expect(find.text('Optimal'), findsOneWidget);
      expect(find.bySemanticsLabel('Litter level optimal'), findsOneWidget);
    });

    testWidgets('a litter level the server only estimated says so', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(const LitterGauge(percent: 62, estimate: true)),
      );
      expect(find.text('Litter (est.)'), findsOneWidget);
      expect(
        find.bySemanticsLabel('Litter level optimal, an estimate'),
        findsOneWidget,
      );
    });

    testWidgets('an unknown level has nothing to caveat', (tester) async {
      await tester.pumpWidget(host(const LitterGauge(estimate: true)));
      expect(find.text('Litter'), findsOneWidget);
    });

    testWidgets('the large size is bigger', (tester) async {
      await tester.pumpWidget(host(const LitterGauge(percent: 62)));
      final small = tester.getSize(find.byType(ClipOval));
      await tester.pumpWidget(
        host(const LitterGauge(percent: 62, large: true)),
      );
      expect(
        tester.getSize(find.byType(ClipOval)).width,
        greaterThan(small.width),
      );
    });
  });

  group('PeriodTabs', () {
    testWidgets('marks the chosen period and reports taps', (tester) async {
      InsightPeriod chosen = InsightPeriod.week;
      await tester.pumpWidget(
        host(
          StatefulBuilder(
            builder: (context, setState) => SizedBox(
              width: 300,
              child: PeriodTabs(
                value: chosen,
                onChanged: (period) => setState(() => chosen = period),
              ),
            ),
          ),
        ),
      );
      expect(find.text('Week'), findsOneWidget);
      expect(find.text('Month'), findsOneWidget);
      expect(find.text('Year'), findsOneWidget);

      Border underline(String code) =>
          (tester
                          .widget<Container>(
                            find.descendant(
                              of: find.byKey(ValueKey('period-$code')),
                              matching: find.byType(Container),
                            ),
                          )
                          .decoration!
                      as BoxDecoration)
                  .border!
              as Border;
      expect(underline('week').bottom.color, lightPalette.primary);
      expect(underline('month').bottom.color, Colors.transparent);

      await tester.tap(find.text('Month'));
      await tester.pump();
      expect(chosen, InsightPeriod.month);
      expect(underline('month').bottom.color, lightPalette.primary);
      expect(underline('week').bottom.color, Colors.transparent);
    });
  });

  group('cards and rows', () {
    testWidgets('SurfaceCard takes taps and is a button for screen readers', (
      tester,
    ) async {
      var taps = 0;
      await tester.pumpWidget(
        host(
          SurfaceCard(
            onTap: () => taps++,
            semanticLabel: 'Open Miso',
            child: const Text('Miso'),
          ),
        ),
      );
      await tester.tap(find.text('Miso'));
      expect(taps, 1);
      expect(find.bySemanticsLabel('Open Miso'), findsOneWidget);
    });

    testWidgets('SurfaceCard draws the shadow in light and none in dark', (
      tester,
    ) async {
      await tester.pumpWidget(host(const SurfaceCard(child: Text('a'))));
      BoxDecoration decoration() =>
          tester
                  .widget<DecoratedBox>(
                    find
                        .descendant(
                          of: find.byType(SurfaceCard),
                          matching: find.byType(DecoratedBox),
                        )
                        .first,
                  )
                  .decoration
              as BoxDecoration;
      expect(decoration().boxShadow, isNotEmpty);
      expect(decoration().color, lightPalette.card);
      await tester.pumpWidget(
        host(const SurfaceCard(child: Text('a')), mode: Brightness.dark),
      );
      await tester.pump(const Duration(seconds: 1));
      expect(decoration().boxShadow, isEmpty);
      expect(decoration().color, darkPalette.card);
    });

    testWidgets('SectionHeader draws the title and a hairline', (tester) async {
      await tester.pumpWidget(
        host(const SizedBox(width: 300, child: SectionHeader('Cats'))),
      );
      expect(find.text('Cats'), findsOneWidget);
      final line = tester.getSize(
        find.byWidgetPredicate(
          (widget) => widget is Container && widget.color == lightPalette.line,
        ),
      );
      expect(line.height, 1);
      expect(line.width, greaterThan(150));
    });

    testWidgets('SectionHeader is bigger on a wide window', (tester) async {
      Future<double> size(Size window) async {
        tester.view.physicalSize = window;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);
        await tester.pumpWidget(host(const SectionHeader('Cats')));
        return tester.widget<Text>(find.text('Cats')).style!.fontSize!;
      }

      expect(await size(const Size(400, 800)), 20);
      expect(await size(const Size(1280, 800)), 24);
    });

    testWidgets('StatTile shows number, unit and label', (tester) async {
      await tester.pumpWidget(
        host(
          const StatTile(
            value: '10.2',
            unit: 'lb',
            label: "Yesterday's weight",
          ),
        ),
      );
      expect(find.textContaining('10.2'), findsOneWidget);
      expect(find.text("Yesterday's weight"), findsOneWidget);
      expect(
        find.bySemanticsLabel("Yesterday's weight: 10.2 lb"),
        findsOneWidget,
      );
    });

    testWidgets('StatusDot uses the tone color', (tester) async {
      await tester.pumpWidget(host(const StatusDot(StatusTone.warning)));
      final dot = tester.widget<Container>(find.byType(Container));
      expect((dot.decoration! as BoxDecoration).color, lightPalette.warn);
    });

    testWidgets('NoticeCard shows its lead, message and action', (
      tester,
    ) async {
      var taps = 0;
      await tester.pumpWidget(
        host(
          NoticeCard(
            lead: '1 visit needs a cat.',
            message: '9.6 lb at 3:42 PM',
            actionLabel: 'Choose',
            onTap: () => taps++,
          ),
        ),
      );
      expect(find.textContaining('1 visit needs a cat.'), findsOneWidget);
      await tester.tap(find.text('Choose'));
      expect(taps, 1);
    });

    testWidgets('RobotGlyph keeps the design proportions', (tester) async {
      await tester.pumpWidget(host(const RobotGlyph()));
      expect(tester.getSize(find.byType(RobotGlyph)), const Size(112, 132));
      expect(find.bySemanticsLabel('Litter robot'), findsOneWidget);
    });

    testWidgets('TopBar keeps the title centered with an empty side', (
      tester,
    ) async {
      await tester.pumpWidget(
        host(
          SizedBox(
            width: 400,
            child: TopBar(
              title: 'Cats',
              leading: TopBarButton(
                icon: AppIcons.bell,
                label: 'Alerts',
                onPressed: () {},
              ),
            ),
          ),
        ),
      );
      expect(
        tester.getCenter(find.text('Cats')).dx,
        tester.getCenter(find.byType(TopBar)).dx,
      );
      expect(tester.getSize(find.byType(TopBar)).height, 60);
    });
  });

  group('PageBody', () {
    testWidgets('pads 16 on a phone and caps and centers on a wide window', (
      tester,
    ) async {
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      tester.view.physicalSize = const Size(400, 800);
      await tester.pumpWidget(
        host(const PageBody(children: [SizedBox(height: 40, key: Key('a'))])),
      );
      expect(tester.getTopLeft(find.byKey(const Key('a'))).dx, 16);

      tester.view.physicalSize = const Size(1600, 800);
      await tester.pump();
      final box = tester.getRect(find.byKey(const Key('a')));
      expect(box.width, contentMaxWidth - 48);
      expect(box.center.dx, 800);
    });
  });

  test('picked photos fall back to the file name for their type', () {
    expect(contentTypeOf(mimeType: 'image/webp', name: 'a'), 'image/webp');
    expect(contentTypeOf(mimeType: null, name: 'cat.PNG'), 'image/png');
    expect(contentTypeOf(mimeType: '', name: 'cat.gif'), 'image/gif');
    expect(contentTypeOf(mimeType: null, name: 'cat'), 'image/jpeg');
  });
}
