import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/common/common.dart';

/// What the person sees about the robot at a glance: the words and the dot.
class RobotCondition {
  const RobotCondition(this.label, this.tone);

  final String label;
  final StatusTone tone;

  factory RobotCondition.of(ServerStatus? status, {DateTime? now}) {
    final robot = status?.robot;
    if (status == null || robot == null) {
      return const RobotCondition('Waiting for the robot', StatusTone.offline);
    }
    if (!status.online) {
      return const RobotCondition('Offline', StatusTone.offline);
    }
    if (robot.drawerFull || robot.drawerFullLockout) {
      return const RobotCondition('Drawer full', StatusTone.warning);
    }
    const needsAttention = {'bonnet_removed', 'off', 'changing_filter'};
    if (needsAttention.contains(robot.status)) {
      return RobotCondition(robot.statusLabel, StatusTone.warning);
    }
    final cleaned = status.lastCleanAt;
    if (robot.status == 'ready' && cleaned != null && now != null) {
      // On a phone the line wraps, and "8 hr" split from "ago" reads badly.
      final age = agoText(cleaned, now).replaceAll(' ', ' ');
      return RobotCondition(
        '${robot.statusLabel}, cleaned $age',
        StatusTone.ready,
      );
    }
    return RobotCondition(robot.statusLabel, StatusTone.ready);
  }
}

/// The reason the numbers on screen may be old, or null when they are live.
String? connectionNotice(
  ServerStatus? status, {
  required bool unreachable,
  required DateTime now,
}) {
  if (status == null) return null;
  if (unreachable) {
    return "Can't reach the server. Showing the last known state.";
  }
  if (!status.brokerConnected) {
    return 'The server lost its connection to the broker. Robot updates are paused.';
  }
  if (status.online) return null;
  final heard = status.lastSeen;
  if (heard == null) return 'Robot offline. It has not connected yet.';
  final age = now.difference(heard);
  if (age.inHours >= 24) {
    return 'Robot offline. Last heard on ${DateFormat.MMMd().format(heard)}.';
  }
  return 'Robot offline. Last heard from ${relativeTime(heard, now)}.';
}

/// Full-width strip under the top bar, as in the offline design.
class ConnectionStrip extends StatelessWidget {
  const ConnectionStrip(this.message, {super.key});

  final String message;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      liveRegion: true,
      child: Container(
        key: const ValueKey('connection-strip'),
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        color: palette.warnSoft,
        child: Text(
          message,
          textAlign: TextAlign.center,
          style: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w700,
            color: palette.warn,
          ),
        ),
      ),
    );
  }
}

/// The low-litter threshold from the alert settings, or null when it cannot
/// be read, in which case the gauges use their own default.
Future<int?> fetchLitterLowAt(AppController controller) async {
  try {
    return (await controller.api.alertSettings()).litterLowPercent;
  } on ApiException {
    return null;
  }
}

/// The robot drawing, its name and state, and the two gauges.
/// [actions] turns it into the wide layout with buttons on the right.
class RobotSummaryCard extends StatelessWidget {
  const RobotSummaryCard({
    required this.status,
    this.lowLitterAt = 30,
    this.actions,
    this.wide,
    this.onTap,
    this.now,
    super.key,
  });

  final ServerStatus? status;
  final int lowLitterAt;
  final Widget? actions;

  /// The roomy layout of a wide window. It is on whenever [actions] is given.
  final bool? wide;
  final VoidCallback? onTap;

  /// The time the status line counts from, so it ages with the screen's clock.
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final condition = RobotCondition.of(status, now: now);
    final robot = status?.robot;
    final wide = this.wide ?? actions != null;
    final palette = context.palette;
    final title = Text(
      'Litter box',
      style: TextStyle(
        fontSize: wide ? 26 : 22,
        fontWeight: FontWeight.w700,
        color: palette.ink,
      ),
    );
    final state = Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(
        children: [
          StatusDot(condition.tone),
          const SizedBox(width: 7),
          Flexible(
            child: Text(
              condition.label,
              style: TextStyle(fontSize: wide ? 17 : 16, color: palette.sub),
            ),
          ),
        ],
      ),
    );
    final gauges = Row(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        WasteGauge(
          percent: robot?.drawerPercent,
          full: robot?.drawerFull ?? false,
          estimate: robot?.drawerIsEstimate ?? false,
          large: wide,
        ),
        SizedBox(width: wide ? 32 : 26),
        LitterGauge(
          percent: robot?.litterPercent,
          lowAt: lowLitterAt,
          large: wide,
        ),
      ],
    );

    final Widget content = LayoutBuilder(
      builder: (context, box) {
        if (!wide) return _phoneRow(box.maxWidth, title, state, gauges);
        final identity = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [title, state],
        );
        if (box.maxWidth >= 760) {
          return Row(
            children: [
              const RobotGlyph(),
              const SizedBox(width: 28),
              Expanded(child: identity),
              const SizedBox(width: 28),
              gauges,
              if (actions != null) ...[const SizedBox(width: 28), actions!],
            ],
          );
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const RobotGlyph(),
                const SizedBox(width: 28),
                Expanded(child: identity),
              ],
            ),
            const SizedBox(height: 18),
            Wrap(
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: 28,
              runSpacing: 18,
              children: [gauges, ?actions],
            ),
          ],
        );
      },
    );
    return SurfaceCard(
      key: const ValueKey('robot-card'),
      padding: EdgeInsets.all(wide ? 24 : 18),
      onTap: onTap,
      semanticLabel: onTap == null
          ? null
          : 'Litter box, ${condition.label}. Open robot controls',
      child: content,
    );
  }
}

/// Under this inner width the drawing shrinks so the gauge words keep their
/// margin inside the card.
const _compactCardWidth = 280.0;

Widget _phoneRow(double width, Widget title, Widget state, Widget gauges) {
  final compact = width < _compactCardWidth;
  return Row(
    children: [
      RobotGlyph(width: compact ? 88 : 112),
      SizedBox(width: compact ? 14 : 18),
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [title, state, const SizedBox(height: 14), gauges],
        ),
      ),
    ],
  );
}

/// A 48 px button in the design's two weights: filled or outlined.
class RobotButton extends StatelessWidget {
  const RobotButton({
    required this.label,
    required this.onPressed,
    this.filled = false,
    this.busy = false,
    super.key,
  });

  final String label;
  final VoidCallback? onPressed;
  final bool filled;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final enabled = onPressed != null && !busy;
    final disabled = onPressed == null && !busy;
    final ink = disabled
        ? palette.disabledInk
        : filled
        ? palette.onPrimary
        : palette.ink;
    final fill = !filled
        ? Colors.transparent
        : disabled
        ? palette.disabledFill
        : palette.primary;
    return Material(
      color: fill,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: filled
            ? BorderSide.none
            : BorderSide(color: palette.line, width: 2),
      ),
      child: InkWell(
        onTap: enabled ? onPressed : null,
        borderRadius: BorderRadius.circular(14),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 48, minWidth: 48),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 22),
            child: Center(
              widthFactor: 1,
              child: busy
                  ? SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                        strokeWidth: 2.5,
                        color: ink,
                      ),
                    )
                  : Text(
                      label,
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                        color: ink,
                      ),
                    ),
            ),
          ),
        ),
      ),
    );
  }
}

/// The two presses that make the robot move.
enum RobotMove {
  clean(
    action: 'clean',
    title: 'Start a clean cycle?',
    body: 'The globe turns right away. Check that no cat is inside.',
    confirm: 'Clean now',
    failure: 'The cycle did not start.',
  ),
  reset(
    action: 'reset',
    title: 'Press Reset?',
    body:
        'Do this after you empty the waste drawer. '
        'The robot clears its full drawer count and starts working again.',
    confirm: 'Reset',
    failure: 'Reset did not go through.',
  );

  const RobotMove({
    required this.action,
    required this.title,
    required this.body,
    required this.confirm,
    required this.failure,
  });

  final String action;
  final String title;
  final String body;
  final String confirm;
  final String failure;
}

/// Asks before anything moves the robot.
Future<bool> confirmMove(BuildContext context, RobotMove move) async {
  final agreed = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(move.title),
      content: Text(move.body),
      actions: [
        TextButton(
          style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        TextButton(
          style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
          onPressed: () => Navigator.of(context).pop(true),
          child: Text(move.confirm),
        ),
      ],
    ),
  );
  return agreed ?? false;
}

/// Returns the text to show where the person pressed when the press failed,
/// and null when it went through.
Future<String?> sendMove(AppController controller, RobotMove move) async {
  try {
    await controller.action(move.action);
    return null;
  } on ApiException catch (error) {
    return '${move.failure} ${error.message}';
  }
}

/// Small red-free error line shown right under the control that failed.
class InlineError extends StatelessWidget {
  const InlineError(this.message, {super.key});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      child: Text(
        message,
        style: TextStyle(fontSize: 15, color: context.palette.warn),
      ),
    );
  }
}

/// Clean now and Reset for the wide layout, with their own busy and error state.
class RobotMoveButtons extends StatefulWidget {
  const RobotMoveButtons({required this.controller, super.key});

  final AppController controller;

  @override
  State<RobotMoveButtons> createState() => _RobotMoveButtonsState();
}

class _RobotMoveButtonsState extends State<RobotMoveButtons> {
  RobotMove? _busy;
  String? _error;

  Future<void> _press(RobotMove move) async {
    if (!await confirmMove(context, move) || !mounted) return;
    setState(() {
      _busy = move;
      _error = null;
    });
    final error = await sendMove(widget.controller, move);
    if (!mounted) return;
    setState(() {
      _busy = null;
      _error = error;
    });
  }

  @override
  Widget build(BuildContext context) {
    final status = widget.controller.status;
    final reachable = (status?.online ?? false) && status?.robot != null;
    final cleaning = status?.robot?.isCleaning ?? false;
    final idle = _busy == null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.end,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            RobotButton(
              key: const ValueKey('clean-now'),
              label: cleaning ? 'Cleaning' : 'Clean now',
              filled: true,
              busy: _busy == RobotMove.clean,
              onPressed: reachable && idle && !cleaning
                  ? () => _press(RobotMove.clean)
                  : null,
            ),
            const SizedBox(width: 10),
            RobotButton(
              key: const ValueKey('reset'),
              label: 'Reset',
              busy: _busy == RobotMove.reset,
              onPressed: reachable && idle
                  ? () => _press(RobotMove.reset)
                  : null,
            ),
          ],
        ),
        if (_error != null) ...[
          const SizedBox(height: 8),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 260),
            child: InlineError(_error!),
          ),
        ],
      ],
    );
  }
}

/// Children laid out in equal-width columns with equal-height rows. The column
/// count is fixed with [columns], or as many as fit [minWidth].
class GridRows extends StatelessWidget {
  const GridRows({
    required this.children,
    this.columns,
    this.minWidth = 320,
    this.gap = 16,
    super.key,
  });

  final List<Widget> children;
  final int? columns;
  final double minWidth;
  final double gap;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, box) {
        final fitting =
            columns ?? ((box.maxWidth + gap) / (minWidth + gap)).floor();
        final count = columns == null && fitting > children.length
            ? children.length
            : fitting;
        final perRow = count < 1 ? 1 : count;
        final rows = <Widget>[];
        for (var start = 0; start < children.length; start += perRow) {
          final cells = <Widget>[];
          for (var i = start; i < start + perRow; i++) {
            if (i > start) cells.add(SizedBox(width: gap));
            cells.add(
              Expanded(
                child: i < children.length ? children[i] : const SizedBox(),
              ),
            );
          }
          if (rows.isNotEmpty) rows.add(SizedBox(height: gap));
          rows.add(
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: cells,
              ),
            ),
          );
        }
        return Column(children: rows);
      },
    );
  }
}

/// A centered spinner that screen readers announce with [label].
class LoadingSpinner extends StatelessWidget {
  const LoadingSpinner(this.label, {super.key});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Semantics(label: label, child: const CircularProgressIndicator()),
    );
  }
}
