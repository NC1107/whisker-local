import 'package:flutter/material.dart';

import '../../theme.dart';
import 'layout.dart';

/// A section title followed by a hairline that fills the row. The title is 20
/// px on a phone and 24 px on a wide window, as in the design.
class SectionHeader extends StatelessWidget {
  const SectionHeader(this.title, {this.trailing, super.key});

  final String title;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Row(
      children: [
        Semantics(
          header: true,
          child: Text(
            title,
            style: TextStyle(
              fontSize: isWide(context) ? 24 : 20,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(child: Container(height: 1, color: palette.line)),
        if (trailing != null) ...[const SizedBox(width: 12), trailing!],
      ],
    );
  }
}
