import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'cycle_progress.dart';
import 'robot_parts.dart';
import 'section_card.dart';

const _nightModes = ['off', 'on', 'auto'];
const _nightLabels = {'off': 'Off', 'on': 'On', 'auto': 'Auto'};
const _waitMinutes = [3, 7, 15, 25, 30];

String _clock(int minuteOfDay) {
  final time = DateTime(2000, 1, 1, minuteOfDay ~/ 60, minuteOfDay % 60);
  return minuteOfDay % 60 == 0
      ? DateFormat.j().format(time)
      : DateFormat.jm().format(time);
}

String dueText(MaintenanceItem item) {
  final days = item.daysUntilDue;
  if (item.lastDone == null || days == null) return 'Not tracked yet';
  if (days > 1) return 'Due in $days days';
  if (days == 1) return 'Due tomorrow';
  if (days == 0) return 'Due today';
  if (days == -1) return 'Overdue by 1 day';
  return 'Overdue by ${-days} days';
}

/// The robot's controls: what it does, how it is set, and what needs upkeep.
class RobotScreen extends StatefulWidget {
  const RobotScreen({
    required this.controller,
    this.clock = DateTime.now,
    super.key,
  });

  final AppController controller;
  final DateTime Function() clock;

  @override
  State<RobotScreen> createState() => _RobotScreenState();
}

class _RobotScreenState extends State<RobotScreen> {
  final _upkeepKey = GlobalKey();

  int _lowLitterAt = 30;
  Maintenance? _maintenance;
  String? _maintenanceError;
  int _maintenanceLoad = 0;

  // Values the person just chose, shown until the robot confirms or refuses.
  final Map<String, Object> _chosen = {};
  RobotMove? _moving;
  String? _tileError;
  String? _brightnessError;
  String? _waitError;
  String? _sleepError;
  String? _upkeepError;
  String? _litterError;
  String? _litterBusy;
  String? _upkeepBusy;
  int? _brightnessDraft;

  AppController get _controller => widget.controller;

  @override
  void initState() {
    super.initState();
    _loadMaintenance();
    _loadLowLitter();
  }

  Future<void> _loadLowLitter() async {
    final lowAt = await fetchLitterLowAt(_controller);
    if (!mounted || lowAt == null) return;
    setState(() => _lowLitterAt = lowAt);
  }

  Future<void> _loadMaintenance() async {
    final id = ++_maintenanceLoad;
    try {
      final loaded = await _controller.api.maintenance();
      if (!mounted || id != _maintenanceLoad) return;
      setState(() {
        _maintenance = loaded;
        _maintenanceError = null;
      });
    } on ApiException catch (error) {
      if (!mounted || id != _maintenanceLoad) return;
      setState(() => _maintenanceError = error.message);
    }
  }

  Future<void> _refresh() async {
    await Future.wait([
      _controller.poll(),
      _loadMaintenance(),
      _loadLowLitter(),
    ]);
  }

  bool get _canControl {
    final status = _controller.status;
    return (status?.online ?? false) && status?.robot != null;
  }

  Future<void> _save(
    String key,
    Object value,
    Map<String, Object> patch,
    void Function(String? error) report,
  ) async {
    if (_chosen.containsKey(key)) return;
    setState(() {
      _chosen[key] = value;
      report(null);
    });
    String? failure;
    try {
      await _controller.updateSettings(patch);
    } on ApiException catch (error) {
      failure = error.message;
    }
    if (!mounted) return;
    setState(() {
      _chosen.remove(key);
      report(failure);
    });
  }

  Future<void> _move(RobotMove move) async {
    if (_moving != null) return;
    if (!await confirmMove(context, move) || !mounted) return;
    setState(() {
      _moving = move;
      _tileError = null;
    });
    final error = await sendMove(_controller, move);
    if (!mounted) return;
    setState(() {
      _moving = null;
      _tileError = error;
    });
  }

  Future<void> _markDone(MaintenanceItem item) async {
    if (_upkeepBusy != null) return;
    final agreed = await _confirm(
      title: 'Mark ${item.label.toLowerCase()} done?',
      body: 'Starts its countdown from today',
      confirm: 'Mark done',
    );
    if (!agreed || !mounted) return;
    setState(() {
      _upkeepBusy = item.kind;
      _upkeepError = null;
    });
    String? failure;
    try {
      await _controller.api.logMaintenance(item.kind);
    } on ApiException catch (error) {
      failure = error.message;
    }
    if (failure == null) await _loadMaintenance();
    if (!mounted) return;
    setState(() {
      _upkeepBusy = null;
      _upkeepError = failure == null
          ? null
          : '${item.label} not marked done: $failure';
    });
  }

  Future<void> _calibrate(String point) async {
    if (_litterBusy != null) return;
    final agreed = await _confirm(
      title: 'Save the $point level?',
      body: point == 'full'
          ? 'Fill the globe to the line first'
          : 'Empty the globe first',
      confirm: 'Save $point level',
    );
    if (!agreed || !mounted) return;
    setState(() {
      _litterBusy = point;
      _litterError = null;
    });
    String? failure;
    try {
      await _controller.calibrateLitter(point);
    } on ApiException catch (error) {
      failure = error.message;
    }
    if (!mounted) return;
    setState(() {
      _litterBusy = null;
      _litterError = failure;
    });
  }

  Future<void> _clearCalibration() async {
    if (_litterBusy != null) return;
    final agreed = await _confirm(
      title: 'Clear the litter levels?',
      body: 'The gauge returns to its learned scale',
      confirm: 'Clear levels',
    );
    if (!agreed || !mounted) return;
    setState(() {
      _litterBusy = 'clear';
      _litterError = null;
    });
    String? failure;
    try {
      await _controller.clearLitterCalibration();
    } on ApiException catch (error) {
      failure = error.message;
    }
    if (!mounted) return;
    setState(() {
      _litterBusy = null;
      _litterError = failure;
    });
  }

  Future<bool> _confirm({
    required String title,
    required String body,
    required String confirm,
  }) async {
    final agreed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(confirm),
          ),
        ],
      ),
    );
    return agreed ?? false;
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: _controller,
      builder: (context, _) {
        final status = _controller.status;
        final notice = connectionNotice(
          status,
          unreachable: _controller.unreachable,
          now: widget.clock(),
        );
        return Column(
          children: [
            if (notice != null) ConnectionStrip(notice),
            Expanded(
              child: status == null
                  ? const LoadingSpinner('Loading')
                  : _content(context, status),
            ),
          ],
        );
      },
    );
  }

  Widget _content(BuildContext context, ServerStatus status) {
    final wide = isWide(context);
    final summary = RobotSummaryCard(
      status: status,
      now: widget.clock(),
      lowLitterAt: _lowLitterAt,
      wide: wide,
    );
    final tiles = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (status.robot?.status == 'empty_cycle') ...[
          const NoticeCard(
            key: ValueKey('empty-parked'),
            lead: 'Litter is in the drawer',
            message:
                'Press Cycle or Reset on the robot to bring the globe home',
            tone: NoticeTone.warning,
          ),
          SizedBox(height: wide ? 16 : 10),
        ],
        GridRows(
          columns: wide ? 3 : 2,
          gap: wide ? 16 : 10,
          children: _tiles(status),
        ),
        if (controlsOffReason(status) case final reason?) ...[
          const SizedBox(height: 10),
          Text(
            reason,
            key: const ValueKey('controls-off'),
            style: TextStyle(fontSize: 14, color: context.palette.sub),
          ),
        ],
        if (_tileError != null) ...[
          const SizedBox(height: 10),
          InlineError(_tileError!),
        ],
      ],
    );
    final brightness = _brightnessCard(status);
    final wait = _waitCard(status);
    final schedule = _sleepCard(status);
    final upkeep = _upkeepCard();
    final about = _aboutCard(status);
    final litter = _litterCard(status);
    final empty = _emptyCard(status);
    final hopper = _hopperCard(status);
    final cycle = status.robot?.cycle;
    final List<Widget> children;
    if (wide) {
      children = [
        const SectionHeader('Robot'),
        summary,
        if (cycle != null) CycleProgressCard(cycle: cycle),
        tiles,
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                children: [
                  brightness,
                  const SizedBox(height: 16),
                  wait,
                  const SizedBox(height: 16),
                  schedule,
                  const SizedBox(height: 16),
                  empty,
                  const SizedBox(height: 16),
                  litter,
                ],
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                children: [
                  upkeep,
                  if (hopper != null) ...[const SizedBox(height: 16), hopper],
                  const SizedBox(height: 16),
                  about,
                ],
              ),
            ),
          ],
        ),
      ];
    } else {
      children = [
        summary,
        if (cycle != null) CycleProgressCard(cycle: cycle),
        tiles,
        brightness,
        wait,
        schedule,
        upkeep,
        ?hopper,
        about,
        empty,
        litter,
      ];
    }
    return PageBody(
      gap: wide ? 18 : 14,
      onRefresh: _refresh,
      children: children,
    );
  }

  String _nightMode(RobotSettings settings) =>
      (_chosen['night_light_mode'] ?? settings.nightLightMode ?? 'off')
          as String;

  List<Widget> _tiles(ServerStatus status) {
    final robot = status.robot;
    final settings = robot?.settings ?? const RobotSettings();
    final enabled = _canControl;
    final cleaning = robot?.isCleaning ?? false;
    final parked = robot?.status == 'empty_cycle';
    final locked =
        (_chosen['keypad_lockout'] ?? settings.keypadLockout ?? false) as bool;
    final night = _nightMode(settings);
    final sleeping =
        (_chosen['sleep_enabled'] ?? settings.sleepEnabled ?? false) as bool;
    final filter = _filterItem();
    final sleepAt = settings.sleepMinute;
    final wakeAt = settings.wakeMinute;
    final sleepText = !sleeping
        ? 'Off'
        : sleepAt == null || wakeAt == null
        ? 'On'
        : '${_clock(sleepAt)} to ${_clock(wakeAt)}';
    return [
      _ControlTile(
        tileKey: 'tile-clean',
        icon: AppIcons.clean,
        name: 'Clean now',
        state: parked
            ? 'Emptying'
            : cleaning
            ? 'Cleaning'
            : 'Starts a cycle',
        on: cleaning || _moving == RobotMove.clean,
        busy: _moving == RobotMove.clean,
        onTap: enabled && !cleaning && _moving == null
            ? () => _move(RobotMove.clean)
            : null,
      ),
      _ControlTile(
        tileKey: 'tile-reset',
        icon: AppIcons.reset,
        name: 'Reset',
        state: 'Drawer emptied',
        on: false,
        busy: _moving == RobotMove.reset,
        onTap: enabled && _moving == null ? () => _move(RobotMove.reset) : null,
      ),
      _ControlTile(
        tileKey: 'tile-lock',
        icon: AppIcons.lock,
        name: 'Panel lock',
        state: locked ? 'On' : 'Off',
        on: locked,
        toggle: true,
        busy: _chosen.containsKey('keypad_lockout'),
        onTap: enabled
            ? () => _save('keypad_lockout', !locked, {
                'keypad_lockout': !locked,
              }, (error) => _tileError = error)
            : null,
      ),
      _ControlTile(
        tileKey: 'tile-night',
        icon: AppIcons.nightLight,
        name: 'Night light',
        state: _nightLabels[night] ?? night,
        on: night != 'off',
        toggle: true,
        busy: _chosen.containsKey('night_light_mode'),
        onTap: enabled
            ? () {
                final next =
                    _nightModes[(_nightModes.indexOf(night) + 1) %
                        _nightModes.length];
                _save('night_light_mode', next, {
                  'night_light_mode': next,
                }, (error) => _tileError = error);
              }
            : null,
      ),
      _ControlTile(
        tileKey: 'tile-sleep',
        icon: AppIcons.sleep,
        name: 'Sleep mode',
        state: sleepText,
        on: sleeping,
        toggle: true,
        busy: _chosen.containsKey('sleep_enabled'),
        onTap: enabled
            ? () => _save('sleep_enabled', !sleeping, {
                'sleep_enabled': !sleeping,
              }, (error) => _tileError = error)
            : null,
      ),
      _ControlTile(
        tileKey: 'tile-filter',
        icon: AppIcons.filter,
        name: filter?.label ?? 'Carbon filter',
        state: filter == null
            ? (_maintenanceError == null ? 'Loading' : 'Not available')
            : dueText(filter),
        on: false,
        onTap: () => Scrollable.ensureVisible(
          _upkeepKey.currentContext!,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        ),
      ),
    ];
  }

  MaintenanceItem? _filterItem() {
    for (final item in _maintenance?.items ?? const <MaintenanceItem>[]) {
      if (item.kind == 'filter') return item;
    }
    return null;
  }

  Widget _brightnessCard(ServerStatus status) {
    final palette = context.palette;
    final settings = status.robot?.settings;
    final saved =
        (_chosen['night_light_brightness'] ??
                settings?.nightLightBrightness ??
                0)
            as int;
    final shown = _brightnessDraft ?? saved;
    final enabled =
        _canControl && !_chosen.containsKey('night_light_brightness');
    return SectionCard(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Expanded(
                  child: Text(
                    'Night light brightness',
                    style: TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                      color: palette.ink,
                    ),
                  ),
                ),
                Text(
                  settings == null ? '-' : '$shown%',
                  style: TextStyle(fontSize: 15, color: palette.sub),
                ),
              ],
            ),
            SizedBox(
              height: 44,
              child: SliderTheme(
                data: SliderTheme.of(context).copyWith(
                  activeTrackColor: palette.primary,
                  inactiveTrackColor: palette.track,
                  thumbColor: palette.primary,
                  disabledActiveTrackColor: palette.disabledFill,
                  disabledInactiveTrackColor: palette.disabledFill,
                  disabledThumbColor: palette.disabledInk,
                  overlayColor: palette.primary.withValues(alpha: 0.12),
                  activeTickMarkColor: Colors.transparent,
                  inactiveTickMarkColor: Colors.transparent,
                  thumbShape: const RoundSliderThumbShape(
                    elevation: 0,
                    pressedElevation: 0,
                  ),
                ),
                child: Slider(
                  key: const ValueKey('brightness'),
                  value: shown.toDouble(),
                  min: 0,
                  max: 100,
                  divisions: 20,
                  semanticFormatterCallback: (value) =>
                      'Night light brightness ${value.round()} percent',
                  onChanged: enabled
                      ? (value) =>
                            setState(() => _brightnessDraft = value.round())
                      : null,
                  onChangeEnd: enabled
                      ? (value) async {
                          final level = value.round();
                          setState(() => _brightnessDraft = null);
                          if (level == settings?.nightLightBrightness) return;
                          await _save('night_light_brightness', level, {
                            'night_light_brightness': level,
                          }, (error) => _brightnessError = error);
                        }
                      : null,
                ),
              ),
            ),
            if (_brightnessError != null) InlineError(_brightnessError!),
          ],
        ),
      ),
    );
  }

  Future<void> _pickSleepTime(String key, int? current) async {
    final minute = current ?? (key == 'sleep_minute' ? 22 * 60 : 6 * 60);
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: minute ~/ 60, minute: minute % 60),
      helpText: key == 'sleep_minute' ? 'Go to sleep at' : 'Wake up at',
    );
    if (picked == null || !mounted) return;
    final chosen = picked.hour * 60 + picked.minute;
    if (chosen == current) return;
    await _save(key, chosen, {key: chosen}, (error) => _sleepError = error);
  }

  Widget _sleepCard(ServerStatus status) {
    final palette = context.palette;
    final settings = status.robot?.settings;
    int? minuteOf(String key, int? reported) =>
        (_chosen[key] ?? reported) as int?;
    final sleepAt = minuteOf('sleep_minute', settings?.sleepMinute);
    final wakeAt = minuteOf('wake_minute', settings?.wakeMinute);
    Widget pick(String name, String key, int? minute) {
      final busy = _chosen.containsKey(key);
      final time = minute == null ? 'Not set' : _clock(minute);
      return Expanded(
        child: Row(
          children: [
            Expanded(
              child: Text(
                name,
                style: TextStyle(fontSize: 16, color: palette.sub),
              ),
            ),
            _SmallButton(
              key: ValueKey('sleep-time-$key'),
              label: time,
              semanticLabel: '$name time, $time. Change',
              busy: busy,
              onPressed: _canControl && !busy
                  ? () => _pickSleepTime(key, minute)
                  : null,
            ),
          ],
        ),
      );
    }

    return SectionCard(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Quiet hours',
              style: TextStyle(
                fontSize: 17,
                fontWeight: FontWeight.w700,
                color: palette.ink,
              ),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                pick('Sleep', 'sleep_minute', sleepAt),
                const SizedBox(width: 16),
                pick('Wake', 'wake_minute', wakeAt),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              'Only while Sleep mode is on',
              style: TextStyle(fontSize: 14, color: palette.sub),
            ),
            if (_sleepError != null) ...[
              const SizedBox(height: 8),
              InlineError(_sleepError!),
            ],
          ],
        ),
      ),
    );
  }

  Widget _waitCard(ServerStatus status) {
    final palette = context.palette;
    final settings = status.robot?.settings;
    final current =
        (_chosen['clean_cycle_wait_minutes'] ?? settings?.cleanCycleWaitMinutes)
            as int?;
    final enabled =
        _canControl && !_chosen.containsKey('clean_cycle_wait_minutes');
    return SectionCard(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Wait before cleaning',
              style: TextStyle(
                fontSize: 17,
                fontWeight: FontWeight.w700,
                color: palette.ink,
              ),
            ),
            const SizedBox(height: 10),
            Semantics(
              container: true,
              label: 'Minutes to wait',
              child: Opacity(
                opacity: _canControl ? 1 : 0.5,
                child: Container(
                  padding: const EdgeInsets.all(3),
                  decoration: BoxDecoration(
                    color: palette.soft,
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Row(
                    children: [
                      for (final minutes in _waitMinutes)
                        Expanded(
                          child: Semantics(
                            button: true,
                            selected: minutes == current,
                            child: Material(
                              color: minutes == current
                                  ? (_canControl
                                        ? palette.selected
                                        : palette.disabledFill)
                                  : Colors.transparent,
                              borderRadius: BorderRadius.circular(999),
                              child: InkWell(
                                key: ValueKey('wait-$minutes'),
                                borderRadius: BorderRadius.circular(999),
                                onTap: enabled && minutes != current
                                    ? () => _save(
                                        'clean_cycle_wait_minutes',
                                        minutes,
                                        {'clean_cycle_wait_minutes': minutes},
                                        (error) => _waitError = error,
                                      )
                                    : null,
                                child: SizedBox(
                                  height: 44,
                                  child: Center(
                                    child: Text(
                                      '$minutes',
                                      style: TextStyle(
                                        fontSize: 15,
                                        fontWeight: FontWeight.w700,
                                        color:
                                            !_canControl && minutes != current
                                            ? palette.disabledInk
                                            : minutes == current
                                            ? palette.ink
                                            : palette.sub,
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 10),
            Text(
              'Minutes after a cat leaves',
              style: TextStyle(fontSize: 14, color: palette.sub),
            ),
            if (_waitError != null) ...[
              const SizedBox(height: 8),
              InlineError(_waitError!),
            ],
          ],
        ),
      ),
    );
  }

  Widget _upkeepCard() {
    final palette = context.palette;
    final items = _maintenance?.items ?? const <MaintenanceItem>[];
    return SectionCard(
      key: _upkeepKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('Upkeep'),
          if (_maintenance == null && _maintenanceError == null)
            const Padding(
              padding: EdgeInsets.all(16),
              child: LoadingSpinner('Loading upkeep'),
            ),
          if (_maintenance == null && _maintenanceError != null)
            SectionRow(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  InlineError('Upkeep did not load: $_maintenanceError'),
                  const SizedBox(height: 8),
                  _SmallButton(label: 'Try again', onPressed: _loadMaintenance),
                ],
              ),
            ),
          for (final item in items)
            SectionRow(
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          item.label,
                          style: TextStyle(fontSize: 16, color: palette.ink),
                        ),
                        Text(
                          dueText(item),
                          style: TextStyle(
                            fontSize: 14,
                            color: item.isDue ? palette.warn : palette.sub,
                          ),
                        ),
                      ],
                    ),
                  ),
                  _SmallButton(
                    key: ValueKey('done-${item.kind}'),
                    label: 'Mark done',
                    semanticLabel: 'Mark ${item.label} done',
                    busy: _upkeepBusy == item.kind,
                    onPressed: _upkeepBusy == null
                        ? () => _markDone(item)
                        : null,
                  ),
                ],
              ),
            ),
          if (_upkeepError != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
              child: InlineError(_upkeepError!),
            ),
        ],
      ),
    );
  }

  Widget _aboutCard(ServerStatus status) {
    final robot = status.robot;
    final rows = [
      ('Serial', status.serial),
      ('Firmware', robot?.espFirmware ?? 'Unknown'),
      (
        'Wi-Fi signal',
        robot?.wifiRssi == null ? 'Unknown' : signalWord(robot!.wifiRssi!),
      ),
    ];
    final palette = context.palette;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('This robot'),
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
        ],
      ),
    );
  }

  Widget? _hopperCard(ServerStatus status) {
    final hopper = status.robot?.hopper;
    if (hopper == null) return null;
    final palette = context.palette;
    final rows = hopperRows(hopper, widget.clock());
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('Litter hopper'),
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
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 14),
            child: Text(
              hopperSwitchNote,
              style: TextStyle(fontSize: 14, color: palette.sub),
            ),
          ),
        ],
      ),
    );
  }

  Widget _emptyCard(ServerStatus status) {
    final robot = status.robot;
    final palette = context.palette;
    final drawerFull =
        (robot?.drawerFull ?? false) || (robot?.drawerFullLockout ?? false);
    final busy = _moving == RobotMove.empty;
    final blocked = !_canControl
        ? null
        : drawerFull
        ? 'The waste drawer is full, empty it first'
        : robot?.status == 'empty_cycle'
        ? 'Bring the globe home first'
        : (robot?.isCleaning ?? false)
        ? 'Wait for the cycle to finish'
        : null;
    final ready = _canControl && blocked == null && _moving == null;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('Empty litter'),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: Text(
              blocked ?? 'Dumps all litter into the waste drawer',
              style: TextStyle(fontSize: 14, color: palette.sub),
            ),
          ),
          SectionRow(
            child: Align(
              alignment: Alignment.centerLeft,
              child: _SmallButton(
                key: const ValueKey('empty-litter'),
                label: 'Empty litter',
                busy: busy,
                onPressed: ready ? () => _move(RobotMove.empty) : null,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _litterCard(ServerStatus status) {
    final robot = status.robot;
    final palette = context.palette;
    final calibration = robot?.litterCalibration;
    final enabled = _canControl && _litterBusy == null;
    Widget level(String point, double? mm) => SectionRow(
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${point == 'full' ? 'Full' : 'Empty'} level',
                  style: TextStyle(fontSize: 16, color: palette.ink),
                ),
                Text(
                  mm == null ? 'Not saved' : 'Saved',
                  style: TextStyle(fontSize: 14, color: palette.sub),
                ),
              ],
            ),
          ),
          _SmallButton(
            key: ValueKey('calibrate-$point'),
            label: point == 'full' ? 'Set as full' : 'Set as empty',
            semanticLabel: 'Set the current litter as $point',
            busy: _litterBusy == point,
            onPressed: enabled ? () => _calibrate(point) : null,
          ),
        ],
      ),
    );
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('Litter level'),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            child: Text(
              'Teach the robot full and empty',
              style: TextStyle(fontSize: 14, color: palette.sub),
            ),
          ),
          level('full', calibration?.fullMm),
          level('empty', calibration?.emptyMm),
          if (calibration?.fullMm != null || calibration?.emptyMm != null)
            SectionRow(
              child: Align(
                alignment: Alignment.centerLeft,
                child: _SmallButton(
                  key: const ValueKey('calibrate-clear'),
                  label: 'Clear levels',
                  busy: _litterBusy == 'clear',
                  onPressed: enabled ? _clearCalibration : null,
                ),
              ),
            ),
          if (_litterError != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
              child: InlineError(_litterError!),
            ),
        ],
      ),
    );
  }
}

class _SmallButton extends StatelessWidget {
  const _SmallButton({
    required this.label,
    required this.onPressed,
    this.semanticLabel,
    this.busy = false,
    super.key,
  });

  final String label;
  final String? semanticLabel;
  final VoidCallback? onPressed;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      button: true,
      label: semanticLabel ?? label,
      enabled: onPressed != null,
      excludeSemantics: true,
      child: Material(
        color: onPressed == null && !busy ? palette.disabledFill : palette.soft,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: busy ? null : onPressed,
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 44, minWidth: 44),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Center(
                widthFactor: 1,
                child: busy
                    ? SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2.5,
                          color: palette.primary,
                        ),
                      )
                    : Text(
                        label,
                        style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w700,
                          color: onPressed == null && !busy
                              ? palette.disabledInk
                              : palette.primary,
                        ),
                      ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _ControlTile extends StatelessWidget {
  const _ControlTile({
    required this.tileKey,
    required this.icon,
    required this.name,
    required this.state,
    required this.on,
    required this.onTap,
    this.toggle = false,
    this.busy = false,
  });

  final String tileKey;
  final String icon;
  final String name;
  final String state;
  final bool on;
  final bool toggle;
  final bool busy;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final disabled = onTap == null && !busy;
    final ink = disabled
        ? palette.disabledInk
        : on
        ? palette.onPrimary
        : palette.ink;
    final fill = disabled
        ? (on ? palette.disabledFill : palette.card)
        : on
        ? palette.primary
        : palette.card;
    final shape = BorderRadius.circular(20);
    return Semantics(
      button: true,
      toggled: toggle ? on : null,
      enabled: onTap != null,
      label: '$name, $state',
      excludeSemantics: true,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: fill,
          borderRadius: shape,
          boxShadow: palette.shadow,
        ),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            key: ValueKey(tileKey),
            borderRadius: shape,
            onTap: busy ? null : onTap,
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: 112),
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    busy
                        ? SizedBox(
                            width: 26,
                            height: 26,
                            child: Padding(
                              padding: const EdgeInsets.all(3),
                              child: CircularProgressIndicator(
                                strokeWidth: 2.5,
                                color: ink,
                              ),
                            ),
                          )
                        : AppIcon(icon, size: 26, color: ink),
                    const SizedBox(height: 10),
                    Text(
                      name,
                      style: TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w700,
                        color: ink,
                      ),
                    ),
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(
                        state,
                        style: TextStyle(fontSize: 14, color: ink),
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
