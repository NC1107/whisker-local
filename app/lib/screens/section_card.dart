import 'package:flutter/material.dart';

import '../theme.dart';
import '../widgets/common/common.dart';

class SectionCard extends StatelessWidget {
  const SectionCard({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return SurfaceCard(padding: EdgeInsets.zero, child: child);
  }
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.title, {super.key});

  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
      child: Semantics(
        header: true,
        child: Text(
          title,
          style: TextStyle(
            fontSize: 17,
            fontWeight: FontWeight.w700,
            color: context.palette.ink,
          ),
        ),
      ),
    );
  }
}

/// A row inside a section card with the hairline on top.
class SectionRow extends StatelessWidget {
  const SectionRow({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(minHeight: 60),
      padding: const EdgeInsets.fromLTRB(16, 6, 12, 6),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: context.palette.line)),
      ),
      alignment: Alignment.centerLeft,
      child: child,
    );
  }
}
