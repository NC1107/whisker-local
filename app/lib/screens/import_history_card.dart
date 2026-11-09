import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../controller.dart';
import '../theme.dart';
import '../widgets/common/common.dart';
import '../widgets/common/csv_picker.dart';
import 'robot_parts.dart';

String _count(int n, String one, String many) => n == 1 ? '1 $one' : '$n $many';

/// The day an export was made, read from a name like `litter_box_activity_2026-11-08.csv`.
/// The file has no years, so the server needs it to tell this November from last.
DateTime? exportDateFromName(String name, {required DateTime today}) {
  final match = RegExp(r'(\d{4})-(\d{2})-(\d{2})').firstMatch(name);
  if (match == null) return null;
  final year = int.parse(match[1]!);
  final month = int.parse(match[2]!);
  final day = int.parse(match[3]!);
  final date = DateTime(year, month, day);
  final real = date.year == year && date.month == month && date.day == day;
  final todayOnly = DateTime(today.year, today.month, today.day);
  return real && !date.isAfter(todayOnly) ? date : null;
}

String _range(DateTime first, DateTime last) {
  final format = DateFormat.MMMd();
  final a = format.format(first);
  final b = format.format(last);
  return a == b ? a : '$a to $b';
}

/// What an import adds or would add, as sentences for a person.
List<String> importSummaryLines(ImportSummary summary) {
  final added = [
    if (summary.visits > 0) _count(summary.visits, 'visit', 'visits'),
    if (summary.cleanCycles > 0)
      _count(summary.cleanCycles, 'clean cycle', 'clean cycles'),
  ];
  final first = summary.firstAt;
  final last = summary.lastAt;
  final skippedCycles = summary.cyclesInterrupted + summary.cyclesInProgress;
  return [
    if (added.isEmpty)
      'There is nothing new in this file to import.'
    else
      '${added.join(' and ')}'
          '${first != null && last != null ? ', from ${_range(first, last)}' : ''}.',
    if (summary.visitsMatchedToCat != null && summary.visits > 0)
      '${summary.visitsMatchedToCat} of the visits fit one of your cats.',
    if (summary.catDetectionsWithoutWeight > 0)
      '${_count(summary.catDetectionsWithoutWeight, 'time', 'times')} a cat '
          'was seen without a weight. Those are left out, because there is '
          'nothing to match to a cat.',
    if (skippedCycles == 1)
      '1 cycle was interrupted or not finished, so it is not kept.'
    else if (skippedCycles > 1)
      '$skippedCycles cycles were interrupted or not finished, so they are '
          'not kept.',
    if (summary.alreadyImported > 0)
      '${_count(summary.alreadyImported, 'entry', 'entries')} came in with '
          'an earlier import.',
    if (summary.afterLiveData > 0)
      '${_count(summary.afterLiveData, 'entry', 'entries')} are from after '
          'this robot started reporting here, so they are left out.',
    if (summary.unreadableRows > 0)
      '${_count(summary.unreadableRows, 'row', 'rows')} could not be read.',
  ];
}

enum _Stage { idle, reading, preview, importing }

/// Brings the Whisker app's activity export into the history, and takes it out again.
class ImportHistoryCard extends StatefulWidget {
  const ImportHistoryCard({
    required this.controller,
    this.pickFile = pickCsvFile,
    this.clock = DateTime.now,
    super.key,
  });

  final AppController controller;
  final Future<PickedCsv?> Function() pickFile;
  final DateTime Function() clock;

  @override
  State<ImportHistoryCard> createState() => _ImportHistoryCardState();
}

class _ImportHistoryCardState extends State<ImportHistoryCard> {
  _Stage _stage = _Stage.idle;
  List<ImportBatch>? _batches;
  String? _batchesError;
  String? _error;
  String? _done;
  bool _undoing = false;
  ImportSummary? _preview;
  Uint8List? _file;
  DateTime? _referenceDate;

  AppController get _controller => widget.controller;

  @override
  void initState() {
    super.initState();
    _loadBatches();
  }

  Future<void> _loadBatches() async {
    setState(() => _batchesError = null);
    try {
      final batches = await _controller.importBatches();
      if (!mounted) return;
      setState(() => _batches = batches);
    } on ApiException catch (err) {
      if (!mounted) return;
      setState(() => _batchesError = err.message);
    }
  }

  Future<void> _choose() async {
    setState(() {
      _error = null;
      _done = null;
    });
    final PickedCsv? picked;
    try {
      picked = await widget.pickFile();
    } on Exception {
      if (!mounted) return;
      setState(() => _error = 'Could not open that file. Try another one.');
      return;
    }
    if (picked == null || !mounted) return;
    if (picked.bytes.length > maxImportBytes) {
      setState(
        () => _error = 'That file is too big to be a Whisker activity export.',
      );
      return;
    }
    final reference = exportDateFromName(picked.name, today: widget.clock());
    setState(() => _stage = _Stage.reading);
    try {
      final summary = await _controller.importWhiskerActivity(
        picked.bytes,
        dryRun: true,
        referenceDate: reference,
      );
      if (!mounted) return;
      setState(() {
        _stage = _Stage.preview;
        _preview = summary;
        _file = picked!.bytes;
        _referenceDate = reference;
      });
    } on ApiException catch (err) {
      if (!mounted) return;
      setState(() {
        _stage = _Stage.idle;
        _error = err.message;
      });
    }
  }

  void _cancel() => setState(() {
    _stage = _Stage.idle;
    _preview = null;
    _file = null;
  });

  Future<void> _confirm() async {
    final file = _file;
    if (file == null) return;
    setState(() {
      _stage = _Stage.importing;
      _error = null;
    });
    try {
      final summary = await _controller.importWhiskerActivity(
        file,
        dryRun: false,
        referenceDate: _referenceDate,
      );
      if (!mounted) return;
      setState(() {
        _stage = _Stage.idle;
        _preview = null;
        _file = null;
        _done =
            'Imported ${_count(summary.visits, 'visit', 'visits')} and '
            '${_count(summary.cleanCycles, 'clean cycle', 'clean cycles')}.';
      });
      await _loadBatches();
    } on ApiException catch (err) {
      if (!mounted) return;
      setState(() {
        _stage = _Stage.preview;
        _error = err.message;
      });
    }
  }

  Future<void> _undo(ImportBatch batch) async {
    final agreed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Undo the last import?'),
        content: Text(
          'This removes ${_count(batch.visits, 'visit', 'visits')} and '
          '${_count(batch.cleanCycles, 'clean cycle', 'clean cycles')} that '
          'came from the file. Anything your robot recorded stays.',
        ),
        actions: [
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(64, 44)),
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Undo import'),
          ),
        ],
      ),
    );
    if (agreed != true || !mounted) return;
    setState(() {
      _undoing = true;
      _error = null;
      _done = null;
    });
    try {
      await _controller.undoImport(batch.id);
      if (!mounted) return;
      setState(() => _done = 'The last import was removed.');
      await _loadBatches();
    } on ApiException catch (err) {
      if (!mounted) return;
      setState(() => _error = err.message);
    } finally {
      if (mounted) setState(() => _undoing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final busy = _stage == _Stage.reading || _stage == _Stage.importing;
    return SurfaceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Semantics(
            header: true,
            child: Text(
              'Import Whisker history',
              style: TextStyle(
                fontSize: 17,
                fontWeight: FontWeight.w700,
                color: palette.ink,
              ),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Bring in the activity you exported from the Whisker app. '
            'Weights become visits, and finished clean cycles are kept.',
            style: TextStyle(fontSize: 15, height: 1.35, color: palette.sub),
          ),
          const SizedBox(height: 12),
          if (_stage == _Stage.preview || _stage == _Stage.importing)
            _previewBlock(context)
          else
            Align(
              alignment: Alignment.centerLeft,
              child: RobotButton(
                key: const ValueKey('import-choose'),
                label: 'Choose CSV file',
                busy: _stage == _Stage.reading,
                onPressed: busy || _undoing ? null : _choose,
              ),
            ),
          if (_stage == _Stage.reading) ...[
            const SizedBox(height: 10),
            Text(
              'Reading the file',
              style: TextStyle(fontSize: 15, color: palette.sub),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 10),
            InlineError(_error!),
          ],
          if (_done != null) ...[
            const SizedBox(height: 10),
            Semantics(
              liveRegion: true,
              child: Text(
                _done!,
                style: TextStyle(fontSize: 15, color: palette.ink),
              ),
            ),
          ],
          if (_stage == _Stage.idle) ...[
            const SizedBox(height: 12),
            Divider(height: 1, color: palette.line),
            const SizedBox(height: 12),
            _lastImport(context),
          ],
        ],
      ),
    );
  }

  Widget _previewBlock(BuildContext context) {
    final palette = context.palette;
    final summary = _preview!;
    final importing = _stage == _Stage.importing;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Semantics(
          container: true,
          label: 'Import preview',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final line in importSummaryLines(summary))
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text(
                    line,
                    key: const ValueKey('import-line'),
                    style: TextStyle(
                      fontSize: 15,
                      height: 1.35,
                      color: palette.ink,
                    ),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 6),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            if (summary.addsAnything)
              RobotButton(
                key: const ValueKey('import-confirm'),
                label: 'Import',
                filled: true,
                busy: importing,
                onPressed: _confirm,
              ),
            RobotButton(
              key: const ValueKey('import-cancel'),
              label: summary.addsAnything ? 'Cancel' : 'Close',
              onPressed: importing ? null : _cancel,
            ),
          ],
        ),
      ],
    );
  }

  Widget _lastImport(BuildContext context) {
    final palette = context.palette;
    final batches = _batches;
    if (batches == null && _batchesError == null) {
      return Text(
        'Checking earlier imports',
        style: TextStyle(fontSize: 15, color: palette.sub),
      );
    }
    if (batches == null) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InlineError('Earlier imports did not load. $_batchesError'),
          const SizedBox(height: 8),
          RobotButton(
            key: const ValueKey('import-retry'),
            label: 'Try again',
            onPressed: _loadBatches,
          ),
        ],
      );
    }
    if (batches.isEmpty) {
      return Text(
        'Nothing has been imported yet.',
        key: const ValueKey('import-empty'),
        style: TextStyle(fontSize: 15, color: palette.sub),
      );
    }
    final last = batches.first;
    final first = last.firstAt;
    final end = last.lastAt;
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Last import',
                style: TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w700,
                  color: palette.ink,
                ),
              ),
              Text(
                '${_count(last.visits, 'visit', 'visits')}'
                '${first != null && end != null ? ', ${_range(first, end)}' : ''}',
                key: const ValueKey('import-last'),
                style: TextStyle(fontSize: 15, color: palette.sub),
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        RobotButton(
          key: const ValueKey('import-undo'),
          label: 'Undo last import',
          busy: _undoing,
          onPressed: _undoing ? null : () => _undo(last),
        ),
      ],
    );
  }
}
