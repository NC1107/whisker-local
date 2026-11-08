import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import 'cats_screen.dart';

const _formMaxWidth = 600.0;
const _photoSize = 120.0;
const _minWeightLb = 2.0;
const _maxWeightLb = 40.0;
final _earliestBirthday = DateTime(1995);

/// What the form hands back when it closes after saving.
class CatSaveResult {
  const CatSaveResult({required this.catId, this.archived = false});

  final int catId;
  final bool archived;
}

typedef PhotoChooser = Future<PickedPhoto?> Function();

/// The form for a new cat, or, with [cat], for changing one.
class AddCatScreen extends StatefulWidget {
  const AddCatScreen({
    required this.controller,
    this.cat,
    this.choosePhoto = pickCatPhoto,
    super.key,
  });

  final AppController controller;
  final Cat? cat;

  /// How a photo is chosen. Tests replace it, since the real chooser opens the
  /// system's gallery.
  final PhotoChooser choosePhoto;

  @override
  State<AddCatScreen> createState() => _AddCatScreenState();
}

class _AddCatScreenState extends State<AddCatScreen> {
  late final _name = TextEditingController(text: widget.cat?.name);
  late final _breed = TextEditingController(text: widget.cat?.breed);
  late final _weight = TextEditingController(
    text: widget.cat?.referenceWeightLb?.toStringAsFixed(1),
  );
  late CatSex _sex = widget.cat?.sex ?? CatSex.unknown;
  late DateTime? _birthday = widget.cat?.birthday;

  // Set once the cat exists on the server, so a second save changes it
  // instead of making another.
  late Cat? _saved = widget.cat;
  PickedPhoto? _newPhoto;
  bool _removePhoto = false;

  CatSuggestions? _suggestions;
  String? _suggestionsError;
  int _suggestionsRequest = 0;

  bool _busy = false;
  String? _nameError;
  String? _weightError;
  String? _formError;

  bool get _editing => widget.cat != null;

  @override
  void initState() {
    super.initState();
    unawaited(_loadSuggestions());
  }

  @override
  void dispose() {
    _name.dispose();
    _breed.dispose();
    _weight.dispose();
    super.dispose();
  }

  Future<void> _loadSuggestions() async {
    final request = ++_suggestionsRequest;
    _suggestionsError = null;
    try {
      final result = await widget.controller.api.catSuggestions();
      if (!mounted || request != _suggestionsRequest) return;
      setState(() => _suggestions = result);
    } on Object catch (error) {
      if (!mounted || request != _suggestionsRequest) return;
      setState(() => _suggestionsError = errorMessage(error));
    }
  }

  Future<void> _choosePhoto() async {
    if (_busy) return;
    try {
      final photo = await widget.choosePhoto();
      if (!mounted || photo == null) return;
      setState(() {
        _newPhoto = photo;
        _removePhoto = false;
        _formError = null;
      });
    } on Object {
      if (!mounted) return;
      setState(() => _formError = "Couldn't open that photo. Try another.");
    }
  }

  Future<void> _chooseBirthday() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _birthday ?? DateTime(now.year - 1, now.month, now.day),
      firstDate: _earliestBirthday,
      lastDate: DateTime(now.year, now.month, now.day),
    );
    if (!mounted || picked == null) return;
    setState(() => _birthday = DateTime(picked.year, picked.month, picked.day));
  }

  double? _parsedWeight() {
    final text = _weight.text.trim().replaceAll(',', '.');
    return text.isEmpty ? null : double.tryParse(text);
  }

  bool _validate() {
    final name = _name.text.trim();
    final weightText = _weight.text.trim();
    final weight = _parsedWeight();
    final badWeight =
        weightText.isNotEmpty &&
        (weight == null || weight < _minWeightLb || weight > _maxWeightLb);
    setState(() {
      _nameError = name.isEmpty ? 'Enter a name.' : null;
      _weightError = badWeight
          ? 'Enter a weight between ${_minWeightLb.toInt()} and '
                '${_maxWeightLb.toInt()} lb.'
          : null;
      _formError = null;
    });
    return _nameError == null && _weightError == null;
  }

  Map<String, Object?> _changesTo(Cat cat) {
    final breed = _breed.text.trim();
    final weight = _parsedWeight();
    final reference = cat.referenceWeightLb;
    final sameWeight = weight == null || reference == null
        ? weight == reference
        : (weight - reference).abs() < 0.05;
    final birthday = _birthday;
    final before = cat.birthday;
    final sameBirthday = birthday == null || before == null
        ? birthday == before
        : DateUtils.isSameDay(birthday, before);
    return {
      if (_name.text.trim() != cat.name) 'name': _name.text.trim(),
      if ((breed.isEmpty ? null : breed) != cat.breed)
        'breed': breed.isEmpty ? null : breed,
      if (!sameBirthday)
        'birthday': birthday == null ? null : formatDate(birthday),
      if (_sex != cat.sex) 'sex': _sex.code,
      if (!sameWeight) 'reference_weight_lb': weight,
    };
  }

  Future<void> _save() async {
    if (_busy || !_validate()) return;
    setState(() {
      _busy = true;
      _formError = null;
    });
    final controller = widget.controller;
    try {
      var cat = _saved;
      if (cat == null) {
        final breed = _breed.text.trim();
        cat = await controller.createCat(
          name: _name.text.trim(),
          referenceWeightLb: _parsedWeight(),
          breed: breed.isEmpty ? null : breed,
          birthday: _birthday,
          sex: _sex == CatSex.unknown ? null : _sex,
        );
        _adopt(cat);
      } else {
        final changes = _changesTo(cat);
        if (changes.isNotEmpty) {
          cat = await controller.updateCat(cat.id, changes);
          _adopt(cat);
        }
      }
      await _savePhoto(cat);
    } on Object catch (error) {
      if (!mounted) return;
      final name = _saved?.name;
      setState(() {
        _busy = false;
        _formError = !_editing && name != null
            ? '$name is saved, but the photo did not upload. '
                  '${errorMessage(error)}'
            : errorMessage(error);
      });
      return;
    }
    if (!mounted) return;
    Navigator.of(context).pop(CatSaveResult(catId: _saved!.id));
  }

  // Shows what the server kept, so that saving again only sends new changes.
  void _adopt(Cat cat) {
    _saved = cat;
    if (!mounted) return;
    _name.text = cat.name;
    _breed.text = cat.breed ?? '';
    _weight.text = cat.referenceWeightLb?.toStringAsFixed(1) ?? '';
    _sex = cat.sex;
    _birthday = cat.birthday;
  }

  Future<void> _savePhoto(Cat cat) async {
    final photo = _newPhoto;
    if (photo != null) {
      await widget.controller.setCatPhoto(
        cat.id,
        photo.bytes,
        contentType: photo.contentType,
      );
      _newPhoto = null;
    } else if (_removePhoto && cat.hasPhoto) {
      await widget.controller.removeCatPhoto(cat.id);
      _removePhoto = false;
    }
  }

  Future<void> _archive() async {
    final cat = _saved;
    if (_busy || cat == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Archive ${cat.name}?'),
        content: Text(
          '${cat.name} stops getting new visits and leaves the cats list. '
          'You can restore ${cat.name} from the Cats tab.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Archive'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() {
      _busy = true;
      _formError = null;
    });
    try {
      await widget.controller.updateCat(cat.id, {'archived': true});
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _formError = errorMessage(error);
      });
      return;
    }
    if (!mounted) return;
    Navigator.of(context).pop(CatSaveResult(catId: cat.id, archived: true));
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final suggestions = _suggestions;
    return Scaffold(
      body: Column(
        children: [
          TopBar.back(
            title: _editing ? 'Edit profile' : 'Add a cat',
            onBack: () => Navigator.of(context).pop(),
            backLabel: 'Back to cats',
          ),
          Expanded(
            child: PageBody(
              children: [
                Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: _formMaxWidth),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        _PhotoPicker(
                          cat: _saved,
                          picked: _newPhoto,
                          removed: _removePhoto,
                          enabled: !_busy,
                          onChoose: _choosePhoto,
                          onRemove: () => setState(() {
                            _newPhoto = null;
                            _removePhoto = true;
                          }),
                        ),
                        const SizedBox(height: 14),
                        _FieldsCard(
                          name: _name,
                          breed: _breed,
                          nameError: _nameError,
                          enabled: !_busy,
                          birthday: _birthday,
                          onBirthday: _chooseBirthday,
                          onClearBirthday: () =>
                              setState(() => _birthday = null),
                          sex: _sex,
                          onSex: (sex) => setState(() => _sex = sex),
                          onNameChanged: () {
                            if (_nameError != null) {
                              setState(() => _nameError = null);
                            }
                          },
                        ),
                        const SizedBox(height: 14),
                        _WeightCard(
                          controller: _weight,
                          error: _weightError,
                          enabled: !_busy,
                          suggestions: suggestions,
                          suggestionsError: _suggestionsError,
                          onRetry: () {
                            setState(() => _suggestions = null);
                            unawaited(_loadSuggestions());
                          },
                          onChanged: () => setState(() => _weightError = null),
                        ),
                        if (_formError case final message?) ...[
                          const SizedBox(height: 14),
                          NoticeCard(
                            tone: NoticeTone.warning,
                            message: message,
                          ),
                        ],
                        const SizedBox(height: 14),
                        SizedBox(
                          height: 54,
                          child: FilledButton(
                            onPressed: _busy ? null : _save,
                            child: _busy
                                ? SizedBox.square(
                                    dimension: 22,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 3,
                                      color: p.onPrimary,
                                    ),
                                  )
                                : const Text(
                                    'Save',
                                    style: TextStyle(fontSize: 17),
                                  ),
                          ),
                        ),
                        if (_editing) ...[
                          const SizedBox(height: 8),
                          SizedBox(
                            height: 52,
                            child: TextButton(
                              onPressed: _busy ? null : _archive,
                              style: TextButton.styleFrom(
                                foregroundColor: p.danger,
                              ),
                              child: const Text('Archive this cat'),
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _PhotoPicker extends StatelessWidget {
  const _PhotoPicker({
    required this.cat,
    required this.picked,
    required this.removed,
    required this.enabled,
    required this.onChoose,
    required this.onRemove,
  });

  final Cat? cat;
  final PickedPhoto? picked;
  final bool removed;
  final bool enabled;
  final VoidCallback onChoose;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final existing = cat != null && cat!.hasPhoto && !removed;
    final hasPhoto = picked != null || existing;
    final Widget circle;
    if (picked != null) {
      circle = ClipOval(
        child: Image.memory(
          picked!.bytes,
          width: _photoSize,
          height: _photoSize,
          cacheWidth: (_photoSize * MediaQuery.devicePixelRatioOf(context))
              .ceil(),
          fit: BoxFit.cover,
          excludeFromSemantics: true,
        ),
      );
    } else if (existing) {
      circle = CatAvatar(cat: cat!, size: _photoSize);
    } else {
      circle = CustomPaint(
        painter: _DashedCircle(p.sub),
        child: Container(
          decoration: BoxDecoration(shape: BoxShape.circle, color: p.card),
          alignment: Alignment.center,
          child: Text(
            'Add photo',
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: p.sub,
            ),
          ),
        ),
      );
    }
    return Column(
      children: [
        Semantics(
          button: true,
          label: hasPhoto ? 'Change photo' : 'Add a photo',
          child: ExcludeSemantics(
            child: InkResponse(
              key: const ValueKey('photo-circle'),
              onTap: enabled ? onChoose : null,
              radius: _photoSize / 2,
              child: SizedBox.square(dimension: _photoSize, child: circle),
            ),
          ),
        ),
        if (hasPhoto)
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              TextButton(
                onPressed: enabled ? onChoose : null,
                child: const Text('Change photo'),
              ),
              TextButton(
                onPressed: enabled ? onRemove : null,
                child: const Text('Remove photo'),
              ),
            ],
          ),
      ],
    );
  }
}

class _DashedCircle extends CustomPainter {
  const _DashedCircle(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 2.0;
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke;
    final circle = Path()
      ..addOval(Rect.fromLTWH(0, 0, size.width, size.height).deflate(1));
    for (final metric in circle.computeMetrics()) {
      const dash = 8.0;
      for (var at = 0.0; at < metric.length; at += dash * 2) {
        canvas.drawPath(
          metric.extractPath(at, math.min(at + dash, metric.length)),
          paint,
        );
      }
    }
  }

  @override
  bool shouldRepaint(_DashedCircle oldDelegate) => oldDelegate.color != color;
}

class _FieldsCard extends StatelessWidget {
  const _FieldsCard({
    required this.name,
    required this.breed,
    required this.nameError,
    required this.enabled,
    required this.birthday,
    required this.onBirthday,
    required this.onClearBirthday,
    required this.sex,
    required this.onSex,
    required this.onNameChanged,
  });

  final TextEditingController name;
  final TextEditingController breed;
  final String? nameError;
  final bool enabled;
  final DateTime? birthday;
  final VoidCallback onBirthday;
  final VoidCallback onClearBirthday;
  final CatSex sex;
  final ValueChanged<CatSex> onSex;
  final VoidCallback onNameChanged;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Labelled(
            label: 'Name',
            child: _Input(
              controller: name,
              hint: 'What do you call them?',
              error: nameError,
              enabled: enabled,
              capitalization: TextCapitalization.words,
              onChanged: (_) => onNameChanged(),
            ),
          ),
          const SizedBox(height: 14),
          _Labelled(
            label: 'Breed',
            child: _Input(
              controller: breed,
              hint: 'American Shorthair',
              enabled: enabled,
              capitalization: TextCapitalization.words,
            ),
          ),
          const SizedBox(height: 14),
          _Labelled(
            label: 'Birthday',
            child: Row(
              children: [
                Expanded(
                  child: Semantics(
                    button: true,
                    label: birthday == null
                        ? 'Birthday, not set'
                        : 'Birthday, ${DateFormat.yMMMd().format(birthday!)}',
                    child: ExcludeSemantics(
                      child: InkWell(
                        key: const ValueKey('birthday-field'),
                        onTap: enabled ? onBirthday : null,
                        borderRadius: BorderRadius.circular(14),
                        child: Container(
                          constraints: const BoxConstraints(minHeight: 50),
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          decoration: BoxDecoration(
                            color: p.bg,
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(color: p.line, width: 2),
                          ),
                          alignment: Alignment.centerLeft,
                          child: Text(
                            birthday == null
                                ? 'Choose a date'
                                : DateFormat.yMMMd().format(birthday!),
                            style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w400,
                              color: birthday == null ? p.sub : p.ink,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
                if (birthday != null)
                  TopBarButton(
                    icon: AppIcons.close,
                    label: 'Clear birthday',
                    onPressed: enabled ? onClearBirthday : () {},
                  ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          _Labelled(
            label: 'Sex',
            child: Semantics(
              container: true,
              label: 'Sex',
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final option in CatSex.values)
                    _Choice(
                      label: option.label,
                      selected: option == sex,
                      onTap: enabled ? () => onSex(option) : null,
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Labelled extends StatelessWidget {
  const _Labelled({required this.label, required this.child});

  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 6),
        child,
      ],
    );
  }
}

class _Input extends StatelessWidget {
  const _Input({
    required this.controller,
    required this.hint,
    required this.enabled,
    this.error,
    this.capitalization = TextCapitalization.none,
    this.keyboardType,
    this.formatters,
    this.onChanged,
  });

  final TextEditingController controller;
  final String hint;
  final String? error;
  final bool enabled;
  final TextCapitalization capitalization;
  final TextInputType? keyboardType;
  final List<TextInputFormatter>? formatters;
  final ValueChanged<String>? onChanged;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return TextField(
      controller: controller,
      enabled: enabled,
      textCapitalization: capitalization,
      keyboardType: keyboardType,
      inputFormatters: formatters,
      onChanged: onChanged,
      style: const TextStyle(fontSize: 16),
      decoration: InputDecoration(
        hintText: hint,
        hintStyle: TextStyle(color: p.sub),
        errorText: error,
        errorStyle: TextStyle(fontSize: 14, color: p.danger),
        fillColor: p.bg,
        constraints: const BoxConstraints(minHeight: 50),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 14,
          vertical: 12,
        ),
      ),
    );
  }
}

class _Choice extends StatelessWidget {
  const _Choice({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      button: true,
      selected: selected,
      label: label,
      child: ExcludeSemantics(
        child: Material(
          color: selected ? p.soft : Colors.transparent,
          shape: StadiumBorder(
            side: BorderSide(color: selected ? p.primary : p.line, width: 2),
          ),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: onTap,
            child: Container(
              constraints: const BoxConstraints(minHeight: 44),
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Center(
                widthFactor: 1,
                child: Text(
                  label,
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: selected ? p.primary : p.ink,
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

class _WeightCard extends StatelessWidget {
  const _WeightCard({
    required this.controller,
    required this.error,
    required this.enabled,
    required this.suggestions,
    required this.suggestionsError,
    required this.onRetry,
    required this.onChanged,
  });

  final TextEditingController controller;
  final String? error;
  final bool enabled;
  final CatSuggestions? suggestions;
  final String? suggestionsError;
  final VoidCallback onRetry;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final found = suggestions;
    final clusters = found != null && found.enoughData
        ? found.clusters
        : const <WeightCluster>[];
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Semantics(
            header: true,
            child: const Text(
              'Weight',
              style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'The robot only sends a weight, so this is how it tells your cats '
            'apart. Pick a group it has seen, or type one.',
            style: TextStyle(fontSize: 15, height: 1.35, color: p.sub),
          ),
          const SizedBox(height: 12),
          if (suggestionsError case final message?)
            _SuggestionsFailed(message: message, onRetry: onRetry)
          else if (found == null)
            Semantics(
              label: 'Looking for weights the robot has seen',
              child: const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Center(
                  child: SizedBox.square(
                    dimension: 24,
                    child: CircularProgressIndicator(strokeWidth: 3),
                  ),
                ),
              ),
            )
          else if (clusters.isEmpty)
            Text(
              'The robot has not seen enough unlabelled visits to suggest a '
              'weight yet. Type one, or leave it empty and choose a cat for '
              'each visit.',
              style: TextStyle(fontSize: 15, height: 1.35, color: p.sub),
            )
          else
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final cluster in clusters)
                  _GroupButton(
                    cluster: cluster,
                    selected:
                        controller.text.trim() ==
                        cluster.centerLb.toStringAsFixed(1),
                    onTap: enabled
                        ? () {
                            controller.text = cluster.centerLb.toStringAsFixed(
                              1,
                            );
                            onChanged();
                          }
                        : null,
                  ),
              ],
            ),
          const SizedBox(height: 14),
          _Labelled(
            label: 'Weight in lb',
            child: _Input(
              controller: controller,
              hint: '10.1',
              error: error,
              enabled: enabled,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              formatters: [
                FilteringTextInputFormatter.allow(RegExp('[0-9.,]')),
              ],
              onChanged: (_) => onChanged(),
            ),
          ),
        ],
      ),
    );
  }
}

class _SuggestionsFailed extends StatelessWidget {
  const _SuggestionsFailed({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return NoticeCard(
      tone: NoticeTone.warning,
      lead: "Couldn't load suggested weights.",
      message: message,
      actionLabel: 'Try again',
      onTap: onRetry,
    );
  }
}

class _GroupButton extends StatelessWidget {
  const _GroupButton({
    required this.cluster,
    required this.selected,
    required this.onTap,
  });

  final WeightCluster cluster;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final weight = 'About ${weightOrNone(cluster.centerLb, '')}';
    final visits =
        '${cluster.visits} ${plural(cluster.visits, 'visit', 'visits')}';
    return Semantics(
      button: true,
      selected: selected,
      label: '$weight, $visits',
      child: ExcludeSemantics(
        child: Material(
          color: selected ? p.soft : Colors.transparent,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
            side: BorderSide(color: selected ? p.primary : p.line, width: 2),
          ),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: onTap,
            child: Container(
              constraints: const BoxConstraints(minHeight: 64),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    weight,
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  Text(visits, style: TextStyle(fontSize: 14, color: p.sub)),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
