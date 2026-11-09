import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'add_cat_screen.dart';
import 'cat_insights_screen.dart';
import 'cats_screen.dart';

const _profileMaxWidth = 720.0;

/// "11/5/2026 7:02 PM".
String lastVisitText(DateTime at) =>
    '${DateFormat.yMd().format(at)} ${clockText(at)}';

String visitsText(int count) =>
    count == 0 ? 'No visits' : '$count ${plural(count, 'visit', 'visits')}';

/// One cat: photo, details, yesterday at a glance, and a way to switch to
/// another cat or edit this one.
class CatProfileScreen extends StatefulWidget {
  const CatProfileScreen({
    required this.controller,
    required this.catId,
    super.key,
  });

  final AppController controller;
  final int catId;

  @override
  State<CatProfileScreen> createState() => _CatProfileScreenState();
}

class _CatProfileScreenState extends State<CatProfileScreen> {
  late int _catId = widget.catId;
  bool _leaving = false;

  void _leave() {
    if (_leaving) return;
    _leaving = true;
    Navigator.of(context).pop();
  }

  Future<void> _edit(Cat cat) async {
    final result = await Navigator.of(context).push<CatSaveResult>(
      MaterialPageRoute(
        builder: (_) => AddCatScreen(controller: widget.controller, cat: cat),
      ),
    );
    if (!mounted) return;
    if (result?.archived ?? false) _leave();
  }

  void _viewInsights(Cat cat) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) =>
            CatInsightsScreen(controller: widget.controller, catId: cat.id),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final controller = widget.controller;
        final status = controller.status;
        final cat = status?.catById(_catId);
        if (status != null && cat == null) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted && (ModalRoute.of(context)?.isCurrent ?? false)) {
              _leave();
            }
          });
        }
        return Scaffold(
          body: cat == null
              ? _Unavailable(
                  unreachable: controller.unreachable,
                  onBack: _leave,
                )
              : _Profile(
                  cat: cat,
                  cats: controller.cats,
                  onBack: _leave,
                  onPick: (other) => setState(() => _catId = other.id),
                  onEdit: () => _edit(cat),
                  onViewInsights: _viewInsights,
                ),
        );
      },
    );
  }
}

class _Unavailable extends StatelessWidget {
  const _Unavailable({required this.unreachable, required this.onBack});

  final bool unreachable;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        TopBar.back(title: 'Cat', onBack: onBack, backLabel: 'Back to cats'),
        Expanded(
          child: PageBody(
            children: [
              if (unreachable)
                const NoticeCard(
                  tone: NoticeTone.warning,
                  message: "Can't reach the server",
                )
              else
                Semantics(
                  label: 'Loading',
                  child: const Padding(
                    padding: EdgeInsets.symmetric(vertical: 48),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Profile extends StatelessWidget {
  const _Profile({
    required this.cat,
    required this.cats,
    required this.onBack,
    required this.onPick,
    required this.onEdit,
    required this.onViewInsights,
  });

  final Cat cat;
  final List<Cat> cats;
  final VoidCallback onBack;
  final ValueChanged<Cat> onPick;
  final VoidCallback onEdit;
  final ValueChanged<Cat> onViewInsights;

  @override
  Widget build(BuildContext context) {
    final wide = isWide(context);
    final summary = cat.summary;
    void viewMore() => onViewInsights(cat);
    final cards = <Widget>[
      _GlanceCard(
        title: 'Weight',
        caption: "Yesterday's weight",
        value: weightOrNone(summary.yesterdayWeightLb, 'No visits'),
        onTap: viewMore,
      ),
      _GlanceCard(
        title: 'Visits',
        caption: "Yesterday's visits",
        value: visitsText(summary.yesterdayVisits),
        onTap: viewMore,
      ),
      _FactCard(label: 'Sex', value: cat.sex.label),
      _FactCard(
        label: 'Reference weight',
        value: weightOrNone(cat.referenceWeightLb, 'Not set'),
      ),
    ];
    final list = ListView(
      padding: EdgeInsets.zero,
      children: [
        CatBanner(cat: cat, height: 240),
        _Identity(cat: cat, cats: cats, onPick: onPick, onEdit: onEdit),
        Padding(
          padding: const EdgeInsets.all(16),
          child: wide
              ? Column(
                  children: [
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(child: cards[0]),
                        const SizedBox(width: 12),
                        Expanded(child: cards[1]),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(child: cards[2]),
                        const SizedBox(width: 12),
                        Expanded(child: cards[3]),
                      ],
                    ),
                  ],
                )
              : Column(
                  children: [
                    for (var i = 0; i < cards.length; i++) ...[
                      if (i > 0) const SizedBox(height: 12),
                      cards[i],
                    ],
                  ],
                ),
        ),
      ],
    );
    final top = MediaQuery.paddingOf(context).top;
    return ColoredBox(
      color: context.palette.bg,
      child: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: _profileMaxWidth),
          child: Stack(
            children: [
              Positioned.fill(child: list),
              Positioned(
                top: 16 + top,
                left: 12,
                child: Semantics(
                  button: true,
                  label: 'Back to cats',
                  child: ExcludeSemantics(
                    child: InkResponse(
                      onTap: onBack,
                      radius: 26,
                      child: Container(
                        width: 44,
                        height: 44,
                        decoration: const BoxDecoration(
                          shape: BoxShape.circle,
                          color: Color.fromRGBO(20, 32, 28, 0.72),
                        ),
                        child: const Center(
                          child: AppIcon(
                            AppIcons.chevronLeft,
                            color: Colors.white,
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Identity extends StatelessWidget {
  const _Identity({
    required this.cat,
    required this.cats,
    required this.onPick,
    required this.onEdit,
  });

  final Cat cat;
  final List<Cat> cats;
  final ValueChanged<Cat> onPick;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final about = catAbout(cat, DateTime.now());
    final lastVisit = cat.summary.lastVisitAt;
    return Container(
      color: p.card,
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 20),
      child: Column(
        children: [
          Semantics(
            container: true,
            header: true,
            child: Text(
              cat.name,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700),
            ),
          ),
          if (about.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              about,
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 16, color: p.sub),
            ),
          ],
          const SizedBox(height: 4),
          Text(
            lastVisit == null
                ? 'No visits yet'
                : 'Last visit: ${lastVisitText(lastVisit)}',
            style: TextStyle(fontSize: 15, color: p.sub),
          ),
          if (cats.length > 1) ...[
            const SizedBox(height: 10),
            Semantics(
              container: true,
              label: 'Choose a cat',
              child: Wrap(
                alignment: WrapAlignment.center,
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final other in cats)
                    _Pill(
                      label: other.name,
                      selected: other.id == cat.id,
                      onTap: () => onPick(other),
                    ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 8),
          Semantics(
            container: true,
            button: true,
            label: 'Edit profile',
            onTap: onEdit,
            excludeSemantics: true,
            child: Material(
              color: p.soft,
              borderRadius: BorderRadius.circular(999),
              clipBehavior: Clip.antiAlias,
              child: InkWell(
                onTap: onEdit,
                child: Container(
                  constraints: const BoxConstraints(minHeight: 46),
                  padding: const EdgeInsets.symmetric(horizontal: 44),
                  child: Center(
                    widthFactor: 1,
                    child: Text(
                      'Edit profile',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                        color: p.primary,
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

class _Pill extends StatelessWidget {
  const _Pill({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      button: true,
      selected: selected,
      label: label,
      onTap: onTap,
      excludeSemantics: true,
      child: ExcludeSemantics(
        child: Material(
          color: selected ? p.primary : Colors.transparent,
          shape: StadiumBorder(
            side: BorderSide(color: selected ? p.primary : p.line, width: 2),
          ),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: onTap,
            child: Container(
              constraints: const BoxConstraints(minHeight: 44, minWidth: 44),
              padding: const EdgeInsets.symmetric(horizontal: 22),
              child: Center(
                widthFactor: 1,
                child: Text(
                  label,
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: selected ? p.onPrimary : p.ink,
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

class _GlanceCard extends StatelessWidget {
  const _GlanceCard({
    required this.title,
    required this.caption,
    required this.value,
    required this.onTap,
  });

  final String title;
  final String caption;
  final String value;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final none = value == 'No visits';
    return SurfaceCard(
      onTap: onTap,
      semanticLabel: '$title. $caption: $value',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              if (onTap != null)
                Text('View more', style: TextStyle(fontSize: 15, color: p.sub)),
            ],
          ),
          const SizedBox(height: 12),
          Text(caption, style: TextStyle(fontSize: 15, color: p.sub)),
          Text(
            value,
            style: TextStyle(
              fontSize: 28,
              height: 1.2,
              fontWeight: FontWeight.w700,
              color: none ? p.sub : p.primary,
            ),
          ),
        ],
      ),
    );
  }
}

class _FactCard extends StatelessWidget {
  const _FactCard({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return SizedBox(
      width: double.infinity,
      child: SurfaceCard(
        semanticLabel: '$label: $value',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: TextStyle(fontSize: 16, color: p.sub)),
            const SizedBox(height: 4),
            Text(
              value,
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
            ),
          ],
        ),
      ),
    );
  }
}
