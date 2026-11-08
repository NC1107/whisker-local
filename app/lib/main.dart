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

class LitterBoxApp extends StatelessWidget {
  const LitterBoxApp({required this.controller, super.key});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
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
