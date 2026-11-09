import 'package:flutter/material.dart';

import '../../theme.dart';

Future<T?> showAppSheet<T>(BuildContext context, WidgetBuilder builder) {
  final palette = context.palette;
  return showModalBottomSheet<T>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: false,
    constraints: const BoxConstraints(maxWidth: 560),
    barrierColor: palette.isDark
        ? const Color.fromRGBO(0, 0, 0, 0.6)
        : const Color.fromRGBO(23, 50, 44, 0.5),
    builder: builder,
  );
}

class SheetHandle extends StatelessWidget {
  const SheetHandle({super.key});

  @override
  Widget build(BuildContext context) => Center(
    child: Container(
      width: 40,
      height: 5,
      decoration: BoxDecoration(
        color: context.palette.line,
        borderRadius: BorderRadius.circular(3),
      ),
    ),
  );
}
