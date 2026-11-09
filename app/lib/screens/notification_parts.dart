import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

/// `HH:MM` from the server as the phone's own time format.
String clockLabel(BuildContext context, String hhmm) {
  final parts = hhmm.split(':');
  final time = TimeOfDay(
    hour: int.tryParse(parts.first) ?? 0,
    minute: int.tryParse(parts.last) ?? 0,
  );
  return time.format(context);
}

String hhmm(TimeOfDay time) =>
    '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';

/// A line under a switch: a title with its own control on the right or below.
class SubRow extends StatelessWidget {
  const SubRow({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
      child: child,
    );
  }
}

/// A slider for the number behind a kind, saved when the finger lifts.
class ThresholdRow extends StatefulWidget {
  const ThresholdRow({
    required this.kind,
    required this.threshold,
    required this.value,
    required this.onSaved,
    super.key,
  });

  final String kind;
  final PushThreshold threshold;
  final int value;
  final ValueChanged<int> onSaved;

  @override
  State<ThresholdRow> createState() => _ThresholdRowState();
}

class _ThresholdRowState extends State<ThresholdRow> {
  int? _draft;

  String _text(int value) {
    final unit = widget.threshold.unit;
    return unit == '%' ? '$value%' : '$value $unit';
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final threshold = widget.threshold;
    final shown = (_draft ?? widget.value).clamp(threshold.min, threshold.max);
    final span = threshold.max - threshold.min;
    return SubRow(
      child: Row(
        children: [
          SizedBox(
            width: 76,
            child: Text(
              _text(shown),
              style: TextStyle(fontSize: 15, color: palette.sub),
            ),
          ),
          Expanded(
            child: SliderTheme(
              data: SliderTheme.of(context).copyWith(
                activeTrackColor: palette.primary,
                inactiveTrackColor: palette.track,
                thumbColor: palette.primary,
                overlayColor: palette.primary.withValues(alpha: 0.12),
                activeTickMarkColor: Colors.transparent,
                inactiveTickMarkColor: Colors.transparent,
                thumbShape: const RoundSliderThumbShape(
                  elevation: 0,
                  pressedElevation: 0,
                ),
              ),
              child: Slider(
                key: ValueKey('push-threshold-${widget.kind}'),
                value: shown.toDouble(),
                min: threshold.min.toDouble(),
                max: threshold.max.toDouble(),
                divisions: span > 0 && span <= 100 ? span : null,
                semanticFormatterCallback: (value) =>
                    '${widget.kind.replaceAll('_', ' ')} at ${_text(value.round())}',
                onChanged: (value) => setState(() => _draft = value.round()),
                onChangeEnd: (value) {
                  setState(() => _draft = null);
                  if (value.round() != widget.value) {
                    widget.onSaved(value.round());
                  }
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Which cats a visit notice is for: everyone, or the chosen ones.
class CatChoiceRow extends StatelessWidget {
  const CatChoiceRow({
    required this.kind,
    required this.cats,
    required this.choice,
    required this.onChanged,
    super.key,
  });

  final String kind;
  final List<PushCat> cats;
  final CatChoice choice;
  final ValueChanged<Map<String, Object>> onChanged;

  void _toggle(int id) {
    final next = {...choice.catIds};
    if (!next.remove(id)) next.add(id);
    onChanged(
      next.isEmpty
          ? {'mode': 'any'}
          : {'mode': 'selected', 'cat_ids': (next.toList()..sort())},
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return SubRow(
      child: Wrap(
        spacing: 8,
        runSpacing: 4,
        children: [
          _Chip(
            key: ValueKey('push-cats-$kind-everyone'),
            label: 'Everyone',
            selected: choice.everyone,
            onTap: () => onChanged({'mode': 'any'}),
          ),
          for (final cat in cats)
            _Chip(
              key: ValueKey('push-cats-$kind-${cat.id}'),
              label: cat.name,
              selected: !choice.everyone && choice.catIds.contains(cat.id),
              onTap: () => _toggle(cat.id),
            ),
          if (!choice.everyone)
            _Chip(
              key: ValueKey('push-cats-$kind-unknown'),
              label: 'Unknown cat',
              selected: choice.includeUnknown,
              onTap: () =>
                  onChanged({'include_unknown': !choice.includeUnknown}),
              color: palette.sub,
            ),
        ],
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({
    required this.label,
    required this.selected,
    required this.onTap,
    this.color,
    super.key,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      button: true,
      selected: selected,
      label: label,
      excludeSemantics: true,
      child: Material(
        color: selected ? palette.primary : palette.soft,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: onTap,
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 44),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              child: Center(
                widthFactor: 1,
                child: Text(
                  label,
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                    color: selected ? palette.onPrimary : color ?? palette.ink,
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// A label with the current time on the right, which opens the time picker.
class TimeRow extends StatelessWidget {
  const TimeRow({
    required this.rowKey,
    required this.label,
    required this.value,
    required this.onPicked,
    super.key,
  });

  final Key rowKey;
  final String label;
  final String value;
  final ValueChanged<TimeOfDay> onPicked;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final text = clockLabel(context, value);
    return SubRow(
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              style: TextStyle(fontSize: 16, color: palette.sub),
            ),
          ),
          Semantics(
            button: true,
            label: '$label time, $text. Change',
            excludeSemantics: true,
            child: Material(
              color: palette.soft,
              borderRadius: BorderRadius.circular(12),
              child: InkWell(
                key: rowKey,
                borderRadius: BorderRadius.circular(12),
                onTap: () async {
                  final parts = value.split(':');
                  final picked = await showTimePicker(
                    context: context,
                    initialTime: TimeOfDay(
                      hour: int.parse(parts.first),
                      minute: int.parse(parts.last),
                    ),
                    helpText: label,
                  );
                  if (picked != null) onPicked(picked);
                },
                child: ConstrainedBox(
                  constraints: const BoxConstraints(
                    minHeight: 44,
                    minWidth: 44,
                  ),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Center(
                      widthFactor: 1,
                      child: Text(
                        text,
                        style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w600,
                          color: palette.ink,
                        ),
                      ),
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
