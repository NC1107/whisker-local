import 'package:flutter/material.dart';

import 'controller.dart';
import 'screens/connect_screen.dart';
import 'screens/history_screen.dart';
import 'screens/home_screen.dart';
import 'screens/settings_screen.dart';
import 'theme.dart';

void main() => runApp(LitterBoxApp(controller: AppController()..start()));

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

class Shell extends StatefulWidget {
  const Shell({required this.controller, super.key});

  final AppController controller;

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  static const _wideAt = 800.0;

  int _tab = 0;

  void _select(int tab) => setState(() => _tab = tab);

  @override
  Widget build(BuildContext context) {
    final pages = IndexedStack(
      index: _tab,
      children: [
        HomeScreen(controller: widget.controller),
        HistoryScreen(controller: widget.controller, active: _tab == 1),
        SettingsScreen(controller: widget.controller),
      ],
    );
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth >= _wideAt) {
          return Scaffold(
            body: SafeArea(
              child: Row(
                children: [
                  NavigationRail(
                    selectedIndex: _tab,
                    onDestinationSelected: _select,
                    labelType: NavigationRailLabelType.all,
                    destinations: const [
                      NavigationRailDestination(
                        icon: Icon(Icons.circle_outlined),
                        label: Text('Home'),
                      ),
                      NavigationRailDestination(
                        icon: Icon(Icons.show_chart),
                        label: Text('History'),
                      ),
                      NavigationRailDestination(
                        icon: Icon(Icons.tune),
                        label: Text('Settings'),
                      ),
                    ],
                  ),
                  const VerticalDivider(width: 1),
                  Expanded(child: pages),
                ],
              ),
            ),
          );
        }
        return Scaffold(
          body: SafeArea(child: pages),
          bottomNavigationBar: NavigationBar(
            selectedIndex: _tab,
            onDestinationSelected: _select,
            destinations: const [
              NavigationDestination(
                icon: Icon(Icons.circle_outlined),
                label: 'Home',
              ),
              NavigationDestination(
                icon: Icon(Icons.show_chart),
                label: 'History',
              ),
              NavigationDestination(icon: Icon(Icons.tune), label: 'Settings'),
            ],
          ),
        );
      },
    );
  }
}
