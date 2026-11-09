import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api.dart';
import '../../theme.dart';
import '../common/common.dart';
import 'insight_checks_sheet.dart';

/// The early-warning notes in plain words: what changed and since when. The
/// info button holds the rules, so the card itself stays one line when quiet.
class WorthALookCard extends StatelessWidget {
  const WorthALookCard({
    required this.look,
    required this.error,
    required this.onRetry,
    this.title = 'Worth a look',
    super.key,
  });

  final WorthALook? look;
  final String? error;
  final VoidCallback onRetry;
  final String title;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final data = look;
    return SurfaceCard(
      key: const ValueKey('worth-a-look'),
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Semantics(
                  header: true,
                  child: Text(
                    title,
                    style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w700,
                      color: palette.ink,
                    ),
                  ),
                ),
              ),
              // Shifted so the glyph lines up with the card's text edge while
              // the touch target stays full size.
              Transform.translate(
                offset: const Offset(11, 0),
                child: IconButton(
                  key: const ValueKey('insight-checks'),
                  tooltip: 'How insights work',
                  icon: const Icon(Icons.info_outline, size: 22),
                  color: palette.sub,
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints.tightFor(
                    width: 44,
                    height: 44,
                  ),
                  onPressed: data == null
                      ? null
                      : () => showInsightChecks(context, data.waiting),
                ),
              ),
            ],
          ),
          const SizedBox(height: 2),
          if (data == null && error != null)
            NoticeCard(
              tone: NoticeTone.warning,
              message: "This didn't load: $error",
              actionLabel: 'Try again',
              onTap: onRetry,
            )
          else if (data == null)
            const SizedBox(
              height: 44,
              child: Center(
                child: SizedBox.square(
                  dimension: 24,
                  child: CircularProgressIndicator(strokeWidth: 3),
                ),
              ),
            )
          else
            ..._body(context, data),
        ],
      ),
    );
  }

  List<Widget> _body(BuildContext context, WorthALook data) {
    final palette = context.palette;
    if (data.notes.isEmpty) {
      return [
        Text(
          data.waiting.every((check) => check.ready)
              ? 'No insights'
              : 'No insights yet',
          style: TextStyle(fontSize: 16, height: 1.4, color: palette.sub),
        ),
      ];
    }
    return [
      for (final note in data.notes) ...[
        _NoteTile(note: note),
        const SizedBox(height: 10),
      ],
    ];
  }
}

class _NoteTile extends StatelessWidget {
  const _NoteTile({required this.note});

  final HealthNote note;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final urgent = note.urgent;
    final since = note.since;
    final label = [if (urgent) 'Urgent', note.title, note.body].join('. ');
    return Semantics(
      container: true,
      label: label,
      child: ExcludeSemantics(
        child: Container(
          key: ValueKey('note-${note.key}'),
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
          decoration: BoxDecoration(
            color: urgent ? palette.warnSoft : palette.soft,
            borderRadius: BorderRadius.circular(14),
            border: Border(
              left: BorderSide(
                width: 5,
                color: urgent ? palette.danger : palette.primary,
              ),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  if (urgent)
                    Text(
                      'URGENT',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 0.6,
                        color: palette.danger,
                      ),
                    ),
                  Text(
                    note.title,
                    style: TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                      color: palette.ink,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                note.body,
                style: TextStyle(fontSize: 15, height: 1.4, color: palette.ink),
              ),
              if (since != null) ...[
                const SizedBox(height: 4),
                Text(
                  'Since ${DateFormat.MMMd().format(since)}',
                  style: TextStyle(fontSize: 14, color: palette.sub),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
