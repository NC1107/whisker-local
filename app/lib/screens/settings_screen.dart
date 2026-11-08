import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({required this.controller, super.key});

  final AppController controller;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool _saving = false;
  double? _draggingBrightness;

  Future<void> _save(Map<String, Object> patch) async {
    setState(() => _saving = true);
    try {
      await widget.controller.updateSettings(patch);
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) {
        setState(() {
          _saving = false;
          _draggingBrightness = null;
        });
      }
    }
  }

  Future<void> _pickTime(String field, int? current) async {
    final minutes = current ?? 0;
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: minutes ~/ 60, minute: minutes % 60),
    );
    if (picked != null) await _save({field: picked.hour * 60 + picked.minute});
  }

  @override
  Widget build(BuildContext context) {
    final status = widget.controller.status;
    final robot = status?.robot;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
      children: [
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 560),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Settings',
                  style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
                ),
                if (status != null && robot != null)
                  ..._robotSections(status, robot),
                ..._appSection(),
              ],
            ),
          ),
        ),
      ],
    );
  }

  List<Widget> _robotSections(ServerStatus status, RobotView robot) {
    final settings = robot.settings;
    final enabled = status.online && !_saving;
    final brightness =
        _draggingBrightness ?? (settings.nightLightBrightness ?? 0).toDouble();
    final isScheduled = settings.sleepEnabled == true;
    return [
      if (!status.online) ...[
        const SizedBox(height: 12),
        Text(
          'The robot is offline, so these are read only.',
          style: TextStyle(fontSize: 14, color: context.palette.warn),
        ),
      ],
      const _SectionHeading('Night light'),
      SegmentedButton<String>(
        showSelectedIcon: false,
        segments: const [
          ButtonSegment(value: 'off', label: Text('Off')),
          ButtonSegment(value: 'on', label: Text('On')),
          ButtonSegment(value: 'auto', label: Text('Auto')),
        ],
        selected: {
          if (settings.nightLightMode != null) settings.nightLightMode!,
        },
        emptySelectionAllowed: settings.nightLightMode == null,
        onSelectionChanged: enabled
            ? (selection) {
                if (selection.isNotEmpty) {
                  _save({'night_light_mode': selection.first});
                }
              }
            : null,
      ),
      const SizedBox(height: 12),
      Row(
        children: [
          const Text('Brightness', style: TextStyle(fontSize: 16)),
          Expanded(
            child: Slider(
              value: brightness.clamp(0, 100),
              max: 100,
              divisions: 20,
              label: '${brightness.round()}%',
              semanticFormatterCallback: (value) => '${value.round()} percent',
              onChanged: enabled
                  ? (value) => setState(() => _draggingBrightness = value)
                  : null,
              onChangeEnd: enabled
                  ? (value) => _save({'night_light_brightness': value.round()})
                  : null,
            ),
          ),
          SizedBox(
            width: 48,
            child: Text(
              '${brightness.round()}%',
              textAlign: TextAlign.right,
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
      const _SectionHeading('Wait after a cat leaves'),
      SegmentedButton<int>(
        showSelectedIcon: false,
        segments: const [
          ButtonSegment(value: 3, label: Text('3')),
          ButtonSegment(value: 7, label: Text('7')),
          ButtonSegment(value: 15, label: Text('15')),
          ButtonSegment(value: 25, label: Text('25')),
          ButtonSegment(value: 30, label: Text('30')),
        ],
        selected: {
          if (settings.cleanCycleWaitMinutes != null)
            settings.cleanCycleWaitMinutes!,
        },
        emptySelectionAllowed: true,
        onSelectionChanged: enabled
            ? (selection) {
                if (selection.isNotEmpty) {
                  _save({'clean_cycle_wait_minutes': selection.first});
                }
              }
            : null,
      ),
      const SizedBox(height: 6),
      Text('Minutes before the globe cleans.', style: _noteStyle(context)),
      const _SectionHeading('Controls'),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('Control lock', style: TextStyle(fontSize: 16)),
        subtitle: const Text(
          'Locks the robot\'s buttons. Also blocks Clean and Reset here.',
        ),
        value: settings.keypadLockout == true,
        onChanged: enabled ? (value) => _save({'keypad_lockout': value}) : null,
      ),
      const _SectionHeading('Sleep schedule'),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('Sleep every night', style: TextStyle(fontSize: 16)),
        subtitle: const Text('The robot waits to clean until it wakes.'),
        value: isScheduled,
        onChanged: enabled ? (value) => _save({'sleep_enabled': value}) : null,
      ),
      if (isScheduled) ...[
        _TimeRow(
          label: 'Sleep at',
          minutes: settings.sleepMinute,
          onTap: enabled
              ? () => _pickTime('sleep_minute', settings.sleepMinute)
              : null,
        ),
        const Divider(),
        _TimeRow(
          label: 'Wake at',
          minutes: settings.wakeMinute,
          onTap: enabled
              ? () => _pickTime('wake_minute', settings.wakeMinute)
              : null,
        ),
      ],
      const _SectionHeading('Robot'),
      _InfoRow('Serial', status.serial),
      const Divider(),
      _InfoRow('Wi-Fi firmware', robot.espFirmware ?? 'No reading'),
      const Divider(),
      _InfoRow(
        'Wi-Fi signal',
        robot.wifiRssi == null ? 'No reading' : signalWord(robot.wifiRssi!),
      ),
      if (_saving) ...[
        const SizedBox(height: 16),
        const LinearProgressIndicator(),
      ],
    ];
  }

  List<Widget> _appSection() {
    return [
      const _SectionHeading('Going back to the Whisker app'),
      Text(
        'Run the reconnect flow for this robot in the Whisker app. It puts the robot back on '
        'the cloud, and the history saved here stays on your server.',
        style: _noteStyle(context),
      ),
      const _SectionHeading('This device'),
      if (!kIsWeb) ...[
        _InfoRow('Server', widget.controller.savedUrl),
        const SizedBox(height: 8),
      ],
      OutlinedButton(
        onPressed: () => widget.controller.disconnect(),
        child: const Text('Forget access key'),
      ),
    ];
  }

  TextStyle _noteStyle(BuildContext context) =>
      TextStyle(fontSize: 14, height: 1.35, color: context.palette.muted);
}

class _SectionHeading extends StatelessWidget {
  const _SectionHeading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 28, bottom: 10),
      child: Semantics(
        header: true,
        child: Text(
          text,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 11),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(
            label,
            style: TextStyle(fontSize: 16, color: context.palette.muted),
          ),
          const SizedBox(width: 16),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.right,
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
  }
}

class _TimeRow extends StatelessWidget {
  const _TimeRow({
    required this.label,
    required this.minutes,
    required this.onTap,
  });

  final String label;
  final int? minutes;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final value = minutes == null
        ? 'Not set'
        : TimeOfDay(
            hour: minutes! ~/ 60,
            minute: minutes! % 60,
          ).format(context);
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: const TextStyle(fontSize: 16)),
            Text(
              value,
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w700,
                color: Theme.of(context).colorScheme.primary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
