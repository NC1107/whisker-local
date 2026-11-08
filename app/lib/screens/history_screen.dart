import 'package:flutter/material.dart';
import 'package:intl/intl.dart' show DateFormat;

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/charts.dart';

const _pageSize = 100;
const _headingStyle = TextStyle(fontSize: 18, fontWeight: FontWeight.w700);

class HistoryScreen extends StatefulWidget {
  const HistoryScreen({required this.controller, required this.active, super.key});

  final AppController controller;
  final bool active;

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  int _days = 30;
  List<HistoryEvent>? _visits;
  List<HistoryEvent> _events = const [];
  bool _hasMore = false;
  bool _loadingMore = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (widget.active) _load();
  }

  @override
  void didUpdateWidget(HistoryScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.active && !oldWidget.active) _load();
  }

  Future<void> _load() async {
    try {
      final api = widget.controller.api;
      final results = await Future.wait([
        api.visits(days: _days),
        api.events(limit: _pageSize),
      ]);
      if (!mounted) return;
      setState(() {
        _visits = results[0];
        _events = results[1];
        _hasMore = results[1].length == _pageSize;
        _error = null;
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _loadMore() async {
    setState(() => _loadingMore = true);
    try {
      final older =
          await widget.controller.api.events(limit: _pageSize, before: _events.last.cursor);
      if (!mounted) return;
      setState(() {
        _events = [..._events, ...older];
        _hasMore = older.length == _pageSize;
      });
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  void _setRange(int days) {
    setState(() => _days = days);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final visits = _visits;
    if (visits == null) {
      if (_error != null) return _LoadFailed(message: _error!, onRetry: _load);
      // Tabs stay mounted in an IndexedStack, and an offstage spinner would tick forever.
      return widget.active
          ? const Center(child: CircularProgressIndicator())
          : const SizedBox.shrink();
    }
    final now = DateTime.now();
    final hasWeights = visits.any((visit) => visit.weightLb != null);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
        children: [
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 720),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'History',
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 16),
                  SegmentedButton<int>(
                    showSelectedIcon: false,
                    segments: const [
                      ButtonSegment(value: 7, label: Text('7 days')),
                      ButtonSegment(value: 30, label: Text('30 days')),
                      ButtonSegment(value: 90, label: Text('90 days')),
                    ],
                    selected: {_days},
                    onSelectionChanged: (selection) => _setRange(selection.first),
                  ),
                  if (visits.isEmpty && _events.isEmpty)
                    const _NothingYet()
                  else ...[
                    const SizedBox(height: 24),
                    const Text('Weight at each visit', style: _headingStyle),
                    const SizedBox(height: 4),
                    Text(
                      'Each dot is one visit, in pounds. The robot does not say which cat it was.',
                      style: TextStyle(fontSize: 14, height: 1.3, color: context.palette.muted),
                    ),
                    const SizedBox(height: 14),
                    if (hasWeights)
                      WeightChart(visits: visits, days: _days, now: now)
                    else
                      const _ChartEmpty('No weights in this period.'),
                    const SizedBox(height: 28),
                    const Text('Visits per day', style: _headingStyle),
                    const SizedBox(height: 14),
                    VisitsChart(counts: visitsPerDay(visits, now, _days), now: now),
                    const SizedBox(height: 32),
                    const Text('Activity', style: _headingStyle),
                    const SizedBox(height: 4),
                    ..._rows(now),
                    if (_hasMore) ...[
                      const SizedBox(height: 12),
                      OutlinedButton(
                        onPressed: _loadingMore ? null : _loadMore,
                        child: Text(_loadingMore ? 'Loading' : 'Show older activity'),
                      ),
                    ],
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _rows(DateTime now) {
    final rows = <Widget>[];
    String? heading;
    for (final event in _events) {
      final day = dayHeading(event.at, now);
      if (day != heading) {
        heading = day;
        rows.add(_DayHeading(day));
      }
      rows.add(_EventRow(event: event));
    }
    return rows;
  }
}

class _DayHeading extends StatelessWidget {
  const _DayHeading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 16, bottom: 4),
      child: Semantics(
        header: true,
        child: Text(
          text,
          style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: context.palette.muted),
        ),
      ),
    );
  }
}

class _EventRow extends StatelessWidget {
  const _EventRow({required this.event});

  final HistoryEvent event;

  (IconData, String, String?) _describe() {
    final detail = event.detail;
    switch (event.kind) {
      case 'visit':
        final parts = [
          if (event.weightLb != null) weightText(event.weightLb!),
          if (event.durationS != null) durationText(event.durationS!),
        ];
        return (Icons.pets, 'Cat visit', parts.isEmpty ? null : parts.join(', '));
      case 'clean_cycle':
        final drawer = detail['drawer_percent'];
        return (Icons.autorenew, 'Clean cycle finished', drawer == null ? null : 'Drawer $drawer%');
      case 'drawer_moved':
        return (Icons.inbox_outlined, 'Waste drawer moved', null);
      case 'fault':
        return (Icons.error_outline, 'Globe motor fault', null);
      case 'firmware':
        return (Icons.system_update_alt, 'Firmware changed', '${detail['version']}');
      case 'command':
        return switch (detail['label']) {
          'cleanCycle' => (Icons.touch_app_outlined, 'You started a clean cycle', null),
          'panelReset' => (Icons.touch_app_outlined, 'You pressed Reset', null),
          final label => (Icons.touch_app_outlined, '$label', null),
        };
      default:
        return (Icons.circle_outlined, event.kind, null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final (icon, title, detail) = _describe();
    return MergeSemantics(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 9),
        child: Row(
          children: [
            Icon(icon, size: 20, color: palette.muted),
            const SizedBox(width: 12),
            Expanded(child: Text(title, style: const TextStyle(fontSize: 16))),
            if (detail != null) ...[
              Text(detail, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
              const SizedBox(width: 12),
            ],
            SizedBox(
              width: 68,
              child: Text(
                DateFormat.jm().format(event.at),
                textAlign: TextAlign.right,
                style: TextStyle(fontSize: 14, color: palette.muted),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ChartEmpty extends StatelessWidget {
  const _ChartEmpty(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Text(text, style: TextStyle(fontSize: 16, color: context.palette.muted)),
    );
  }
}

class _NothingYet extends StatelessWidget {
  const _NothingYet();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 48),
      child: Text(
        'Nothing recorded yet. Visits and clean cycles show up here as they happen.',
        textAlign: TextAlign.center,
        style: TextStyle(fontSize: 16, height: 1.4, color: context.palette.muted),
      ),
    );
  }
}

class _LoadFailed extends StatelessWidget {
  const _LoadFailed({required this.message, required this.onRetry});

  final String message;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(message, style: const TextStyle(fontSize: 16)),
          const SizedBox(height: 16),
          FilledButton(onPressed: onRetry, child: const Text('Try again')),
        ],
      ),
    );
  }
}
