import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/screens/robot_parts.dart';
import 'package:whisker_local/theme.dart';

double contrast(Color a, Color b) {
  final high = math.max(a.computeLuminance(), b.computeLuminance());
  final low = math.min(a.computeLuminance(), b.computeLuminance());
  return (high + 0.05) / (low + 0.05);
}

Future<void> pumpIn(WidgetTester tester, Brightness mode, Widget child) =>
    tester.pumpWidget(
      MaterialApp(
        theme: buildTheme(mode),
        home: Scaffold(body: Center(child: child)),
      ),
    );

Color textColor(WidgetTester tester, String text) => tester
    .widget<RichText>(
      find.descendant(of: find.text(text), matching: find.byType(RichText)),
    )
    .text
    .style!
    .color!;

Color fillOf(WidgetTester tester, Finder button) => tester
    .widget<Material>(
      find.descendant(of: button, matching: find.byType(Material)).first,
    )
    .color!;

void main() {
  for (final mode in Brightness.values) {
    group('disabled buttons in $mode', () {
      final palette = mode == Brightness.dark ? darkPalette : lightPalette;

      test('the palette pair stays at 3:1 or better', () {
        expect(
          contrast(palette.disabledInk, palette.disabledFill),
          greaterThanOrEqualTo(3),
        );
        expect(
          contrast(palette.disabledInk, palette.card),
          greaterThanOrEqualTo(3),
        );
      });

      testWidgets('a disabled filled button uses it', (tester) async {
        await pumpIn(
          tester,
          mode,
          const FilledButton(onPressed: null, child: Text('Save')),
        );
        final fill = fillOf(tester, find.byType(FilledButton));
        expect(fill, palette.disabledFill);
        expect(
          contrast(textColor(tester, 'Save'), fill),
          greaterThanOrEqualTo(3),
        );
      });

      testWidgets('a disabled outlined and text button stay readable', (
        tester,
      ) async {
        await pumpIn(
          tester,
          mode,
          const Column(
            children: [
              OutlinedButton(onPressed: null, child: Text('Outlined')),
              TextButton(onPressed: null, child: Text('Plain')),
            ],
          ),
        );
        for (final label in ['Outlined', 'Plain']) {
          expect(
            contrast(textColor(tester, label), palette.bg),
            greaterThanOrEqualTo(3),
          );
        }
      });

      testWidgets('a disabled robot button is not faded', (tester) async {
        await pumpIn(
          tester,
          mode,
          const Column(
            children: [
              RobotButton(label: 'Filled', filled: true, onPressed: null),
              RobotButton(label: 'Outline', onPressed: null),
            ],
          ),
        );
        expect(find.byType(Opacity), findsNothing);
        expect(
          contrast(
            textColor(tester, 'Filled'),
            fillOf(tester, find.byType(RobotButton).first),
          ),
          greaterThanOrEqualTo(3),
        );
        expect(
          contrast(textColor(tester, 'Outline'), palette.bg),
          greaterThanOrEqualTo(3),
        );
      });
    });
  }
}
