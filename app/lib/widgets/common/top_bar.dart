import 'package:flutter/material.dart';

import '../../theme.dart';
import 'app_icon.dart';

/// The 60 px bar at the top of a phone screen: a button on each side and a
/// centered title. Leave a side empty and it keeps its 44 px so the title
/// stays in the middle.
class TopBar extends StatelessWidget {
  const TopBar({
    required this.title,
    this.leading,
    this.trailing,
    this.subtleTitle = false,
    super.key,
  });

  /// A bar for a screen pushed over the tabs, with a back arrow on the left.
  factory TopBar.back({
    required String title,
    required VoidCallback onBack,
    String backLabel = 'Back',
    Key? key,
  }) => TopBar(
    key: key,
    title: title,
    subtleTitle: true,
    leading: TopBarButton(
      icon: AppIcons.chevronLeft,
      label: backLabel,
      onPressed: onBack,
      iconSize: 26,
      ink: true,
    ),
  );

  final String title;
  final Widget? leading;
  final Widget? trailing;

  /// The grey title used on pushed screens.
  final bool subtleTitle;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Container(
      padding: EdgeInsets.fromLTRB(8, MediaQuery.paddingOf(context).top, 8, 0),
      decoration: BoxDecoration(color: palette.card, boxShadow: palette.shadow),
      child: SizedBox(
        height: 60,
        child: Row(
          children: [
            SizedBox(width: 44, child: leading),
            Expanded(
              child: Semantics(
                header: true,
                child: Text(
                  title,
                  textAlign: TextAlign.center,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: subtleTitle ? 20 : 18,
                    fontWeight: FontWeight.w700,
                    color: subtleTitle ? palette.sub : palette.ink,
                  ),
                ),
              ),
            ),
            SizedBox(width: 44, child: trailing),
          ],
        ),
      ),
    );
  }
}

/// A 44 px icon button for a [TopBar], with an optional dot for something new.
class TopBarButton extends StatelessWidget {
  const TopBarButton({
    required this.icon,
    required this.label,
    required this.onPressed,
    this.showDot = false,
    this.iconSize = 24,
    this.ink = false,
    super.key,
  });

  final String icon;

  /// What a screen reader says, such as "Alerts, 1 new".
  final String label;
  final VoidCallback onPressed;
  final bool showDot;
  final double iconSize;

  /// Draw in the text color instead of the secondary one.
  final bool ink;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      button: true,
      label: label,
      child: ExcludeSemantics(
        child: InkResponse(
          onTap: onPressed,
          radius: 24,
          child: SizedBox.square(
            dimension: 44,
            child: Stack(
              alignment: Alignment.center,
              children: [
                AppIcon(
                  icon,
                  size: iconSize,
                  color: ink ? palette.ink : palette.sub,
                ),
                if (showDot)
                  Positioned(
                    top: 9,
                    right: 9,
                    child: Container(
                      key: const ValueKey('unread-dot'),
                      width: 9,
                      height: 9,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: palette.danger,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
