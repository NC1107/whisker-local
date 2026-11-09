import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../controller.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'import_history_card.dart';
import 'notifications_screen.dart';
import 'robot_parts.dart';
import 'section_card.dart';

Future<String> _installedVersion() async {
  final info = await PackageInfo.fromPlatform();
  return info.version;
}

/// Everything about this app and its server, as opposed to the robot itself.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    required this.controller,
    this.clock = DateTime.now,
    this.version = _installedVersion,
    super.key,
  });

  final AppController controller;
  final DateTime Function() clock;
  final Future<String> Function() version;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  String? _version;

  AppController get _controller => widget.controller;

  @override
  void initState() {
    super.initState();
    _loadVersion();
  }

  Future<void> _loadVersion() async {
    try {
      final version = await widget.version();
      if (!mounted) return;
      setState(() => _version = version);
    } on Exception {
      // The row keeps its dash; a missing version is not worth an error.
    }
  }

  void _openNotifications() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => NotificationsScreen(controller: _controller),
      ),
    );
  }

  Future<void> _forgetKey() async {
    final agreed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sign out?'),
        content: const Text('You need the access key to sign back in'),
        actions: [
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Sign out'),
          ),
        ],
      ),
    );
    if (agreed ?? false) await _controller.disconnect();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(
        children: [
          TopBar.back(
            title: 'Settings',
            onBack: () => Navigator.of(context).pop(),
          ),
          Expanded(
            child: ListenableBuilder(
              listenable: _controller,
              builder: (context, _) => _body(context),
            ),
          ),
        ],
      ),
    );
  }

  Widget _body(BuildContext context) {
    final wide = isWide(context);
    final notifications = _controller.pushAvailable
        ? _notificationsCard()
        : null;
    final server = _connectionCard();
    final importHistory = ImportHistoryCard(
      key: const ValueKey('import-history'),
      controller: _controller,
      clock: widget.clock,
    );
    final whisker = _whiskerCard();
    final List<Widget> children;
    if (wide) {
      children = [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (notifications != null) ...[
                    notifications,
                    const SizedBox(height: 16),
                  ],
                  importHistory,
                ],
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [server, const SizedBox(height: 16), whisker],
              ),
            ),
          ],
        ),
      ];
    } else {
      children = [?notifications, importHistory, server, whisker];
    }
    return PageBody(gap: wide ? 18 : 14, children: children);
  }

  Widget _notificationsCard() {
    final palette = context.palette;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('Notifications'),
          InkWell(
            key: const ValueKey('open-notifications'),
            onTap: _openNotifications,
            child: SectionRow(
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Alerts on this phone',
                          style: TextStyle(fontSize: 16, color: palette.ink),
                        ),
                        Text(
                          _controller.pushEnabled ? 'On' : 'Off',
                          style: TextStyle(fontSize: 14, color: palette.sub),
                        ),
                      ],
                    ),
                  ),
                  AppIcon(AppIcons.chevronRight, size: 22, color: palette.sub),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _connectionCard() {
    final palette = context.palette;
    final rows = [
      ('Server', _controller.serverAddress),
      ('App version', _version ?? '-'),
    ];
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('Connection'),
          for (final row in rows)
            Container(
              constraints: const BoxConstraints(minHeight: 44),
              padding: const EdgeInsets.symmetric(horizontal: 16),
              decoration: BoxDecoration(
                border: Border(top: BorderSide(color: palette.line)),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    row.$1,
                    style: TextStyle(fontSize: 15, color: palette.sub),
                  ),
                  const SizedBox(width: 12),
                  Flexible(
                    child: Text(
                      row.$2,
                      textAlign: TextAlign.end,
                      style: TextStyle(fontSize: 15, color: palette.ink),
                    ),
                  ),
                ],
              ),
            ),
          SectionRow(
            child: Align(
              alignment: Alignment.centerLeft,
              child: RobotButton(
                key: const ValueKey('forget-key'),
                label: 'Sign out',
                onPressed: _forgetKey,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _whiskerCard() {
    final palette = context.palette;
    return SurfaceCard(
      color: palette.soft,
      shadow: false,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Return to Whisker',
            style: TextStyle(
              fontSize: 17,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Use the reconnect flow in the Whisker app',
            style: TextStyle(fontSize: 15, height: 1.35, color: palette.ink),
          ),
        ],
      ),
    );
  }
}
