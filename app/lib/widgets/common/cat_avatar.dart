import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../api.dart';
import '../../theme.dart';
import 'app_scope.dart';

/// A cat's photo, loaded with the access key and kept in memory, or [fallback]
/// when it has none, hasn't arrived yet or can't be loaded.
class CatPhoto extends StatefulWidget {
  const CatPhoto({
    required this.cat,
    required this.fallback,
    this.decodeWidth,
    super.key,
  });

  final Cat cat;
  final Widget fallback;

  /// Pixels to decode the image at, so a 1600 px photo doesn't sit in memory
  /// at full size behind a 52 px circle.
  final int? decodeWidth;

  @override
  State<CatPhoto> createState() => _CatPhotoState();
}

class _CatPhotoState extends State<CatPhoto> {
  Uint8List? _bytes;
  Future<Uint8List?>? _loading;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _refresh();
  }

  @override
  void didUpdateWidget(CatPhoto oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.cat.id != widget.cat.id ||
        oldWidget.cat.photoVersion != widget.cat.photoVersion ||
        oldWidget.cat.hasPhoto != widget.cat.hasPhoto) {
      _refresh();
    }
  }

  void _refresh() {
    final photos = AppScope.maybeOf(context)?.photos;
    final cat = widget.cat;
    if (photos == null || !cat.hasPhoto) {
      _bytes = null;
      _loading = null;
      return;
    }
    _bytes = photos.peek(cat);
    if (_bytes != null) {
      _loading = null;
      return;
    }
    final loading = photos.load(cat);
    _loading = loading;
    loading.then((bytes) {
      if (mounted && _loading == loading && bytes != null) {
        setState(() => _bytes = bytes);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final bytes = _bytes;
    if (bytes == null) return widget.fallback;
    return Image.memory(
      bytes,
      fit: BoxFit.cover,
      cacheWidth: widget.decodeWidth,
      gaplessPlayback: true,
      excludeFromSemantics: true,
      errorBuilder: (_, _, _) => widget.fallback,
    );
  }
}

String _initialOf(Cat cat) {
  final name = cat.name.trim();
  return name.isEmpty
      ? '?'
      : String.fromCharCode(name.runes.first).toUpperCase();
}

/// A round picture of a cat: its photo, or its first letter on the cat's color.
class CatAvatar extends StatelessWidget {
  const CatAvatar({required this.cat, this.size = 52, super.key});

  final Cat cat;
  final double size;

  @override
  Widget build(BuildContext context) {
    final color = context.palette.catColor(cat.colorIndex);
    final ratio = MediaQuery.devicePixelRatioOf(context);
    final initial = Container(
      color: color.background,
      alignment: Alignment.center,
      child: Text(
        _initialOf(cat),
        style: TextStyle(
          fontSize: size * 0.46,
          height: 1,
          fontWeight: FontWeight.w700,
          color: color.foreground,
        ),
      ),
    );
    return Semantics(
      image: true,
      label: cat.name,
      child: ExcludeSemantics(
        child: SizedBox.square(
          dimension: size,
          child: ClipOval(
            child: CatPhoto(
              cat: cat,
              fallback: initial,
              decodeWidth: (size * ratio).ceil(),
            ),
          ),
        ),
      ),
    );
  }
}

/// A wide picture of a cat that fills its width: the photo, or the cat's color
/// with its first letter. Round the corners with [borderRadius].
class CatBanner extends StatelessWidget {
  const CatBanner({
    required this.cat,
    this.height = 150,
    this.borderRadius = BorderRadius.zero,
    super.key,
  });

  final Cat cat;
  final double height;
  final BorderRadius borderRadius;

  @override
  Widget build(BuildContext context) {
    final color = context.palette.catColor(cat.colorIndex);
    final initial = Container(
      color: color.background,
      alignment: Alignment.center,
      child: Text(
        _initialOf(cat),
        style: TextStyle(
          fontSize: height * 0.5,
          height: 1,
          fontWeight: FontWeight.w700,
          color: color.foreground,
        ),
      ),
    );
    return Semantics(
      image: true,
      label: cat.name,
      child: ExcludeSemantics(
        child: ClipRRect(
          borderRadius: borderRadius,
          child: SizedBox(
            height: height,
            width: double.infinity,
            child: CatPhoto(cat: cat, fallback: initial, decodeWidth: 1200),
          ),
        ),
      ),
    );
  }
}
