import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/globe.dart';

const _wideAt = 760.0;
const _drawerWarnPercent = 80;

class HomeScreen extends StatelessWidget {
  const HomeScreen({required this.controller, super.key});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final status = controller.status;
    if (status == null) {
      return controller.unreachable
          ? _Message(
              icon: Icons.cloud_off,
              title: "Can't reach the server",
              body: 'Check that this device is on your home network and the server is running.',
              action: 'Try again',
              onAction: controller.poll,
            )
          : const Center(child: CircularProgressIndicator());
    }
    final robot = status.robot;
    if (robot == null) {
      return _Message(
        icon: Icons.hourglass_empty,
        title: 'Waiting for the robot',
        body: 'The server is running, but robot ${status.serial} has not connected yet. '
            'Run scripts/connect-robot.sh next to the robot to point it here.',
      );
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final figure = _RobotFigure(status: status, robot: robot);
        final details = _Details(controller: controller, status: status, robot: robot);
        final isWide = constraints.maxWidth >= _wideAt;
        return SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
          child: Center(
            child: ConstrainedBox(
              constraints: BoxConstraints(maxWidth: isWide ? 920 : 460),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _Header(status: status, unreachable: controller.unreachable),
                  const SizedBox(height: 20),
                  if (isWide)
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(child: figure),
                        const SizedBox(width: 56),
                        Expanded(child: details),
                      ],
                    )
                  else ...[
                    figure,
                    const SizedBox(height: 24),
                    details,
                  ],
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.status, required this.unreachable});

  final ServerStatus status;
  final bool unreachable;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final (icon, label, color) = unreachable
        ? (Icons.cloud_off, 'Server unreachable', palette.critical)
        : status.online
            ? (Icons.check_circle_outline, 'Online', palette.good)
            : (Icons.wifi_off, _offlineLabel(), palette.warn);
    return Row(
      children: [
        const Expanded(
          child: Text('Litter box', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
        ),
        Icon(icon, size: 18, color: color),
        const SizedBox(width: 6),
        Text(label, style: TextStyle(fontSize: 14, color: color)),
      ],
    );
  }

  String _offlineLabel() {
    final seen = status.lastSeen;
    return seen == null ? 'Offline' : 'Offline since ${relativeTime(seen, DateTime.now())}';
  }
}

class _RobotFigure extends StatelessWidget {
  const _RobotFigure({required this.status, required this.robot});

  final ServerStatus status;
  final RobotView robot;

  @override
  Widget build(BuildContext context) {
    final litter = robot.litterPercent;
    return Column(
      children: [
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 280),
          child: Globe(
            statusLabel: robot.statusLabel,
            detail: litter == null ? 'No litter reading yet' : 'Litter $litter%',
            litterPercent: litter,
            turning: status.online && robot.isCleaning,
          ),
        ),
        const SizedBox(height: 16),
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 340),
          child: _Drawer(robot: robot),
        ),
      ],
    );
  }
}

class _Drawer extends StatelessWidget {
  const _Drawer({required this.robot});

  final RobotView robot;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final percent = robot.drawerPercent;
    final isFull = robot.drawerFull;
    final isHigh = !isFull && (percent ?? 0) >= _drawerWarnPercent;
    final color = isFull ? palette.critical : (isHigh ? palette.warn : palette.waste);
    final reading = isFull
        ? 'Full'
        : robot.drawerIsEstimate
            ? 'Measures after next cycle'
            : percent == null
                ? 'No reading'
                : '$percent%';
    return Semantics(
      label: 'Waste drawer: $reading',
      child: ExcludeSemantics(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Expanded(child: Text('Waste drawer', style: TextStyle(fontSize: 16))),
                if (isFull || isHigh) ...[
                  Icon(Icons.warning_amber_rounded, size: 18, color: color),
                  const SizedBox(width: 4),
                ],
                Text(
                  reading,
                  style: TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                    color: isFull || isHigh ? color : palette.ink,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: SizedBox(
                height: 12,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    ColoredBox(color: palette.hairline),
                    if (percent != null && !robot.drawerIsEstimate)
                      FractionallySizedBox(
                        alignment: Alignment.centerLeft,
                        widthFactor: percent.clamp(0, 100) / 100,
                        child: ColoredBox(color: color),
                      ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Details extends StatelessWidget {
  const _Details({required this.controller, required this.status, required this.robot});

  final AppController controller;
  final ServerStatus status;
  final RobotView robot;

  Future<void> _send(
    BuildContext context, {
    required String title,
    required String body,
    required String confirm,
    required String action,
    required String sentMessage,
  }) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Not now'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: Text(confirm),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await controller.action(action);
      messenger.showSnackBar(SnackBar(content: Text(sentMessage)));
    } on ApiException catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final isLocked = robot.settings.keypadLockout == true;
    final canSend = status.online && !controller.unreachable && !isLocked;
    final lastVisit = robot.lastVisitAt;
    final weight = robot.lastWeightLb;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final alert in status.alerts) ...[
          _AlertLine(alert: alert),
          const SizedBox(height: 10),
        ],
        if (status.alerts.isNotEmpty) const SizedBox(height: 6),
        Row(
          children: [
            Expanded(
              flex: 3,
              child: FilledButton(
                onPressed: canSend && !robot.isCleaning
                    ? () => _send(
                          context,
                          title: 'Clean now?',
                          body: 'The globe turns once to clean. '
                              'It stops on its own if a cat steps in.',
                          confirm: 'Clean now',
                          action: 'clean',
                          sentMessage: 'Clean cycle sent to the robot',
                        )
                    : null,
                child: Text(robot.isCleaning ? 'Cleaning' : 'Clean now'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              flex: 2,
              child: OutlinedButton(
                onPressed: canSend
                    ? () => _send(
                          context,
                          title: 'Reset the robot?',
                          body: 'This clears a full drawer alert after you empty it. '
                              'It can also resume a cycle that paused for a cat.',
                          confirm: 'Reset',
                          action: 'reset',
                          sentMessage: 'Reset sent to the robot',
                        )
                    : null,
                child: const Text('Reset'),
              ),
            ),
          ],
        ),
        if (isLocked) ...[
          const SizedBox(height: 8),
          Text(
            'Control lock is on. Turn it off in Settings to use these.',
            style: TextStyle(fontSize: 14, color: palette.muted),
          ),
        ],
        const SizedBox(height: 28),
        _Fact(
          label: 'Last visit',
          value: lastVisit == null ? 'None yet' : relativeTime(lastVisit, DateTime.now()),
        ),
        const Divider(),
        _Fact(label: 'Last weight', value: weight == null ? 'None yet' : weightText(weight)),
        const Divider(),
        _Fact(
          label: 'Last visit length',
          value: robot.lastVisitDurationS == null
              ? 'None yet'
              : durationText(robot.lastVisitDurationS!),
        ),
        const Divider(),
        _Fact(
          label: 'Clean cycles',
          value: robot.cleanCycles == null ? 'No reading' : '${robot.cleanCycles}',
        ),
      ],
    );
  }
}

class _AlertLine extends StatelessWidget {
  const _AlertLine({required this.alert});

  final RobotAlert alert;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final color = alert.urgent ? palette.critical : palette.warn;
    return Semantics(
      liveRegion: alert.urgent,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            alert.urgent ? Icons.error_outline : Icons.warning_amber_rounded,
            size: 20,
            color: color,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(alert.message, style: TextStyle(fontSize: 16, height: 1.3, color: color)),
          ),
        ],
      ),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: TextStyle(fontSize: 16, color: context.palette.muted)),
          Text(value, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.title,
    required this.body,
    this.action,
    this.onAction,
  });

  final IconData icon;
  final String title;
  final String body;
  final String? action;
  final Future<void> Function()? onAction;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 400),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 40, color: palette.muted),
              const SizedBox(height: 16),
              Text(
                title,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 8),
              Text(
                body,
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 16, height: 1.4, color: palette.muted),
              ),
              if (action != null) ...[
                const SizedBox(height: 20),
                FilledButton(onPressed: onAction, child: Text(action!)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
