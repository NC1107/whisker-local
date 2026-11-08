import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'robot_parts.dart';
import 'who_was_this_sheet.dart';

/// Opens the place where a visit gets its cat. Screens that show an
/// unlabelled visit call this, so one screen can replace the default sheet.
typedef ChooseVisit = Future<void> Function(
  BuildContext context,
  int visitId,
  int? closestCatId,
);

enum _Filter {
  all('All'),
  cats('Cats'),
  robot('Robot'),
  alerts('Alerts');

  const _Filter(this.label);

  final String label;
}

class _Entry {
  const _Entry({
    required this.at,
    required this.group,
    required this.title,
    required this.detail,
    this.cat,
    this.mark,
    this.markIcon,
    this.warning = false,
    this.chooseVisitId,
    this.closestCatId,
    this.weightLb,
    this.durationS,
  });

  final DateTime at;
  final _Filter group;
  final String title;
  final String detail;
  final Cat? cat;
  final String? mark;
  final String? markIcon;
  final bool warning;
  final int? chooseVisitId;
  final int? closestCatId;
  final double? weightLb;
  final int? durationS;
}

/// Alerts, visits and what the robot did, newest first. The shell opens it
/// from the bell, over the tabs.
class ActivityScreen extends StatefulWidget {
  const ActivityScreen({
    required this.controller,
    this.onChooseVisit,
    this.clock = DateTime.now,
    super.key,
  });

  final AppController controller;
  final ChooseVisit? onChooseVisit;
  final DateTime Function() clock;

  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen> {
  static const _eventLimit = 300;

  _Filter _filter = _Filter.all;
  List<HistoryEvent>? _events;
  Map<int, Visit> _visits = const {};
  String? _error;
  int _loadId = 0;
  String _lastSignature = '';

  @override
  void initState() {
    super.initState();
    _lastSignature = _signature();
    widget.controller.addListener(_onController);
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => widget.controller.markAlertsSeen(),
    );
    _load();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onController);
    super.dispose();
  }

  // The list only changes when something happened, so most polls cost nothing.
  String _signature() {
    final status = widget.controller.status;
    final robot = status?.robot;
    return [
      status?.alerts.map((alert) => alert.key).join(','),
      status?.visitsNeedingCat,
      robot?.cleanCycles,
      robot?.lastVisitAt,
      robot?.drawerLastMoved,
    ].join('|');
  }

  void _onController() {
    final signature = _signature();
    if (signature == _lastSignature) return;
    _lastSignature = signature;
    _load();
  }

  Future<void> _load() async {
    final id = ++_loadId;
    final api = widget.controller.api;
    try {
      final loaded = await Future.wait([
        api.events(limit: _eventLimit),
        api.visits(),
      ]);
      if (!mounted || id != _loadId) return;
      setState(() {
        _events = loaded[0] as List<HistoryEvent>;
        _visits = {
          for (final visit in loaded[1] as List<Visit>) visit.id: visit,
        };
        _error = null;
      });
    } on ApiException catch (error) {
      if (!mounted || id != _loadId) return;
      setState(() => _error = error.message);
    }
  }

  Future<void> _choose(_Entry entry) async {
    final choose = widget.onChooseVisit;
    if (choose != null) {
      await choose(context, entry.chooseVisitId!, entry.closestCatId);
    } else {
      await showWhoWasThis(
        context,
        widget.controller,
        only: UnlabelledVisit(
          id: entry.chooseVisitId!,
          at: entry.at,
          weightLb: entry.weightLb,
          durationS: entry.durationS,
          closestCatId: entry.closestCatId,
        ),
      );
    }
    if (mounted) await _load();
  }

  _Entry? _entryFor(HistoryEvent event, ServerStatus? status) {
    final detail = event.detail;
    switch (event.kind) {
      case 'visit':
        return _visitEntry(event, _visits[event.id], status);
      case 'clean_cycle':
        final drawer = detail['drawer_percent'];
        return _Entry(
          at: event.at,
          group: _Filter.robot,
          title: 'Clean cycle finished',
          detail: drawer == null ? 'Cycle done' : 'Drawer at $drawer%',
          mark: 'C',
        );
      case 'alert':
        final raised = event.raised;
        return _Entry(
          at: event.at,
          group: _Filter.alerts,
          title: event.message ?? 'Alert',
          detail: !raised
              ? 'Cleared'
              : event.urgent
              ? 'Needs attention now'
              : 'Needs attention',
          mark: raised ? '!' : null,
          markIcon: raised ? null : AppIcons.check,
          warning: raised,
        );
      case 'fault':
        return _Entry(
          at: event.at,
          group: _Filter.robot,
          title: 'Robot reported a fault',
          detail: 'Code ${detail['code']}',
          mark: '!',
          warning: true,
        );
      case 'firmware':
        return _Entry(
          at: event.at,
          group: _Filter.robot,
          title: 'Firmware changed',
          detail: 'Version ${detail['version']}',
          mark: 'F',
        );
      case 'drawer_moved':
        return _Entry(
          at: event.at,
          group: _Filter.robot,
          title: 'Waste drawer moved',
          detail: 'Pulled out or pushed in',
          mark: 'D',
        );
      case 'command':
        return _Entry(
          at: event.at,
          group: _Filter.robot,
          title: '${detail['label'] ?? 'Command'} sent',
          detail: 'From this app',
          mark: 'R',
        );
    }
    return null;
  }

  _Entry _visitEntry(HistoryEvent event, Visit? visit, ServerStatus? status) {
    final weight = event.weightLb;
    final length = event.durationS;
    final facts = [
      if (weight != null) weightText(weight),
      if (length != null)
        '${weight == null ? 'For' : 'for'} ${durationText(length)}',
    ].join(' ');
    final detail = facts.isEmpty ? 'No weight recorded' : facts;
    final label = visit?.label ?? VisitLabel.unknown;
    switch (label) {
      case VisitLabel.cat:
        final cat = status?.catById(visit!.catId ?? -1);
        return _Entry(
          at: event.at,
          group: _Filter.cats,
          title: cat == null ? 'A cat visited' : '${cat.name} visited',
          detail: detail,
          cat: cat,
          mark: cat == null ? 'C' : null,
        );
      case VisitLabel.notCat:
        return _Entry(
          at: event.at,
          group: _Filter.cats,
          title: 'Not a cat',
          detail: detail,
          mark: '-',
        );
      case VisitLabel.unknown:
        final canChoose = visit != null;
        final latest = status?.latestVisitNeedingCat;
        return _Entry(
          at: event.at,
          group: _Filter.cats,
          title: 'Cat visit',
          detail: detail,
          mark: '?',
          weightLb: weight,
          durationS: length,
          chooseVisitId: canChoose ? visit.id : null,
          closestCatId: latest != null && latest.id == visit?.id
              ? latest.closestCatId
              : null,
        );
    }
  }

  List<_Entry> _entries(ServerStatus? status) {
    final events = _events ?? const [];
    final entries = <_Entry>[];
    for (final event in events) {
      final entry = _entryFor(event, status);
      if (entry == null) continue;
      if (_filter != _Filter.all && entry.group != _filter) continue;
      entries.add(entry);
    }
    return entries;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(
        children: [
          TopBar.back(
            title: 'Activity',
            onBack: () => Navigator.of(context).pop(),
          ),
          Expanded(
            child: ListenableBuilder(
              listenable: widget.controller,
              builder: (context, _) => _body(context),
            ),
          ),
        ],
      ),
    );
  }

  Widget _body(BuildContext context) {
    final status = widget.controller.status;
    final entries = _entries(status);
    final now = widget.clock();
    final tabs = _FilterTabs(
      value: _filter,
      onChanged: (value) => setState(() => _filter = value),
    );
    final children = <Widget>[tabs];
    if (_events == null && _error == null) {
      children.add(const _Loading());
    } else if (_events == null) {
      children.add(_LoadFailed(message: _error!, onRetry: _load));
    } else if (entries.isEmpty) {
      children.add(_Empty(filter: _filter));
    } else {
      final days = <DateTime, List<_Entry>>{};
      for (final entry in entries) {
        final day = DateTime(entry.at.year, entry.at.month, entry.at.day);
        days.putIfAbsent(day, () => []).add(entry);
      }
      for (final day in days.entries) {
        children.add(_DayHeading(dayHeading(day.key, now)));
        children.add(_DayCard(entries: day.value, onChoose: _choose));
      }
      if (_error != null) children.add(InlineError(_error!));
    }
    return PageBody(gap: 10, onRefresh: _load, children: children);
  }
}

class _FilterTabs extends StatelessWidget {
  const _FilterTabs({required this.value, required this.onChanged});

  final _Filter value;
  final ValueChanged<_Filter> onChanged;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Semantics(
      container: true,
      label: 'Show',
      child: Container(
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: palette.line)),
        ),
        child: Row(
          children: [
            for (final filter in _Filter.values)
              Expanded(
                child: Semantics(
                  button: true,
                  selected: filter == value,
                  child: InkWell(
                    key: ValueKey('filter-${filter.name}'),
                    onTap: () => onChanged(filter),
                    child: Container(
                      height: 48,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        border: Border(
                          bottom: BorderSide(
                            width: 4,
                            color: filter == value
                                ? palette.primary
                                : Colors.transparent,
                          ),
                        ),
                      ),
                      child: Text(
                        filter.label,
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w700,
                          color: filter == value ? palette.ink : palette.sub,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _DayHeading extends StatelessWidget {
  const _DayHeading(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Semantics(
        header: true,
        child: Text(
          label,
          style: TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w700,
            color: context.palette.sub,
          ),
        ),
      ),
    );
  }
}

class _DayCard extends StatelessWidget {
  const _DayCard({required this.entries, required this.onChoose});

  final List<_Entry> entries;
  final ValueChanged<_Entry> onChoose;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return SurfaceCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          for (var i = 0; i < entries.length; i++)
            DecoratedBox(
              decoration: BoxDecoration(
                border: i == entries.length - 1
                    ? null
                    : Border(bottom: BorderSide(color: palette.line)),
              ),
              child: _Row(entry: entries[i], onChoose: onChoose),
            ),
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.entry, required this.onChoose});

  final _Entry entry;
  final ValueChanged<_Entry> onChoose;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final cat = entry.cat;
    final Widget leading;
    if (cat != null) {
      leading = CatAvatar(cat: cat, size: 40);
    } else {
      final background = entry.warning ? palette.warnSoft : palette.soft;
      final ink = entry.warning
          ? palette.warn
          : entry.mark == '?' || entry.mark == '-'
          ? palette.ink
          : palette.primary;
      leading = ExcludeSemantics(
        child: Container(
          width: 40,
          height: 40,
          alignment: Alignment.center,
          decoration: BoxDecoration(color: background, shape: BoxShape.circle),
          child: entry.markIcon != null
              ? AppIcon(entry.markIcon!, size: 20, color: ink)
              : Text(
                  entry.mark ?? '',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: ink,
                  ),
                ),
        ),
      );
    }
    return ConstrainedBox(
      constraints: const BoxConstraints(minHeight: 62),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        child: Row(
          children: [
            leading,
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    entry.title,
                    style: TextStyle(fontSize: 16, color: palette.ink),
                  ),
                  Text(
                    entry.detail,
                    style: TextStyle(fontSize: 14, color: palette.sub),
                  ),
                ],
              ),
            ),
            if (entry.chooseVisitId != null) ...[
              const SizedBox(width: 8),
              Material(
                color: palette.soft,
                borderRadius: BorderRadius.circular(12),
                child: InkWell(
                  key: ValueKey('choose-${entry.chooseVisitId}'),
                  borderRadius: BorderRadius.circular(12),
                  onTap: () => onChoose(entry),
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
                          'Choose',
                          style: TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                            color: palette.primary,
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ],
            const SizedBox(width: 12),
            Text(
              DateFormat.jm().format(entry.at),
              style: TextStyle(fontSize: 14, color: palette.sub),
            ),
          ],
        ),
      ),
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 48),
      child: LoadingSpinner('Loading activity'),
    );
  }
}

class _LoadFailed extends StatelessWidget {
  const _LoadFailed({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Activity did not load',
            style: TextStyle(
              fontSize: 17,
              fontWeight: FontWeight.w700,
              color: context.palette.ink,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            message,
            style: TextStyle(fontSize: 15, color: context.palette.sub),
          ),
          const SizedBox(height: 12),
          RobotButton(label: 'Try again', onPressed: onRetry),
        ],
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.filter});

  final _Filter filter;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final text = switch (filter) {
      _Filter.all => 'Visits, cleaning cycles and alerts show up here.',
      _Filter.cats => 'Cat visits show up here.',
      _Filter.robot => 'Cleaning cycles and robot events show up here.',
      _Filter.alerts => 'No alerts so far.',
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 32),
      child: Column(
        children: [
          Text(
            'Nothing yet',
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            text,
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 15, color: palette.sub),
          ),
        ],
      ),
    );
  }
}
