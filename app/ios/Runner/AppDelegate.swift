import Flutter
import UIKit
import UserNotifications

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private static let pushChannelName = "top.npcserver.whiskerlocal/push"
  // How long a request for the device token waits for Apple before giving up.
  private static let tokenWaitSeconds = 10.0

  private var pushChannel: FlutterMethodChannel?
  private var cachedToken: String?
  private var tokenWaiters: [FlutterResult] = []

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    // Without a delegate iOS drops every notification that arrives while the app is open.
    UNUserNotificationCenter.current().delegate = self
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  override func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    willPresent notification: UNNotification,
    withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
  ) {
    completionHandler([.banner, .list, .sound])
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let channel = FlutterMethodChannel(
      name: AppDelegate.pushChannelName,
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    channel.setMethodCallHandler { [weak self] call, result in
      self?.handlePush(call, result: result)
    }
    pushChannel = channel
  }

  // Nothing here asks for permission by itself: the prompt shows only when Dart calls
  // requestPermission, which the app does when the person turns notifications on.
  private func handlePush(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "permission":
      UNUserNotificationCenter.current().getNotificationSettings { settings in
        let granted: Bool
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral: granted = true
        default: granted = false
        }
        DispatchQueue.main.async { result(granted) }
      }
    case "requestPermission":
      UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) {
        granted, _ in
        DispatchQueue.main.async { result(granted) }
      }
    case "token":
      if let token = cachedToken {
        result(token)
        return
      }
      tokenWaiters.append(result)
      UIApplication.shared.registerForRemoteNotifications()
      DispatchQueue.main.asyncAfter(deadline: .now() + AppDelegate.tokenWaitSeconds) {
        [weak self] in
        self?.answerTokenWaiters(nil)
      }
    case "forgetToken":
      cachedToken = nil
      UIApplication.shared.unregisterForRemoteNotifications()
      result(nil)
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  private func answerTokenWaiters(_ token: String?) {
    let waiting = tokenWaiters
    tokenWaiters = []
    for waiter in waiting { waiter(token) }
  }

  override func application(
    _ application: UIApplication,
    didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
  ) {
    // Data's own string conversion is redacted on modern iOS, so the hex is built by hand.
    let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
    cachedToken = hex
    answerTokenWaiters(hex)
    pushChannel?.invokeMethod("onToken", arguments: hex)
    super.application(application, didRegisterForRemoteNotificationsWithDeviceToken: deviceToken)
  }

  override func application(
    _ application: UIApplication,
    didFailToRegisterForRemoteNotificationsWithError error: Error
  ) {
    answerTokenWaiters(nil)
    super.application(application, didFailToRegisterForRemoteNotificationsWithError: error)
  }
}
