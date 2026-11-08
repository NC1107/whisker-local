import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:whisker_local/main.dart' as app;

// A plain test() has no binding, which is the state a release build starts in.
void main() {
  test('main initialises the binding before the controller uses it', () {
    SharedPreferences.setMockInitialValues({});
    expect(app.main, returnsNormally);
  });
}
