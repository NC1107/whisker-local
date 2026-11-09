import 'package:flutter/material.dart';

import '../../api.dart';
import '../../theme.dart';
import '../common/common.dart';

Future<void> showInsightChecks(
  BuildContext context,
  List<WaitingCheck> checks,
) => showAppSheet<void>(context, (_) => InsightChecksSheet(checks: checks));

/// What each early-warning check does, what it needs and where it stands.
class InsightChecksSheet extends StatelessWidget {
  const InsightChecksSheet({required this.checks, super.key});

  final List<WaitingCheck> checks;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final byKind = <String, List<WaitingCheck>>{};
    for (final check in checks) {
      byKind.putIfAbsent(check.kind, () => []).add(check);
    }
    return SingleChildScrollView(
      key: const ValueKey('insight-checks-sheet'),
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SheetHandle(),
          const SizedBox(height: 16),
          Semantics(
            header: true,
            child: const Text(
              'How insights work',
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            'Compares against past box data. Offline days are skipped. '
            'No insight is not an all clear, and none of this is a diagnosis.',
            style: TextStyle(fontSize: 15, height: 1.4, color: palette.sub),
          ),
          for (final group in byKind.values) ...[
            const SizedBox(height: 16),
            _CheckRow(group: group),
          ],
        ],
      ),
    );
  }
}

class _CheckRow extends StatelessWidget {
  const _CheckRow({required this.group});

  final List<WaitingCheck> group;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            group.first.title,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            group.first.rule,
            style: TextStyle(fontSize: 14, height: 1.35, color: palette.sub),
          ),
          for (final check in group)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                check.text,
                key: ValueKey('check-${check.kind}-${check.catId}'),
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: check.ready ? palette.primary : palette.ink,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
