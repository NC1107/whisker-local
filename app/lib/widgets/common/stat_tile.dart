import 'package:flutter/material.dart';

import '../../theme.dart';

/// A number with an optional unit beside it and a label below, such as
/// "10.2 lb" over "Yesterday's weight".
class StatTile extends StatelessWidget {
  const StatTile({
    required this.value,
    required this.label,
    this.unit,
    this.valueSize = 24,
    this.labelSize = 15,
    super.key,
  });

  final String value;
  final String? unit;
  final String label;
  final double valueSize;
  final double labelSize;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      label: '$label: $value${unit == null || unit!.isEmpty ? '' : ' $unit'}',
      child: ExcludeSemantics(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text.rich(
              TextSpan(
                text: value,
                children: [
                  if (unit != null && unit!.isNotEmpty)
                    TextSpan(
                      text: ' $unit',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w400,
                        color: palette.sub,
                      ),
                    ),
                ],
              ),
              style: TextStyle(
                fontSize: valueSize,
                height: 1.15,
                fontWeight: FontWeight.w700,
                color: palette.ink,
              ),
            ),
            Text(
              label,
              style: TextStyle(fontSize: labelSize, color: palette.sub),
            ),
          ],
        ),
      ),
    );
  }
}
