import 'dart:typed_data';

import 'package:image_picker/image_picker.dart';

class PickedPhoto {
  const PickedPhoto(this.bytes, this.contentType);

  final Uint8List bytes;
  final String contentType;
}

/// Longest side sent to the server, which scales to this anyway; resizing here
/// keeps a phone photo well under the upload limit.
const _longestSide = 1600.0;

/// Opens the gallery or file chooser of the platform. Null when the person
/// closes it without choosing.
Future<PickedPhoto?> pickCatPhoto({ImagePicker? picker}) async {
  final file = await (picker ?? ImagePicker()).pickImage(
    source: ImageSource.gallery,
    maxWidth: _longestSide,
    maxHeight: _longestSide,
    imageQuality: 90,
  );
  if (file == null) return null;
  return PickedPhoto(
    await file.readAsBytes(),
    contentTypeOf(mimeType: file.mimeType, name: file.name),
  );
}

/// The picker doesn't always know the type (the web gives none for some
/// sources), so fall back to the file extension and then to JPEG.
String contentTypeOf({required String? mimeType, required String name}) {
  if (mimeType != null && mimeType.startsWith('image/')) return mimeType;
  final lower = name.toLowerCase();
  if (lower.endsWith('.png')) return 'image/png';
  if (lower.endsWith('.webp')) return 'image/webp';
  if (lower.endsWith('.gif')) return 'image/gif';
  return 'image/jpeg';
}
