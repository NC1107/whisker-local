import 'package:flutter/material.dart';

import 'controller.dart';
import 'screens/activity_screen.dart';
import 'screens/cats_screen.dart';
import 'screens/home_screen.dart';
import 'screens/insights_screen.dart';
import 'screens/robot_screen.dart';
import 'screens/settings_screen.dart';
import 'theme.dart';
import 'widgets/common/common.dart';

enum AppTab {
  home('Home', AppIcons.home),
  insights('Insights', AppIcons.insights),
  robot('Robot', AppIcons.robot),
  cats('Cats', AppIcons.cats);

  const AppTab(this.label, this.icon);

  final String label;
  final String icon;
}

/// Lets a screen send the person to another tab.
class ShellScope extends InheritedWidget {
  const ShellScope({
    required this.select,
    required this.current,
    required super.child,
    super.key,
  });

  final ValueChanged<AppTab> select;
  final AppTab current;

  static ShellScope of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<ShellScope>()!;

  @override
  bool updateShouldNotify(ShellScope oldWidget) => current != oldWidget.current;
}

class Shell extends StatefulWidget {
  const Shell({required this.controller, super.key});

  final AppController controller;

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  AppTab _tab = AppTab.home;
  final _visited = {AppTab.home};

  void _select(AppTab tab) => setState(() {
    _tab = tab;
    _visited.add(tab);
  });

  void _openActivity() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ActivityScreen(controller: widget.controller),
      ),
    );
  }

  void _openSettings() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => SettingsScreen(controller: widget.controller),
      ),
    );
  }

  Widget _screen(AppTab tab) => switch (tab) {
    AppTab.home => HomeScreen(controller: widget.controller),
    AppTab.insights => InsightsScreen(controller: widget.controller),
    AppTab.robot => RobotScreen(controller: widget.controller),
    AppTab.cats => CatsScreen(controller: widget.controller),
  };

  @override
  Widget build(BuildContext context) {
    final pages = IndexedStack(
      index: _tab.index,
      children: [
        for (final tab in AppTab.values)
          _visited.contains(tab) ? _screen(tab) : const SizedBox.shrink(),
      ],
    );
    return ShellScope(
      select: _select,
      current: _tab,
      child: ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final demo = widget.controller.status?.demo ?? false;
          final unread = widget.controller.unreadAlertCount;
          final body = Column(
            children: [
              if (demo) const _SampleDataBanner(),
              Expanded(child: pages),
            ],
          );
          if (isWide(context)) {
            return Scaffold(
              body: Row(
                children: [
                  _SideRail(
                    current: _tab,
                    onSelect: _select,
                    unread: unread,
                    onActivity: _openActivity,
                    onSettings: _openSettings,
                  ),
                  Expanded(child: body),
                ],
              ),
            );
          }
          return Scaffold(
            body: Column(
              children: [
                TopBar(
                  title: _tab == AppTab.home ? 'Litter box' : _tab.label,
                  leading: TopBarButton(
                    icon: AppIcons.bell,
                    label: unread == 0 ? 'Activity' : 'Activity, $unread new',
                    showDot: unread > 0,
                    onPressed: _openActivity,
                  ),
                  trailing: TopBarButton(
                    icon: AppIcons.settings,
                    label: 'Settings',
                    onPressed: _openSettings,
                  ),
                ),
                Expanded(child: body),
                _BottomBar(current: _tab, onSelect: _select),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _SampleDataBanner extends StatelessWidget {
  const _SampleDataBanner();

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      liveRegion: true,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        color: palette.soft,
        child: Text(
          'Sample data until the robot connects',
          textAlign: TextAlign.center,
          style: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w700,
            color: palette.ink,
          ),
        ),
      ),
    );
  }
}

class _BottomBar extends StatelessWidget {
  const _BottomBar({required this.current, required this.onSelect});

  final AppTab current;
  final ValueChanged<AppTab> onSelect;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Container(
      padding: EdgeInsets.fromLTRB(
        6,
        8,
        6,
        12 + MediaQuery.paddingOf(context).bottom,
      ),
      decoration: BoxDecoration(
        color: palette.card,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
        boxShadow: palette.navShadow,
      ),
      child: Row(
        children: [
          for (final tab in AppTab.values)
            Expanded(
              child: Semantics(
                button: true,
                selected: tab == current,
                label: tab.label,
                child: ExcludeSemantics(
                  child: InkWell(
                    key: ValueKey('tab-${tab.name}'),
                    borderRadius: BorderRadius.circular(18),
                    onTap: () => onSelect(tab),
                    child: SizedBox(
                      height: 56,
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          AppIcon(
                            tab.icon,
                            size: 26,
                            color: tab == current
                                ? palette.primary
                                : palette.sub,
                          ),
                          const SizedBox(height: 3),
                          Text(
                            tab.label,
                            style: TextStyle(
                              fontSize: 13,
                              fontWeight: tab == current
                                  ? FontWeight.w700
                                  : FontWeight.w400,
                              color: tab == current
                                  ? palette.primary
                                  : palette.sub,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _SideRail extends StatelessWidget {
  const _SideRail({
    required this.current,
    required this.onSelect,
    required this.unread,
    required this.onActivity,
    required this.onSettings,
  });

  final AppTab current;
  final ValueChanged<AppTab> onSelect;
  final int unread;
  final VoidCallback onActivity;
  final VoidCallback onSettings;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Container(
      width: 210,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 20),
      decoration: BoxDecoration(color: palette.card, boxShadow: palette.shadow),
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 16),
              child: Semantics(
                header: true,
                child: Text(
                  'Litter box',
                  style: TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w700,
                    color: palette.ink,
                  ),
                ),
              ),
            ),
            for (final tab in AppTab.values)
              _RailItem(
                key: ValueKey('tab-${tab.name}'),
                label: tab.label,
                selected: tab == current,
                onTap: () => onSelect(tab),
              ),
            const Spacer(),
            _RailItem(
              key: const ValueKey('rail-activity'),
              label: unread == 0 ? 'Activity' : 'Activity ($unread new)',
              selected: false,
              dot: unread > 0,
              onTap: onActivity,
            ),
            _RailItem(
              key: const ValueKey('rail-settings'),
              label: 'Settings',
              selected: false,
              onTap: onSettings,
            ),
          ],
        ),
      ),
    );
  }
}

class _RailItem extends StatelessWidget {
  const _RailItem({
    required this.label,
    required this.selected,
    required this.onTap,
    this.dot = false,
    super.key,
  });

  final String label;
  final bool selected;
  final bool dot;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Semantics(
        button: true,
        selected: selected,
        label: label,
        child: ExcludeSemantics(
          child: Material(
            color: selected ? palette.soft : Colors.transparent,
            borderRadius: BorderRadius.circular(14),
            clipBehavior: Clip.antiAlias,
            child: InkWell(
              onTap: onTap,
              child: Container(
                height: 46,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                alignment: Alignment.centerLeft,
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        label,
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w700,
                          color: selected ? palette.primary : palette.sub,
                        ),
                      ),
                    ),
                    if (dot)
                      Container(
                        key: const ValueKey('unread-dot'),
                        width: 9,
                        height: 9,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: palette.danger,
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
