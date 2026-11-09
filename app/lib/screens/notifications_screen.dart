import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'notification_parts.dart';
import 'robot_parts.dart';

/// The alert kinds for a server that does not send its own catalog, grouped the
/// way a person thinks about them. A kind the server has that is not listed
/// here still shows up, under Other.
const _groups = <(String, List<(String, String)>)>[
  (
    'Needs you now',
    [
      ('globe_fault', 'Motor fault'),
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
      _settings = before.copyWith(kinds: {...before.kinds, key: on});
    });
    await _save(before, () => _controller.api.updatePushKinds({key: on}));
  }

  /// Runs a save, and puts the screen back as it was if the server says no.
  Future<void> _save(
    PushSettings before,
    Future<PushSettings> Function() request,
  ) async {
    try {
      final saved = await request();
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

  Future<void> _saveOptions(Map<String, Object> patch) async {
    final before = _settings;
    if (before == null) return;
    setState(() => _error = null);
    await _save(before, () => _controller.api.updatePushOptions(patch));
  }

  Future<void> _saveCats(String kind, Map<String, Object> change) =>
      _saveOptions({
        'cats': {kind: change},
      });

  Future<void> _saveThreshold(String setting, int value) async {
    final before = _settings;
    if (before == null) return;
    setState(() {
      _error = null;
      _settings = before.copyWith(
        thresholds: {...before.thresholds, setting: value},
      );
    });
    try {
      await _controller.api.updateAlertSettings({setting: value});
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
                title: 'Notifications on this phone',
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
                  for (final kind in group.$2) ..._kindRows(settings, kind),
                ],
              ),
            ),
          if (settings.catalog.isNotEmpty) _deliveryCard(settings),
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

  List<Widget> _kindRows(PushSettings settings, (String, String) kind) {
    final key = kind.$1;
    final on = settings.kinds[key] ?? false;
    final info = settings.catalog.where((k) => k.key == key).firstOrNull;
    final threshold = info?.threshold;
    final value = threshold == null
        ? null
        : settings.thresholds[threshold.setting];
    return [
      _SwitchRow(
        switchKey: ValueKey('push-kind-$key'),
        title: kind.$2,
        value: on,
        onChanged: (on) => _toggleKind(key, on),
      ),
      if (on && threshold != null && value != null)
        ThresholdRow(
          kind: key,
          threshold: threshold,
          value: value,
          onSaved: (next) => _saveThreshold(threshold.setting, next),
        ),
      if (on && info != null && info.supportsCats && settings.cats.isNotEmpty)
        CatChoiceRow(
          kind: key,
          cats: settings.cats,
          choice: settings.options.cats[key] ?? CatChoice.anyone,
          onChanged: (change) => _saveCats(key, change),
        ),
      if (on && key == 'daily_summary')
        TimeRow(
          rowKey: const ValueKey('push-summary-time'),
          label: 'Send at',
          value:
              '${settings.options.dailySummaryHour.toString().padLeft(2, '0')}:00',
          onPicked: (time) => _saveOptions({'daily_summary_hour': time.hour}),
        ),
    ];
  }

  Widget _deliveryCard(PushSettings settings) {
    final palette = context.palette;
    final quiet = settings.options.quietHours;
    return SurfaceCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
            child: Semantics(
              header: true,
              child: Text(
                'Delivery',
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  color: palette.ink,
                ),
              ),
            ),
          ),
          _SwitchRow(
            switchKey: const ValueKey('push-quiet'),
            title: 'Quiet hours',
            detail: 'Urgent alerts still come through',
            value: quiet.enabled,
            onChanged: (on) => _saveOptions({
              'quiet_hours': {'enabled': on},
            }),
            first: true,
          ),
          if (quiet.enabled) ...[
            TimeRow(
              rowKey: const ValueKey('push-quiet-start'),
              label: 'From',
              value: quiet.start,
              onPicked: (time) => _saveOptions({
                'quiet_hours': {'start': hhmm(time)},
              }),
            ),
            TimeRow(
              rowKey: const ValueKey('push-quiet-end'),
              label: 'Until',
              value: quiet.end,
              onPicked: (time) => _saveOptions({
                'quiet_hours': {'end': hhmm(time)},
              }),
            ),
            _SwitchRow(
              switchKey: const ValueKey('push-quiet-hold'),
              title: 'Send the rest in the morning',
              detail: quiet.hold ? 'Merged into one' : 'Skipped',
              value: quiet.hold,
              onChanged: (on) => _saveOptions({
                'quiet_hours': {'mode': on ? 'hold' : 'drop'},
              }),
            ),
          ],
        ],
      ),
    );
  }

  List<(String, List<(String, String)>)> _groupsFor(PushSettings settings) {
    if (settings.catalog.isNotEmpty) {
      return [
        for (final group in settings.groups)
          (
            group,
            [
              for (final kind in settings.catalog)
                if (kind.group == group && kind.available)
                  (kind.key, kind.name),
            ],
          ),
      ].where((group) => group.$2.isNotEmpty).toList();
    }
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
