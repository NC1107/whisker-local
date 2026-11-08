import 'package:flutter/material.dart';

import '../../api.dart';
import '../../theme.dart';

/// Week, Month and Year as three equal tabs with a 4 px underline on the chosen one.
class PeriodTabs extends StatelessWidget {
  const PeriodTabs({
    required this.value,
    required this.onChanged,
    this.semanticLabel = 'Period',
    super.key,
  });

  final InsightPeriod value;
  final ValueChanged<InsightPeriod> onChanged;
  final String semanticLabel;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      label: semanticLabel,
      child: Row(
        children: [
          for (final period in InsightPeriod.values)
            Expanded(
              child: Semantics(
                button: true,
                selected: period == value,
                child: InkWell(
                  key: ValueKey('period-${period.code}'),
                  onTap: () => onChanged(period),
                  child: Container(
                    height: 48,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      border: Border(
                        bottom: BorderSide(
                          width: 4,
                          color: period == value
                              ? palette.primary
                              : Colors.transparent,
                        ),
                      ),
                    ),
                    child: Text(
                      period.label,
                      style: TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w700,
                        color: period == value ? palette.ink : palette.sub,
                      ),
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
