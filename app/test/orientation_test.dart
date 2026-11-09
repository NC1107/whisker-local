import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/orientation.dart';

void main() {
  test('a phone is under 600 logical pixels on its short side', () {
    expect(isPhone(const Size(390, 844)), isTrue);
    expect(isPhone(const Size(844, 390)), isTrue);
    expect(isPhone(const Size(599, 1200)), isTrue);
  });

  test('a tablet is 600 or more on its short side and keeps rotating', () {
    expect(isPhone(const Size(600, 960)), isFalse);
    expect(isPhone(const Size(1280, 800)), isFalse);
  });
}
