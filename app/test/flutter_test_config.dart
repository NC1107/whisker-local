import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:whisker_local/theme.dart';

/// Tests lay text out with the app's real font, so a label that wraps or
/// overflows in the app does so in the tests too. It is read from disk, because
/// a test that checks the app starts without a binding must not get one here.
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  for (final file in ['Regular', 'Bold']) {
    final bytes = await File('assets/fonts/AtkinsonHyperlegible-$file.ttf')
        .readAsBytes();
    await ui.loadFontFromList(bytes, fontFamily: fontFamily);
  }
  await testMain();
}
