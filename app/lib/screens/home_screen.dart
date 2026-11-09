import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../shell.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'activity_screen.dart';
import 'robot_parts.dart';
import 'who_was_this_sheet.dart';

/// Who is in the box and how the robot is doing, with the two things that most
/// often need a person: a full drawer and a visit nobody has named.
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    required this.controller,
    this.onOpenCat,
    this.onChooseVisit,
    this.clock = DateTime.now,
    super.key,
  });

  final AppController controller;

  /// Opens one cat. Without it a tap on a cat opens the Cats tab.
  final void Function(BuildContext context, Cat cat)? onOpenCat;

  /// Opens the place where a visit gets its cat. Without it a sheet opens.
  final ChooseVisit? onChooseVisit;
  final DateTime Function() clock;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _lowLitterAt = 30;
  int _loadId = 0;

  @override
  void initState() {
    super.initState();
    _loadLowLitter();
  }

  Future<void> _loadLowLitter() async {
    final id = ++_loadId;
    final lowAt = await fetchLitterLowAt(widget.controller);
    if (!mounted || id != _loadId || lowAt == null) return;
    setState(() => _lowLitterAt = lowAt);
  }

  Future<void> _refresh() async {
    await Future.wait([widget.controller.poll(), _loadLowLitter()]);
  }

  void _openCat(Cat cat) {
    final open = widget.onOpenCat;
    if (open != null) {
      open(context, cat);
    } else {
      ShellScope.of(context).select(AppTab.cats);
    }
  }

  Future<void> _choose(UnlabelledVisit visit) async {
    final choose = widget.onChooseVisit;
    if (choose != null) {
      await choose(context, visit.id, visit.closestCatId);
    } else {
      await showWhoWasThis(context, widget.controller);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final status = widget.controller.status;
        final now = widget.clock();
        final notice = connectionNotice(
          status,
          unreachable: widget.controller.unreachable,
          now: now,
        );
        return Column(
          children: [
            if (notice != null) ConnectionStrip(notice),
            Expanded(
              child: status == null
                  ? _NoStatus(
                      unreachable: widget.controller.unreachable,
                      onRetry: widget.controller.poll,
                    )
                  : _content(context, status, now),
            ),
          ],
        );
      },
    );
  }

  Widget _content(BuildContext context, ServerStatus status, DateTime now) {
    final wide = isWide(context);
    final robot = status.robot;
    final shell = ShellScope.of(context);
    final cats = status.cats;
    final children = <Widget>[
      if (!wide) _Greeting(cats: cats, now: now),
      const SectionHeader('Robot'),
      RobotSummaryCard(
        status: status,
        now: now,
        lowLitterAt: _lowLitterAt,
        actions: wide ? RobotMoveButtons(controller: widget.controller) : null,
        onTap: wide ? null : () => shell.select(AppTab.robot),
      ),
      if (robot != null && status.online && robot.drawerFullLockout)
        NoticeCard(
          key: const ValueKey('drawer-full'),
          message: 'Empty the drawer, then press Reset',
          actionLabel: 'Reset',
          tone: NoticeTone.warning,
          onTap: () => shell.select(AppTab.robot),
        ),
      const SectionHeader('Cats'),
      if (cats.isEmpty)
        _NoCats(onAdd: () => shell.select(AppTab.cats))
      else if (wide)
        GridRows(
          children: [
            for (final cat in cats)
              _WideCatCard(cat: cat, onTap: () => _openCat(cat)),
          ],
        )
      else
        for (final cat in cats) _CatCard(cat: cat, onTap: () => _openCat(cat)),
      if (status.visitsNeedingCat > 0) _visitsNotice(status, shell),
    ];
    return PageBody(
      gap: wide ? 18 : 14,
      onRefresh: _refresh,
      children: children,
    );
  }

  Widget _visitsNotice(ServerStatus status, ShellScope shell) {
    final count = status.visitsNeedingCat;
    final latest = status.latestVisitNeedingCat;
    final facts = [
      if (latest?.weightLb != null) weightText(latest!.weightLb!),
      if (latest != null) 'at ${DateFormat.jm().format(latest.at)}',
    ].join(' ');
    final noCats = status.cats.isEmpty;
    return NoticeCard(
      key: const ValueKey('visits-need-cat'),
      lead: count == 1 ? '1 visit needs a cat' : '$count visits need a cat',
      message: facts,
      actionLabel: noCats ? 'Add a cat' : 'Choose',
      onTap: noCats || latest == null
          ? () => shell.select(AppTab.cats)
          : () => _choose(latest),
    );
  }
}

class _NoStatus extends StatelessWidget {
  const _NoStatus({required this.unreachable, required this.onRetry});

  final bool unreachable;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    if (!unreachable) {
      return const LoadingSpinner('Loading');
    }
    return PageBody(
      children: [
        SurfaceCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                "Can't reach the server",
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                  color: palette.ink,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                'Check the server and your network',
                style: TextStyle(fontSize: 15, color: palette.sub),
              ),
              const SizedBox(height: 14),
              RobotButton(label: 'Try again', onPressed: onRetry, filled: true),
            ],
          ),
        ),
      ],
    );
  }
}

class _Greeting extends StatelessWidget {
  const _Greeting({required this.cats, required this.now});

  final List<Cat> cats;
  final DateTime now;

  String get _hello => now.hour < 12
      ? 'Good morning'
      : now.hour < 18
      ? 'Good afternoon'
      : 'Good evening';

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final cat = cats.isEmpty
        ? null
        : cats[now.difference(DateTime(now.year)).inDays % cats.length];
    return SurfaceCard(
      radius: 18,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Row(
        children: [
          if (cat != null) ...[
            CatAvatar(cat: cat, size: 46),
            const SizedBox(width: 12),
          ],
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _hello,
                  style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                    color: palette.ink,
                  ),
                ),
                Text(
                  cat == null
                      ? 'Add a cat to see numbers'
                      : 'Tell ${cat.name} we said hi',
                  style: TextStyle(fontSize: 15, color: palette.sub),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _NoCats extends StatelessWidget {
  const _NoCats({required this.onAdd});

  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return SurfaceCard(
      key: const ValueKey('no-cats'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'No cats yet',
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Visits get a name and weight',
            style: TextStyle(fontSize: 15, color: palette.sub),
          ),
          const SizedBox(height: 14),
          RobotButton(label: 'Add a cat', onPressed: onAdd, filled: true),
        ],
      ),
    );
  }
}

String _lastVisitText(Cat cat) {
  final at = cat.summary.lastVisitAt;
  if (at == null) return 'No visits yet';
  return 'Last visit ${DateFormat('M/d').format(at)} at ${DateFormat.jm().format(at)}';
}

String _label(Cat cat) {
  final weight = cat.summary.latestWeightLb;
  return [
    cat.name,
    if (weight != null) weightText(weight),
    _lastVisitText(cat),
  ].join(', ');
}

class _CatCard extends StatelessWidget {
  const _CatCard({required this.cat, required this.onTap});

  final Cat cat;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final weight = cat.summary.latestWeightLb;
    return SurfaceCard(
      key: ValueKey('cat-${cat.id}'),
      onTap: onTap,
      semanticLabel: _label(cat),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CatAvatar(cat: cat),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  cat.name,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: palette.sub,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            weight == null ? 'No weight yet' : weightText(weight),
            style: TextStyle(
              fontSize: weight == null ? 20 : 32,
              height: weight == null ? 1.4 : 1.1,
              fontWeight: FontWeight.w700,
              color: palette.ink,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            _lastVisitText(cat),
            style: TextStyle(fontSize: 15, color: palette.sub),
          ),
        ],
      ),
    );
  }
}

String _number(double? value) {
  if (value == null) return '-';
  return value == value.roundToDouble()
      ? value.round().toString()
      : value.toStringAsFixed(1);
}

class _WideCatCard extends StatelessWidget {
  const _WideCatCard({required this.cat, required this.onTap});

  final Cat cat;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final summary = cat.summary;
    final weight = summary.latestWeightLb;
    final hour = summary.commonVisitHour;
    final stats = [
      (
        summary.yesterdayWeightLb == null
            ? '-'
            : summary.yesterdayWeightLb!.toStringAsFixed(1),
        'Yesterday lb',
      ),
      ('${summary.yesterdayVisits}', 'Visits'),
      (_number(summary.yesterdayMinutes), 'Minutes'),
      (
        hour == null ? '-' : DateFormat.j().format(DateTime(2000, 1, 1, hour)),
        'Peak time',
      ),
    ];
    return SurfaceCard(
      key: ValueKey('cat-${cat.id}'),
      padding: const EdgeInsets.all(18),
      onTap: onTap,
      semanticLabel: _label(cat),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CatAvatar(cat: cat, size: 60),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      cat.name,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w700,
                        color: palette.ink,
                      ),
                    ),
                    Text(
                      _lastVisitText(cat),
                      style: TextStyle(fontSize: 15, color: palette.sub),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Text(
                weight == null ? 'No weight' : weightText(weight),
                style: TextStyle(
                  fontSize: weight == null ? 18 : 30,
                  fontWeight: FontWeight.w700,
                  color: palette.ink,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              for (var i = 0; i < stats.length; i++) ...[
                if (i > 0) const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        stats[i].$1,
                        style: TextStyle(
                          fontSize: 20,
                          height: 1.15,
                          fontWeight: FontWeight.w700,
                          color: palette.ink,
                        ),
                      ),
                      Text(
                        stats[i].$2,
                        style: TextStyle(fontSize: 14, color: palette.sub),
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }
}
