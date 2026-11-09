import 'package:flutter/services.dart';

/// Under this shortest side a device is a phone, matching Android's own
/// definition of a small screen, so tablets keep rotating.
const phoneShortestSide = 600.0;

const portraitOnly = [DeviceOrientation.portraitUp];

bool isPhone(Size logicalSize) => logicalSize.shortestSide < phoneShortestSide;
