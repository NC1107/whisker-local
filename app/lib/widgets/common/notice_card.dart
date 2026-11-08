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
    final style = TextStyle(fontSize: 15, height: 1.3, color: ink);
    final text = lead != null && message.isNotEmpty
        ? Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(lead!, style: style.copyWith(fontWeight: FontWeight.w700)),
              Text(message, style: style),
            ],
          )
        : Text(
            lead ?? message,
            style: lead == null
                ? style
                : style.copyWith(fontWeight: FontWeight.w700),
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
            constraints: const BoxConstraints(minHeight: 56),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              child: Row(
                children: [
                  Expanded(child: text),
                  if (actionLabel != null) ...[
                    const SizedBox(width: 10),
                    ConstrainedBox(
                      constraints: const BoxConstraints(
                        minHeight: 48,
                        minWidth: 48,
                      ),
                      child: Center(
                        widthFactor: 1,
                        child: Text(
                          actionLabel!,
                          style: TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                            color: warning ? palette.warn : palette.primary,
                          ),
                        ),
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
