import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api.dart';
import '../../theme.dart';
import '../common/common.dart';

/// The early-warning notes in plain words: what changed and since when. It
/// also says which checks are still waiting for more history, so an empty list
/// is never mistaken for an all clear.
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
          Semantics(
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
          const SizedBox(height: 10),
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
    final waiting = data.waiting.where((item) => !item.ready).toList();
    return [
      if (data.notes.isEmpty)
        Text(
          'Nothing has stood out. That is not a clean bill of health: these '
          'are early-warning checks on box data, not a diagnosis.',
          style: TextStyle(fontSize: 16, height: 1.4, color: palette.sub),
        ),
      for (final note in data.notes) ...[
        _NoteTile(note: note),
        const SizedBox(height: 10),
      ],
      if (waiting.isNotEmpty) ...[
        const SizedBox(height: 6),
        Text(
          'Waiting for more history',
          style: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w700,
            color: palette.ink,
          ),
        ),
        const SizedBox(height: 4),
        for (final item in waiting)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              item.text,
              key: ValueKey('waiting-${item.kind}-${item.catId}'),
              style: TextStyle(fontSize: 15, height: 1.35, color: palette.sub),
            ),
          ),
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
