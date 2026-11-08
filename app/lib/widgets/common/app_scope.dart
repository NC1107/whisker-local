import 'package:flutter/widgets.dart';

import '../../controller.dart';

/// Makes the controller reachable from shared widgets, which need it for
/// photos, without passing it through every constructor.
class AppScope extends InheritedWidget {
  const AppScope({required this.controller, required super.child, super.key});

  final AppController controller;

  /// Null outside the app, such as a widget test that pumps one widget alone.
  static AppController? maybeOf(BuildContext context) =>
      context.getInheritedWidgetOfExactType<AppScope>()?.controller;

  @override
  bool updateShouldNotify(AppScope oldWidget) =>
      controller != oldWidget.controller;
}
