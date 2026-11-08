/// Round tick values for a value axis, with the labels to print for them.
class ChartAxis {
  const ChartAxis._(this.min, this.max, this.step);

  /// The axis for values between [low] and [high]. Bar charts pass
  /// [fromZero] so their bars start at the baseline.
  factory ChartAxis.fit(
    double low,
    double high, {
    bool fromZero = false,
    int maxIntervals = 5,
  }) {
    var lo = fromZero ? 0.0 : low;
    var hi = high;
    if (hi - lo < 1e-9) {
      if (fromZero) {
        hi = lo + 1;
      } else {
        lo -= 0.5;
        hi += 0.5;
      }
    } else if (!fromZero) {
      final pad = (hi - lo) * 0.1;
      lo -= pad;
      hi += pad;
    }
    for (final step in _steps) {
      final first = (lo / step).floor();
      final last = (hi / step).ceil();
      if (last - first <= maxIntervals) {
        return ChartAxis._(_clean(first * step), _clean(last * step), step);
      }
    }
    return ChartAxis._(lo, hi, hi - lo);
  }

  final double min;
  final double max;
  final double step;

  /// Ticks from the lowest to the highest.
  List<double> get ticks {
    final count = ((max - min) / step).round();
    return [for (var i = 0; i <= count; i++) _clean(min + i * step)];
  }

  String label(double value) => value.toStringAsFixed(step >= 1 ? 0 : 1);

  /// 0 at [min] and 1 at [max].
  double fraction(double value) => (value - min) / (max - min);

  static final _steps = [
    for (final scale in [0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0])
      for (final unit in [1.0, 2.0, 5.0]) unit * scale,
  ];

  static double _clean(double value) => (value * 1e6).roundToDouble() / 1e6;
}
