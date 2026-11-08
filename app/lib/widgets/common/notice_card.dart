import 'package:flutter/material.dart';

import '../../theme.dart';

enum NoticeTone {
  /// Something to do, in the soft green.
  info,

  /// Something is wrong, in the soft amber.
  warning,
}

/// A rounded strip with a short message and an optional action word on the
/// right, such as "1 visit needs a cat." with "Choose".
class NoticeCard extends StatelessWidget {
  const NoticeCard({
    required this.message,
    this.lead,
    this.actionLabel,
    this.onTap,
    this.tone = NoticeTone.info,
    super.key,
  });

  /// Bold words at the start.
  final String? lead;
  final String message;
  final String? actionLabel;
  final VoidCallback? onTap;
  final NoticeTone tone;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final warning = tone == NoticeTone.warning;
    final ink = warning ? palette.warn : palette.ink;
    final text = Text.rich(
      TextSpan(
        children: [
          if (lead != null)
            TextSpan(
              text: lead,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          if (lead != null && message.isNotEmpty) const TextSpan(text: ' '),
          TextSpan(text: message),
        ],
      ),
      style: TextStyle(fontSize: 15, height: 1.3, color: ink),
    );
    return Semantics(
      container: true,
      button: onTap != null,
      child: Material(
        color: warning ? palette.warnSoft : palette.soft,
        borderRadius: BorderRadius.circular(16),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 52),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              child: Row(
                children: [
                  Expanded(child: text),
                  if (actionLabel != null) ...[
                    const SizedBox(width: 10),
                    Text(
                      actionLabel!,
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                        color: warning ? palette.warn : palette.primary,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
