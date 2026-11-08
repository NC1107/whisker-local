import 'package:flutter/material.dart';

/// At this width and above the bottom bar becomes a rail on the left.
const wideBreakpoint = 800.0;

/// The page content never gets wider than this, however wide the window is.
const contentMaxWidth = 1060.0;

bool isWide(BuildContext context) =>
    MediaQuery.sizeOf(context).width >= wideBreakpoint;

/// The scrolling body of a screen: 16 px padding on a phone, 24 px on a wide
/// window, centered and capped at [contentMaxWidth], children spaced by [gap].
class PageBody extends StatelessWidget {
  const PageBody({
    required this.children,
    this.gap = 14,
    this.onRefresh,
    this.controller,
    super.key,
  });

  final List<Widget> children;
  final double gap;
  final Future<void> Function()? onRefresh;
  final ScrollController? controller;

  @override
  Widget build(BuildContext context) {
    final padding = isWide(context) ? 24.0 : 16.0;
    final list = ListView.separated(
      controller: controller,
      physics: const AlwaysScrollableScrollPhysics(),
      padding: EdgeInsets.fromLTRB(padding, padding, padding, padding + 8),
      itemCount: children.length,
      separatorBuilder: (_, _) => SizedBox(height: gap),
      itemBuilder: (_, index) => children[index],
    );
    final centered = Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: contentMaxWidth),
        child: onRefresh == null
            ? list
            : RefreshIndicator(onRefresh: onRefresh!, child: list),
      ),
    );
    return centered;
  }
}
