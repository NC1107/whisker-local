import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/charts/chart_chrome.dart';
import '../widgets/charts/latest_request.dart';
import '../widgets/common/common.dart';
import '../widgets/health/coverage_calendar.dart';

const _weeksPerPage = 13;

/// Which days the robot was listened to, which the scale misbehaved on, and how
/// many visits could not be matched to a cat. The page steps back without limit.
class DataQualityScreen extends StatefulWidget {
  const DataQualityScreen({required this.controller, super.key});

  final AppController controller;

  @override
  State<DataQualityScreen> createState() => _DataQualityScreenState();
}

class _DataQualityScreenState extends State<DataQualityScreen> {
  final _quality = LatestRequest<DataQuality>();
  var _page = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _quality.dispose();
    super.dispose();
  }

  DateTime get _end {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    return today.subtract(Duration(days: _page * _weeksPerPage * 7));
  }

  DateTime get _start {
    final end = _end;
    final monday = end.subtract(Duration(days: end.weekday - 1));
    return monday.subtract(const Duration(days: (_weeksPerPage - 1) * 7));
  }

  Future<void> _load() => _quality.run(
    () => widget.controller.api.dataQuality(start: _start, end: _end),
  );

  void _go(int page) {
    setState(() => _page = page);
    _load();
  }

  Future<void> _mark() async {
    final range = await showDateRangePicker(
      context: context,
      firstDate: DateTime(2020),
      lastDate: DateTime.now(),
      helpText: 'Days to treat as unreliable',
    );
    if (range == null || !mounted) return;
    final note = await showDialog<String>(
      context: context,
      builder: (_) => const _NoteDialog(),
    );
    if (note == null) return;
    await _change(
      () => widget.controller.api.markUnreliable(range.start, range.end, note),
    );
  }

  Future<void> _change(Future<void> Function() action) async {
    try {
      await action();
    } on ApiException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
      return;
    }
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Scaffold(
      backgroundColor: palette.bg,
      body: Column(
        children: [
          TopBar.back(
            title: 'Data quality',
            onBack: () => Navigator.of(context).pop(),
          ),
          Expanded(
            child: ListenableBuilder(
              listenable: _quality,
              builder: (context, _) =>
                  PageBody(onRefresh: _load, children: _children(context)),
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _children(BuildContext context) {
    final palette = context.palette;
    final data = _quality.value;
    final shown = data != null && data.end == _end ? data : null;
    return [
      Text(
        'A day with no data from the robot is not a day with no visits, and '
        'nothing here counts it as one. Days that are partial, hatched or '
        'marked unreliable are left out of averages and checks.',
        style: TextStyle(fontSize: 15, height: 1.4, color: palette.sub),
      ),
      SurfaceCard(
        padding: const EdgeInsets.fromLTRB(18, 12, 18, 18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            RangeNavigator(
              label: shown == null
                  ? ''
                  : '${DateFormat.MMMd().format(shown.start)} to '
                        '${DateFormat.yMMMd().format(shown.end)}',
              onEarlier:
                  shown != null &&
                      shown.earliest != null &&
                      shown.earliest!.isBefore(shown.start)
                  ? () => _go(_page + 1)
                  : null,
              onLater: _page > 0 && !_quality.loading
                  ? () => _go(_page - 1)
                  : null,
            ),
            const SizedBox(height: 8),
            if (shown != null)
              CoverageCalendar(quality: shown)
            else if (_quality.error != null)
              NoticeCard(
                tone: NoticeTone.warning,
                message: "This didn't load: ${_quality.error}",
                actionLabel: 'Try again',
                onTap: _load,
              )
            else
              const SizedBox(
                height: 120,
                child: Center(child: CircularProgressIndicator()),
              ),
          ],
        ),
      ),
      if (shown != null) _Matching(quality: shown),
      if (shown != null) _Marked(quality: shown, onRemove: _remove),
      FilledButton(
        key: const ValueKey('mark-unreliable'),
        onPressed: _mark,
        child: const Text('Mark days as unreliable'),
      ),
    ];
  }

  Future<void> _remove(UnreliableRange range) =>
      _change(() => widget.controller.api.unmarkUnreliable(range.id));
}

class _Matching extends StatelessWidget {
  const _Matching({required this.quality});

  final DataQuality quality;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final share = quality.unnamedShare;
    return SurfaceCard(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Matching visits to cats',
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            key: const ValueKey('unnamed-share'),
            share == null
                ? 'No visits in these weeks.'
                : '${quality.unnamed} of ${quality.visits} visits '
                      '(${(share * 100).round()}%) could not be matched to a '
                      'cat with confidence. Per-cat numbers leave them out, so '
                      'they are lower bounds, not exact counts.',
            style: TextStyle(fontSize: 15, height: 1.4, color: palette.sub),
          ),
        ],
      ),
    );
  }
}

class _Marked extends StatelessWidget {
  const _Marked({required this.quality, required this.onRemove});

  final DataQuality quality;
  final Future<void> Function(UnreliableRange range) onRemove;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final fmt = DateFormat.MMMd();
    final ranges = [
      for (final r in quality.ranges)
        if (!r.end.isBefore(quality.start) && !r.start.isAfter(quality.end)) r,
    ];
    final gaps = [
      for (final gap in quality.gaps)
        if (gap.kind != 'unreliable')
          '${fmt.format(gap.start)}${gap.start == gap.end ? '' : ' to ${fmt.format(gap.end)}'}: '
              '${gap.kind == 'no_data' ? 'no data from the robot, not counted as zero visits' : 'the scale gave unusual readings, so weight and visit figures are less reliable'}.',
    ];
    return SurfaceCard(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'Left out of the numbers',
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 8),
          if (gaps.isEmpty && ranges.isEmpty)
            Text(
              'Nothing in these weeks.',
              style: TextStyle(fontSize: 15, color: palette.sub),
            ),
          for (final text in gaps)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                text,
                style: TextStyle(fontSize: 15, height: 1.4, color: palette.ink),
              ),
            ),
          for (final range in ranges)
            Row(
              children: [
                Expanded(
                  child: Text(
                    '${fmt.format(range.start)}'
                    '${range.start == range.end ? '' : ' to ${fmt.format(range.end)}'}'
                    ': marked unreliable${range.note.isEmpty ? '' : ', "${range.note}"'}.',
                    style: TextStyle(
                      fontSize: 15,
                      height: 1.4,
                      color: palette.ink,
                    ),
                  ),
                ),
                TextButton(
                  key: ValueKey('unmark-${range.id}'),
                  onPressed: () => onRemove(range),
                  child: const Text('Remove'),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _NoteDialog extends StatefulWidget {
  const _NoteDialog();

  @override
  State<_NoteDialog> createState() => _NoteDialogState();
}

class _NoteDialogState extends State<_NoteDialog> {
  final _text = TextEditingController();

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('What happened?'),
    content: TextField(
      controller: _text,
      autofocus: true,
      maxLength: 120,
      decoration: const InputDecoration(hintText: 'For example, moved the box'),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () => Navigator.of(context).pop(
          _text.text.trim().isEmpty ? 'Marked unreliable' : _text.text.trim(),
        ),
        child: const Text('Mark days'),
      ),
    ],
  );
}
