import 'package:flutter/foundation.dart';

import '../../api.dart';

/// Holds the answer to the newest request only. An answer to an older one, or
/// one that arrives after [dispose], is dropped.
class LatestRequest<T> extends ChangeNotifier {
  T? value;

  /// A message fit to show a person; null while the last request worked.
  String? error;
  bool loading = false;

  int _generation = 0;
  bool _disposed = false;

  /// A [quiet] refresh keeps what is shown when it fails.
  Future<void> run(Future<T> Function() fetch, {bool quiet = false}) async {
    final generation = ++_generation;
    loading = true;
    if (!quiet) error = null;
    notifyListeners();
    T? answer;
    String? failure;
    try {
      answer = await fetch();
    } on ApiException catch (exception) {
      failure = exception.message;
    } on Object {
      failure = const UnexpectedResponse().message;
    }
    if (_disposed || generation != _generation) return;
    loading = false;
    if (failure == null) {
      value = answer;
      error = null;
    } else if (!(quiet && value != null)) {
      error = failure;
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
