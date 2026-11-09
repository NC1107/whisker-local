import 'package:flutter/material.dart';

import '../api.dart';
import '../controller.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'cats_screen.dart';

/// Opens the sheet that asks which cat the latest unlabelled visit was. It
/// stays open and moves on to the next visit until none is left. Does
/// nothing while no visit needs a cat. With [only] it asks about that one
/// visit and closes once it has a cat. With [current], that visit already has
/// a cat or Not a cat, and the sheet marks it and can clear it.
Future<void> showWhoWasThis(
  BuildContext context,
  AppController controller, {
  UnlabelledVisit? only,
  Visit? current,
}) {
  if (only == null && controller.status?.latestVisitNeedingCat == null) {
    return Future.value();
  }
  final palette = context.palette;
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: false,
    constraints: const BoxConstraints(maxWidth: 560),
    barrierColor: palette.isDark
        ? const Color.fromRGBO(0, 0, 0, 0.6)
        : const Color.fromRGBO(23, 50, 44, 0.5),
    builder: (_) =>
        WhoWasThisSheet(controller: controller, only: only, current: current),
  );
}

/// "9.6 lb for 35 s, today at 3:42 AM".
String visitSummary(UnlabelledVisit visit, DateTime now) {
  final weight = visit.weightLb;
  final duration = visit.durationS;
  final what = switch ((weight, duration)) {
    (final double w, final int d) => '${weightText(w)} for ${durationText(d)}',
    (final double w, null) => weightText(w),
    (null, final int d) => 'A visit of ${durationText(d)}',
    _ => 'A visit',
  };
  return '$what, ${whenText(visit.at, now)}';
}

class WhoWasThisSheet extends StatefulWidget {
  const WhoWasThisSheet({
    required this.controller,
    this.only,
    this.current,
    super.key,
  });

  final AppController controller;
  final UnlabelledVisit? only;
  final Visit? current;

  @override
  State<WhoWasThisSheet> createState() => _WhoWasThisSheetState();
}

class _WhoWasThisSheetState extends State<WhoWasThisSheet> {
  bool _busy = false;
  bool _closing = false;
  String? _error;

  void _close() {
    if (_closing) return;
    _closing = true;
    Navigator.of(context).pop();
  }

  Future<void> _label(
    UnlabelledVisit visit,
    VisitLabel label, {
    int? catId,
  }) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.controller.labelVisit(visit.id, label, catId: catId);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = errorMessage(error);
      });
      return;
    }
    if (!mounted) return;
    if (widget.only != null) {
      _close();
      return;
    }
    setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final status = widget.controller.status;
        final visit = widget.only ?? status?.latestVisitNeedingCat;
        if (status == null || visit == null) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) _close();
          });
          return const SizedBox(height: 1);
        }
        final waiting = widget.only == null ? status.visitsNeedingCat : 1;
        return SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
          child: Semantics(
            container: true,
            label: 'Who was this',
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Center(
                  child: Container(
                    width: 40,
                    height: 5,
                    decoration: BoxDecoration(
                      color: p.line,
                      borderRadius: BorderRadius.circular(3),
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Semantics(
                  header: true,
                  child: const Text(
                    'Who was this?',
                    style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700),
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  visitSummary(visit, DateTime.now()),
                  style: TextStyle(fontSize: 16, color: p.sub),
                ),
                if (waiting > 1)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      '${waiting - 1} more after this one',
                      style: TextStyle(fontSize: 15, color: p.sub),
                    ),
                  ),
                const SizedBox(height: 12),
                for (final cat in widget.controller.cats)
                  _ChoiceRow(
                    leading: CatAvatar(cat: cat),
                    title: cat.name,
                    subtitle: _usually(cat),
                    badge: _isCurrent(VisitLabel.cat, cat.id)
                        ? 'Current'
                        : visit.closestCatId == cat.id
                        ? 'Closest match'
                        : null,
                    onTap: _busy
                        ? null
                        : () => _label(visit, VisitLabel.cat, catId: cat.id),
                  ),
                _ChoiceRow(
                  leading: Container(
                    width: 52,
                    height: 52,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: p.soft,
                    ),
                    child: Center(
                      child: AppIcon(AppIcons.close, size: 24, color: p.sub),
                    ),
                  ),
                  title: 'Not a cat',
                  subtitle: 'Leaves it out of the charts',
                  badge: _isCurrent(VisitLabel.notCat, null) ? 'Current' : null,
                  onTap: _busy ? null : () => _label(visit, VisitLabel.notCat),
                ),
                if (widget.current != null)
                  _ChoiceRow(
                    leading: Container(
                      width: 52,
                      height: 52,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: p.soft,
                      ),
                      child: Center(
                        child: AppIcon(AppIcons.reset, size: 24, color: p.sub),
                      ),
                    ),
                    title: 'Not sure',
                    subtitle: 'Clears the label',
                    onTap: _busy
                        ? null
                        : () => _label(visit, VisitLabel.unknown),
                  ),
                if (_error case final message?)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: NoticeCard(
                      tone: NoticeTone.warning,
                      message: message,
                    ),
                  ),
                const SizedBox(height: 6),
                Material(
                  color: p.soft,
                  borderRadius: BorderRadius.circular(14),
                  clipBehavior: Clip.antiAlias,
                  child: InkWell(
                    onTap: _close,
                    child: const SizedBox(
                      height: 52,
                      child: Center(
                        child: Text(
                          'Cancel',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
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
      },
    );
  }

  bool _isCurrent(VisitLabel label, int? catId) {
    final current = widget.current;
    return current != null && current.label == label && current.catId == catId;
  }

  String _usually(Cat cat) {
    final weight = cat.summary.usualWeightLb ?? cat.referenceWeightLb;
    return weight == null
        ? 'No usual weight yet'
        : 'Usually ${weightText(weight)}';
  }
}

class _ChoiceRow extends StatelessWidget {
  const _ChoiceRow({
    required this.leading,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.badge,
  });

  final Widget leading;
  final String title;
  final String subtitle;
  final String? badge;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      button: true,
      enabled: onTap != null,
      label: [title, subtitle, ?badge].join(', '),
      child: ExcludeSemantics(
        child: InkWell(
          onTap: onTap,
          child: Container(
            constraints: const BoxConstraints(minHeight: 72),
            padding: const EdgeInsets.symmetric(vertical: 8),
            decoration: BoxDecoration(
              border: Border(top: BorderSide(color: p.line)),
            ),
            child: Row(
              children: [
                leading,
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      Text(
                        subtitle,
                        style: TextStyle(fontSize: 15, color: p.sub),
                      ),
                    ],
                  ),
                ),
                if (badge != null)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: p.soft,
                      borderRadius: BorderRadius.circular(999),
                    ),
                    child: Text(
                      badge!,
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w700,
                        color: p.primary,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
