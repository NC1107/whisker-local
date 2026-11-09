import 'package:flutter_test/flutter_test.dart';
import 'package:whisker_local/format.dart';

void main() {
  final now = DateTime(2026, 11, 8, 21, 30);

  group('whenText', () {
    test('writes today and yesterday in words', () {
      expect(whenText(DateTime(2026, 11, 8, 20, 19), now), 'today at 8:19 PM');
      expect(
        whenText(DateTime(2026, 11, 7, 20, 19), now),
        'yesterday at 8:19 PM',
      );
    });

    test('writes any other day as month and day', () {
      expect(whenText(DateTime(2026, 11, 3, 8, 19), now), '11/3 at 8:19 AM');
    });

    test('adds the year only when it is not this one', () {
      expect(
        whenText(DateTime(2025, 12, 30, 8, 19), now),
        '12/30/2025 at 8:19 AM',
      );
    });

    test('a time just after midnight is still the day before', () {
      final early = DateTime(2026, 11, 8, 0, 10);
      expect(
        whenText(DateTime(2026, 11, 7, 23, 50), early),
        'yesterday at 11:50 PM',
      );
    });
  });
}
