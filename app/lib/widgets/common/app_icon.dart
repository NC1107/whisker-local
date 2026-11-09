import 'package:flutter/widgets.dart';
import 'package:flutter_svg/flutter_svg.dart';

/// Stroke icons from the design, as the inside of a 24 by 24 SVG.
abstract final class AppIcons {
  static const home =
      '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/>'
      '<path d="M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>';
  static const insights =
      '<path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>';
  static const robot =
      '<circle cx="12" cy="9" r="7"/><path d="M7 21h10"/>'
      '<path d="M8 15.5V21"/><path d="M16 15.5V21"/>';
  static const cats =
      '<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/>'
      '<circle cx="20" cy="16" r="2"/>'
      '<path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"/>';
  static const bell =
      '<path d="M10.268 21a2 2 0 0 0 3.464 0"/>'
      '<path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>';
  static const settings =
      '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
      '<circle cx="12" cy="12" r="3"/>';
  static const chevronLeft = '<path d="m15 18-6-6 6-6"/>';
  static const chevronRight = '<path d="m9 18 6-6-6-6"/>';
  static const clean =
      '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
      '<path d="M21 3v5h-5"/>'
      '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
      '<path d="M8 16H3v5"/>';
  static const reset =
      '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>'
      '<path d="M3 3v5h5"/>';
  static const emptyLitter =
      '<path d="M12 17V3"/><path d="m6 11 6 6 6-6"/><path d="M19 21H5"/>';
  static const lock =
      '<path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
      '<path d="M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z"/>';
  static const nightLight =
      '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/>'
      '<path d="M9 18h6"/><path d="M10 22h4"/>';
  static const sleep = '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>';
  static const filter =
      '<path d="M12.8 19.6A2 2 0 1 0 14 16H2"/>'
      '<path d="M17.5 8a2.5 2.5 0 1 1 2 4H2"/>'
      '<path d="M9.8 4.4A2 2 0 1 1 11 8H2"/>';
  static const plus = '<path d="M5 12h14"/><path d="M12 5v14"/>';
  static const close = '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>';
  static const check = '<path d="M20 6 9 17l-5-5"/>';
}

/// One of [AppIcons], drawn in [color] (the surrounding text color by default).
class AppIcon extends StatelessWidget {
  const AppIcon(this.markup, {this.size = 24, this.color, super.key});

  final String markup;
  final double size;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final tint = color ?? DefaultTextStyle.of(context).style.color;
    return ExcludeSemantics(
      child: SvgPicture.string(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">$markup</svg>',
        width: size,
        height: size,
        theme: SvgTheme(currentColor: tint ?? const Color(0xFF000000)),
      ),
    );
  }
}
