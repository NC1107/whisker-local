import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

enum PushPermission { granted, denied }

/// The phone's side of push, behind an interface so the rest of the app, and
/// its tests, never touch a platform channel.
abstract class PushPlatform {
  /// The platform name the server expects with a token.
  String get name;

  /// What the person already decided, without asking.
  Future<PushPermission> permission();

  /// Shows the system prompt the first time, and answers with the outcome.
  Future<PushPermission> requestPermission();

  Future<String?> token();

  Stream<String> get tokenRefreshed;

  Future<void> forgetToken();
}

/// Only iOS has push for now, because Android delivery needs a service this app does not use.
/// Everywhere else this is null, which hides every notification control.
PushPlatform? defaultPushPlatform() {
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.iOS) return null;
  return ApnsPushPlatform();
}

/// Talks to AppDelegate, which owns the APNs registration. The device token
/// comes straight from Apple, so no push SDK is linked.
class ApnsPushPlatform implements PushPlatform {
  ApnsPushPlatform({MethodChannel? channel})
    : _channel = channel ?? const MethodChannel(channelName) {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'onToken' && call.arguments is String) {
        _refreshed.add(call.arguments as String);
      }
    });
  }

  static const channelName = 'top.npcserver.whiskerlocal/push';

  final MethodChannel _channel;
  final _refreshed = StreamController<String>.broadcast();

  @override
  String get name => 'ios';

  static PushPermission _outcome(bool? granted) =>
      granted == true ? PushPermission.granted : PushPermission.denied;

  @override
  Future<PushPermission> permission() async =>
      _outcome(await _channel.invokeMethod<bool>('permission'));

  @override
  Future<PushPermission> requestPermission() async =>
      _outcome(await _channel.invokeMethod<bool>('requestPermission'));

  @override
  Future<String?> token() async {
    try {
      return await _channel.invokeMethod<String>('token');
    } on PlatformException {
      return null;
    }
  }

  @override
  Stream<String> get tokenRefreshed => _refreshed.stream;

  @override
  Future<void> forgetToken() async {
    try {
      await _channel.invokeMethod<void>('forgetToken');
    } on PlatformException {
      // The server forgets the token either way, so a stale one is harmless.
    }
  }
}
