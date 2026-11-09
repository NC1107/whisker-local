import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../controller.dart';
import '../theme.dart';
import '../widgets/common/common.dart';

class ConnectScreen extends StatefulWidget {
  const ConnectScreen({required this.controller, super.key});

  final AppController controller;

  @override
  State<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends State<ConnectScreen> {
  late final _url = TextEditingController(text: widget.controller.savedUrl);
  final _key = TextEditingController();
  bool _busy = false;
  late String? _error = widget.controller.connectError;

  @override
  void dispose() {
    _url.dispose();
    _key.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    if (_url.text.trim().isEmpty || _key.text.trim().isEmpty) {
      setState(
        () => _error = kIsWeb
            ? 'Enter the access key'
            : 'Enter the address and access key',
      );
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final error = await widget.controller.connect(_url.text, _key.text);
    if (mounted) {
      setState(() {
        _busy = false;
        _error = error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: AutofillGroup(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Align(
                      alignment: Alignment.centerLeft,
                      child: RobotGlyph(width: 72),
                    ),
                    const SizedBox(height: 20),
                    Semantics(
                      header: true,
                      child: Text(
                        'Litter box',
                        style: TextStyle(
                          fontSize: 34,
                          fontWeight: FontWeight.w700,
                          color: palette.ink,
                        ),
                      ),
                    ),
                    const SizedBox(height: 28),
                    if (!kIsWeb) ...[
                      TextField(
                        controller: _url,
                        keyboardType: TextInputType.url,
                        autocorrect: false,
                        textInputAction: TextInputAction.next,
                        decoration: const InputDecoration(
                          labelText: 'Server address',
                          hintText: '192.168.1.50:8420',
                        ),
                      ),
                      const SizedBox(height: 16),
                    ],
                    TextField(
                      controller: _key,
                      obscureText: true,
                      autocorrect: false,
                      enableSuggestions: false,
                      autofocus: kIsWeb,
                      onSubmitted: (_) => _connect(),
                      decoration: const InputDecoration(
                        labelText: 'Access key',
                      ),
                    ),
                    const SizedBox(height: 12),
                    Visibility(
                      visible: _error != null,
                      maintainSize: true,
                      maintainAnimation: true,
                      maintainState: true,
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.error_outline,
                            size: 20,
                            color: palette.danger,
                            semanticLabel: 'Error',
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              _error ?? ' ',
                              style: TextStyle(color: palette.danger),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _busy ? null : _connect,
                      child: Text(_busy ? 'Connecting' : 'Connect'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
