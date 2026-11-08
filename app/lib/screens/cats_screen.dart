import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../shell.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'add_cat_screen.dart';
import 'cat_profile_screen.dart';
import 'who_was_this_sheet.dart';

/// What to show a person for a failed request: the server's own message when
/// it has one that reads well, and a plain sentence otherwise.
String errorMessage(Object error) =>
    error is ApiException ? error.message : 'Something went wrong. Try again.';

String plural(int count, String one, String many) => count == 1 ? one : many;

/// A time of day such as "3:42 PM", with a plain space before the AM or PM.
String clockText(DateTime at) =>
    DateFormat.jm().format(at).replaceAll(' ', ' ');

/// How long ago [birthday] was, such as "4 yrs 7 mos". Null for a date that
/// has not happened yet.
String? ageText(DateTime birthday, DateTime now) {
  var months = (now.year - birthday.year) * 12 + now.month - birthday.month;
  if (now.day < birthday.day) months -= 1;
  if (months < 0) return null;
  if (months == 0) return 'Under 1 mo';
  final years = months ~/ 12;
  final rest = months % 12;
  final yearPart = years == 0 ? null : '$years ${plural(years, 'yr', 'yrs')}';
  final monthPart = rest == 0 ? null : '$rest ${plural(rest, 'mo', 'mos')}';
  return [yearPart, monthPart].nonNulls.join(' ');
}

// The line under the name says what a person would, and leaves the spayed
// or neutered detail to the profile.
String? _sexWord(CatSex sex) => switch (sex) {
  CatSex.female || CatSex.spayedFemale => 'female',
  CatSex.male || CatSex.neuteredMale => 'male',
  CatSex.unknown => null,
};

/// The line under a cat's name: breed, sex and age, leaving out what is not
/// known. Empty when nothing is.
String catAbout(Cat cat, DateTime now) {
  final birthday = cat.birthday;
  final age = birthday == null ? null : ageText(birthday, now);
  final breed = cat.breed?.trim();
  return [
    if (breed != null && breed.isNotEmpty) breed,
    ?_sexWord(cat.sex),
    ?age,
  ].join(', ');
}

String weightOrNone(double? pounds, String none) =>
    pounds == null ? none : '${pounds.toStringAsFixed(1)} lb';

/// Where the person sees the cats: one card each, the visits waiting for a
/// cat, and the way to add another.
class CatsScreen extends StatelessWidget {
  const CatsScreen({required this.controller, super.key});

  final AppController controller;

  void _openProfile(BuildContext context, Cat cat) {
    final shell = context.getInheritedWidgetOfExactType<ShellScope>();
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => CatProfileScreen(
          controller: controller,
          catId: cat.id,
          onViewInsights: shell == null
              ? null
              : (_) {
                  Navigator.of(
                    context,
                    rootNavigator: true,
                  ).popUntil((route) => route.isFirst);
                  shell.select(AppTab.insights);
                },
        ),
      ),
    );
  }

  void _openAdd(BuildContext context) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AddCatScreen(controller: controller),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final status = controller.status;
        final wide = isWide(context);
        if (status == null) {
          return PageBody(
            onRefresh: controller.poll,
            children: [
              if (wide) const SectionHeader('Cats'),
              if (controller.unreachable)
                const NoticeCard(
                  tone: NoticeTone.warning,
                  message: "Can't reach the server. Trying again.",
                )
              else
                Semantics(
                  label: 'Loading cats',
                  child: const Padding(
                    padding: EdgeInsets.symmetric(vertical: 48),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                ),
            ],
          );
        }
        final cats = controller.cats;
        return PageBody(
          onRefresh: controller.poll,
          children: [
            if (wide) const SectionHeader('Cats'),
            if (controller.unreachable)
              const NoticeCard(
                tone: NoticeTone.warning,
                message:
                    "Can't reach the server. These cats may be out of date.",
              ),
            if (cats.isNotEmpty && status.visitsNeedingCat > 0)
              _NeedsCatNotice(
                status: status,
                onTap: () => showWhoWasThis(context, controller),
              ),
            if (cats.isEmpty)
              const _NoCats()
            else
              _CatGrid(cats: cats, onOpen: (cat) => _openProfile(context, cat)),
            _AddCatCard(onTap: () => _openAdd(context)),
            ArchivedCats(
              controller: controller,
              activeIds: [for (final cat in cats) cat.id],
            ),
          ],
        );
      },
    );
  }
}

class _NeedsCatNotice extends StatelessWidget {
  const _NeedsCatNotice({required this.status, required this.onTap});

  final ServerStatus status;
  final VoidCallback onTap;

  String _detail(UnlabelledVisit visit) {
    final weight = visit.weightLb;
    final clock = clockText(visit.at);
    final now = DateTime.now();
    final sameDay =
        visit.at.year == now.year &&
        visit.at.month == now.month &&
        visit.at.day == now.day;
    final when = sameDay
        ? 'at $clock'
        : 'on ${DateFormat.MMMd().format(visit.at)} at $clock';
    if (weight == null) return when[0].toUpperCase() + when.substring(1);
    return '${weightOrNone(weight, '')} $when';
  }

  @override
  Widget build(BuildContext context) {
    final count = status.visitsNeedingCat;
    final latest = status.latestVisitNeedingCat;
    return NoticeCard(
      lead: count == 1 ? '1 visit needs a cat.' : '$count visits need a cat.',
      message: latest == null ? '' : _detail(latest),
      actionLabel: 'Choose',
      onTap: onTap,
    );
  }
}

class _NoCats extends StatelessWidget {
  const _NoCats();

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return SurfaceCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'No cats yet',
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 6),
          Text(
            'Add each cat so the robot can tell them apart by weight.',
            style: TextStyle(fontSize: 16, height: 1.35, color: p.sub),
          ),
        ],
      ),
    );
  }
}

class _CatGrid extends StatelessWidget {
  const _CatGrid({required this.cats, required this.onOpen});

  final List<Cat> cats;
  final ValueChanged<Cat> onOpen;

  static const _minCardWidth = 300.0;

  @override
  Widget build(BuildContext context) {
    final gap = isWide(context) ? 16.0 : 14.0;
    return LayoutBuilder(
      builder: (context, box) {
        final columns = ((box.maxWidth + gap) / (_minCardWidth + gap))
            .floor()
            .clamp(1, 3);
        final rows = <Widget>[];
        for (var start = 0; start < cats.length; start += columns) {
          final row = cats.skip(start).take(columns).toList();
          rows.add(
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (var i = 0; i < columns; i++) ...[
                    if (i > 0) SizedBox(width: gap),
                    Expanded(
                      child: i < row.length
                          ? _CatCard(cat: row[i], onTap: () => onOpen(row[i]))
                          : const SizedBox.shrink(),
                    ),
                  ],
                ],
              ),
            ),
          );
        }
        return Column(
          children: [
            for (var i = 0; i < rows.length; i++) ...[
              if (i > 0) SizedBox(height: gap),
              rows[i],
            ],
          ],
        );
      },
    );
  }
}

class _CatCard extends StatelessWidget {
  const _CatCard({required this.cat, required this.onTap});

  final Cat cat;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final about = catAbout(cat, DateTime.now());
    final weight = weightOrNone(cat.summary.latestWeightLb, 'No visits yet');
    final lastWeight = cat.summary.latestWeightLb == null
        ? weight
        : 'Last weight: $weight';
    return SurfaceCard(
      padding: EdgeInsets.zero,
      onTap: onTap,
      semanticLabel: [
        cat.name,
        if (about.isNotEmpty) about,
        lastWeight,
      ].join(', '),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CatBanner(
            cat: cat,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  cat.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 26,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                if (about.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(about, style: TextStyle(fontSize: 16, color: p.sub)),
                ],
                const SizedBox(height: 10),
                Row(
                  children: [
                    Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: p.fill,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        lastWeight,
                        style: TextStyle(fontSize: 15, color: p.sub),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _AddCatCard extends StatelessWidget {
  const _AddCatCard({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return SurfaceCard(
      padding: EdgeInsets.zero,
      onTap: onTap,
      semanticLabel: 'Add a cat',
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 64),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Container(
              width: 32,
              height: 32,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: p.primary,
              ),
              child: Center(
                child: AppIcon(AppIcons.plus, size: 20, color: p.onPrimary),
              ),
            ),
            const SizedBox(width: 10),
            const Text('Add a cat', style: TextStyle(fontSize: 17)),
          ],
        ),
      ),
    );
  }
}

/// Cats that were archived, each with a way to bring it back. Shows nothing
/// while there are none, or while they can't be loaded.
class ArchivedCats extends StatefulWidget {
  const ArchivedCats({
    required this.controller,
    required this.activeIds,
    super.key,
  });

  final AppController controller;

  /// The ids of the cats that are not archived. When these change, the
  /// archived ones are loaded again.
  final List<int> activeIds;

  @override
  State<ArchivedCats> createState() => _ArchivedCatsState();
}

class _ArchivedCatsState extends State<ArchivedCats> {
  List<Cat> _archived = const [];
  int _request = 0;
  final Set<int> _restoring = {};
  final Map<int, String> _errors = {};

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void didUpdateWidget(ArchivedCats oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.activeIds.join(',') != widget.activeIds.join(',')) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final request = ++_request;
    try {
      final all = await widget.controller.api.cats(includeArchived: true);
      if (!mounted || request != _request) return;
      setState(() => _archived = all.where((cat) => cat.archived).toList());
    } on ApiException {
      if (!mounted || request != _request) return;
      setState(() => _archived = const []);
    }
  }

  Future<void> _restore(Cat cat) async {
    if (_restoring.contains(cat.id)) return;
    setState(() {
      _restoring.add(cat.id);
      _errors.remove(cat.id);
    });
    try {
      await widget.controller.updateCat(cat.id, {'archived': false});
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _restoring.remove(cat.id);
        _errors[cat.id] = errorMessage(error);
      });
      return;
    }
    if (!mounted) return;
    setState(() => _restoring.remove(cat.id));
  }

  @override
  Widget build(BuildContext context) {
    if (_archived.isEmpty) return const SizedBox.shrink();
    final p = context.palette;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionHeader('Archived'),
        const SizedBox(height: 8),
        for (final cat in _archived)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    CatAvatar(cat: cat, size: 44),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        cat.name,
                        style: const TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    TextButton(
                      onPressed: _restoring.contains(cat.id)
                          ? null
                          : () => _restore(cat),
                      child: Text('Restore ${cat.name}'),
                    ),
                  ],
                ),
                if (_errors[cat.id] case final message?)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      message,
                      style: TextStyle(fontSize: 15, color: p.danger),
                    ),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}
