import 'dart:typed_data';

import 'api.dart';

/// Keeps cat photos in memory, because an image widget cannot send the access
/// key header and the photos are small enough to hold on to.
class PhotoCache {
  PhotoCache(this._fetch, {this.capacity = 64});

  final Future<Uint8List> Function(int catId) _fetch;
  final int capacity;

  // Insertion order is the recency order, which the eviction relies on.
  final _bytes = <String, Uint8List>{};
  final _loading = <String, Future<Uint8List?>>{};

  static String _key(Cat cat) => '${cat.id}:${cat.photoVersion}';

  /// The photo if it is already in memory, so a rebuild never flashes the fallback.
  Uint8List? peek(Cat cat) {
    final key = _key(cat);
    final bytes = _bytes.remove(key);
    if (bytes != null) _bytes[key] = bytes;
    return bytes;
  }

  /// Null when the cat has no photo or it can't be loaded right now.
  Future<Uint8List?> load(Cat cat) {
    if (!cat.hasPhoto) return Future.value();
    final cached = peek(cat);
    if (cached != null) return Future.value(cached);
    final key = _key(cat);
    return _loading[key] ??= _download(cat, key);
  }

  Future<Uint8List?> _download(Cat cat, String key) async {
    try {
      final bytes = await _fetch(cat.id);
      _forget(cat.id);
      _bytes[key] = bytes;
      while (_bytes.length > capacity) {
        _bytes.remove(_bytes.keys.first);
      }
      return bytes;
    } on ApiException {
      return null;
    } finally {
      _loading.remove(key);
    }
  }

  void _forget(int catId) =>
      _bytes.removeWhere((key, _) => key.startsWith('$catId:'));

  void clear() {
    _bytes.clear();
    _loading.clear();
  }
}
