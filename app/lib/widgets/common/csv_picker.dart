import 'dart:typed_data';

import 'package:file_selector/file_selector.dart';

class PickedCsv {
  const PickedCsv(this.bytes, this.name);

  final Uint8List bytes;
  final String name;
}

const _csvTypes = XTypeGroup(
  label: 'CSV',
  extensions: ['csv'],
  mimeTypes: ['text/csv', 'text/comma-separated-values'],
  // iOS only filters by these identifiers, not by extension.
  uniformTypeIdentifiers: ['public.comma-separated-values-text'],
);

/// Opens the file chooser of the platform. Null when the person closes it
/// without choosing.
Future<PickedCsv?> pickCsvFile() async {
  final file = await openFile(acceptedTypeGroups: const [_csvTypes]);
  if (file == null) return null;
  return PickedCsv(await file.readAsBytes(), file.name);
}
