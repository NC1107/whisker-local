import 'package:flutter/foundation.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

/// Gives a custom painted chart its size, its hover and tap handling, and
/// forgets the selection when the [data] it refers to changes.
class ChartFrame extends StatefulWidget {
  const ChartFrame({
    required this.height,
    required this.data,
    required this.semanticLabel,
    required this.pick,
    required this.paint,
    super.key,
  });

  final double height;

  /// Everything the selection depends on; compared item by item.
  final List<Object?> data;
  final String semanticLabel;

  /// The index under a pointer, or null when nothing is selectable there.
  final int? Function(Offset position, Size size) pick;
  final CustomPainter Function(int? selected) paint;

  @override
  State<ChartFrame> createState() => _ChartFrameState();
}

class _ChartFrameState extends State<ChartFrame> {
  int? _hovered;
  int? _pinned;

  @override
  void didUpdateWidget(ChartFrame oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!listEquals(oldWidget.data, widget.data)) {
      _hovered = null;
      _pinned = null;
    }
  }

  void _setPinned(Offset position, Size size, {bool toggle = false}) {
    final index = widget.pick(position, size);
    setState(() => _pinned = toggle && index == _pinned ? null : index);
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      image: true,
      label: widget.semanticLabel,
      child: ExcludeSemantics(
        child: LayoutBuilder(
          builder: (context, constraints) {
            final size = Size(constraints.maxWidth, widget.height);
            return MouseRegion(
              onHover: (event) {
                final index = widget.pick(event.localPosition, size);
                if (index != _hovered) setState(() => _hovered = index);
              },
              onExit: (_) {
                if (_hovered != null) setState(() => _hovered = null);
              },
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTapDown: (details) =>
                    _setPinned(details.localPosition, size, toggle: true),
                onHorizontalDragUpdate: (details) =>
                    _setPinned(details.localPosition, size),
                supportedDevices: const {
                  PointerDeviceKind.touch,
                  PointerDeviceKind.mouse,
                  PointerDeviceKind.stylus,
                },
                child: CustomPaint(
                  size: size,
                  painter: widget.paint(_hovered ?? _pinned),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}
