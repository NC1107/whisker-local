import 'package:flutter/material.dart';

import 'controller.dart';
import 'screens/connect_screen.dart';
import 'shell.dart';
import 'theme.dart';
import 'widgets/common/common.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(LitterBoxApp(controller: AppController()..start()));
}

class LitterBoxApp extends StatefulWidget {
  const LitterBoxApp({required this.controller, super.key});

  final AppController controller;

  @override
  State<LitterBoxApp> createState() => _LitterBoxAppState();
}

class _LitterBoxAppState extends State<LitterBoxApp> {
  final _navigator = GlobalKey<NavigatorState>();

  AppController get controller => widget.controller;

  @override
  void initState() {
    super.initState();
    controller.addListener(_leavePushedScreens);
  }

  @override
  void dispose() {
    controller.removeListener(_leavePushedScreens);
    super.dispose();
  }

  // Screens pushed over the shell would otherwise stay above the connect screen.
  void _leavePushedScreens() {
    if (controller.phase != AppPhase.needsConnection) return;
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => _navigator.currentState?.popUntil((route) => route.isFirst),
    );
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      navigatorKey: _navigator,
      title: 'Litter box',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      themeMode: ThemeMode.system,
      builder: (context, child) =>
          AppScope(controller: controller, child: child!),
      home: ListenableBuilder(
        listenable: controller,
        builder: (context, _) => switch (controller.phase) {
          AppPhase.starting => const Scaffold(body: SizedBox.shrink()),
          AppPhase.needsConnection => ConnectScreen(controller: controller),
          AppPhase.ready => Shell(controller: controller),
        },
      ),
    );
  }
}
