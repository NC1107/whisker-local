import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'robot_parts.dart';

/// The alert kinds, grouped the way a person thinks about them. A kind the
/// server has that is not listed here still shows up, under Other, so an older
/// app keeps working with a newer server.
const _groups = <(String, List<(String, String)>)>[
  (
    'Needs you now',
    [
      ('globe_fault', 'Globe motor fault'),
      ('bonnet_removed', 'Bonnet removed'),
      ('drawer_full_lockout', 'Drawer full, robot stopped'),
      ('cat_sensor_blocked', 'Cat sensor blocked'),
      ('excess_weight', 'Too much weight'),
      ('stuck_cycle', 'Cycle stuck'),
      ('offline', 'Robot offline'),
    ],
  ),
  (
    'Litter and waste',
    [
      ('drawer_full', 'Waste drawer full'),
      ('drawer_level', 'Waste drawer filling up'),
      ('litter_low', 'Litter is low'),
      ('hopper_empty', 'Hopper empty'),
      ('hopper_disconnected', 'Hopper disconnected'),
    ],
  ),
  (
    'Upkeep',
    [
      ('deep_clean_due', 'Deep clean due'),
      ('filter_due', 'Filter due'),
      ('globe_clean_due', 'Globe clean due'),
    ],
  ),
  (
    'Cats',
    [
      ('no_visit', 'No visit for a while'),
      ('weight_change', 'Weight changed'),
      ('visit', 'Every visit'),
      ('long_visit', 'Long visit'),
      ('frequent_visits', 'Many visits in an hour'),
    ],
  ),
  (
    'Routine',
    [
      ('cycle_completed', 'Clean cycle finished'),
      ('panel_lock_changed', 'Control lock changed'),
    ],
  ),
];

String _humanize(String key) {
  final words = key.replaceAll('_', ' ');
  return words.isEmpty ? key : words[0].toUpperCase() + words.substring(1);
}

/// Opt in to push notifications on this phone, and pick which alerts are sent.
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({required this.controller, super.key});

  final AppController controller;

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  PushSettings? _settings;
  String? _loadError;
  String? _error;
  String? _testResult;
  bool _switching = false;
  bool _testing = false;

  AppController get _controller => widget.controller;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final settings = await _controller.api.pushSettings();
      if (!mounted) return;
      setState(() {
        _settings = settings;
        _loadError = null;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _loadError = error.message);
    }
  }

  Future<void> _toggleDevice(bool on) async {
    setState(() {
      _switching = true;
      _error = null;
      _testResult = null;
    });
    final failure = await _controller.setPushEnabled(on);
    if (!mounted) return;
    setState(() {
      _switching = false;
      _error = failure;
    });
    if (failure == null) await _load();
  }

  Future<void> _toggleKind(String key, bool on) async {
    final before = _settings;
    if (before == null) return;
    setState(() {
      _error = null;
      _settings = PushSettings(
        configured: before.configured,
        devices: before.devices,
        kinds: {...before.kinds, key: on},
        defaults: before.defaults,
      );
    });
    try {
      final saved = await _controller.api.updatePushKinds({key: on});
      if (!mounted) return;
      setState(() => _settings = saved);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _settings = before;
        _error = error.message;
      });
    }
  }

  Future<void> _sendTest() async {
    setState(() {
      _testing = true;
      _error = null;
      _testResult = null;
    });
    String? failure;
    String? result;
    try {
      final delivered = await _controller.api.sendTestPush();
      result = delivered == 0 ? 'Sent, not confirmed' : 'Sent';
    } on ApiException catch (error) {
      failure = error.message;
    }
    if (!mounted) return;
    setState(() {
      _testing = false;
      _error = failure;
      _testResult = result;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(
        children: [
          TopBar.back(
            title: 'Notifications',
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
    final palette = context.palette;
    final enabled = _controller.pushEnabled;
    final settings = _settings;
    return PageBody(
      onRefresh: _load,
      children: [
        SurfaceCard(
          padding: EdgeInsets.zero,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _SwitchRow(
                switchKey: const ValueKey('push-device'),
                title: 'Alerts on this phone',
                detail: enabled ? 'On' : 'Off',
                value: enabled,
                onChanged: _switching ? null : _toggleDevice,
                first: true,
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
                child: Text(
                  'Alert text passes through the push relay',
                  style: TextStyle(fontSize: 14, color: palette.sub),
                ),
              ),
            ],
          ),
        ),
        if (_error != null) InlineError(_error!),
        if (_loadError != null) InlineError(_loadError!),
        if (enabled && settings != null) ...[
          for (final group in _groupsFor(settings))
            SurfaceCard(
              padding: EdgeInsets.zero,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
                    child: Semantics(
                      header: true,
                      child: Text(
                        group.$1,
                        style: TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w700,
                          color: palette.ink,
                        ),
                      ),
                    ),
                  ),
                  for (final kind in group.$2)
                    _SwitchRow(
                      switchKey: ValueKey('push-kind-${kind.$1}'),
                      title: kind.$2,
                      value: settings.kinds[kind.$1] ?? false,
                      onChanged: (on) => _toggleKind(kind.$1, on),
                    ),
                ],
              ),
            ),
          RobotButton(
            key: const ValueKey('push-test'),
            label: 'Send a test notification',
            busy: _testing,
            onPressed: _testing ? null : _sendTest,
          ),
          if (_testResult != null)
            Text(
              _testResult!,
              style: TextStyle(fontSize: 15, color: palette.sub),
            ),
        ],
      ],
    );
  }

  List<(String, List<(String, String)>)> _groupsFor(PushSettings settings) {
    final known = {
      for (final group in _groups)
        for (final kind in group.$2) kind.$1,
    };
    final extra = [
      for (final key in settings.kinds.keys)
        if (!known.contains(key)) (key, _humanize(key)),
    ];
    return [
      for (final group in _groups)
        (
          group.$1,
          [
            for (final kind in group.$2)
              if (settings.kinds.containsKey(kind.$1)) kind,
          ],
        ),
      if (extra.isNotEmpty) ('Other', extra),
    ].where((group) => group.$2.isNotEmpty).toList();
  }
}

class _SwitchRow extends StatelessWidget {
  const _SwitchRow({
    required this.switchKey,
    required this.title,
    required this.value,
    required this.onChanged,
    this.detail,
    this.first = false,
  });

  final Key switchKey;
  final String title;
  final String? detail;
  final bool value;
  final ValueChanged<bool>? onChanged;
  final bool first;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Container(
      constraints: const BoxConstraints(minHeight: 56),
      padding: const EdgeInsets.fromLTRB(16, 4, 8, 4),
      decoration: BoxDecoration(
        border: first ? null : Border(top: BorderSide(color: palette.line)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(title, style: TextStyle(fontSize: 16, color: palette.ink)),
                if (detail != null)
                  Text(
                    detail!,
                    style: TextStyle(fontSize: 14, color: palette.sub),
                  ),
              ],
            ),
          ),
          Semantics(
            label: title,
            child: Switch(key: switchKey, value: value, onChanged: onChanged),
          ),
        ],
      ),
    );
  }
}
