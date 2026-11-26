import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../auth/login_client.dart';
import '../auth/http2_transport.dart';
import '../auth/platform_crypto.dart';
import '../data/backend.dart';
import 'theme.dart';

class LocalLogin extends StatefulWidget {
  final BackendApi api;
  final SecureVault vault;
  final String sessionId;
  final String sessionName;
  final String username;
  final bool authenticatorOnly;
  const LocalLogin({
    super.key,
    required this.api,
    required this.vault,
    required this.sessionId,
    required this.sessionName,
    this.username = '',
    this.authenticatorOnly = false,
  });
  @override
  State<LocalLogin> createState() => _LocalLoginState();
}

class _LocalLoginState extends State<LocalLogin> {
  final user = TextEditingController(),
      password = TextEditingController(),
      code = TextEditingController();
  Http2Transport? transport;
  LoginClient? client;
  Map<String, dynamic>? savedHandoff;
  Timer? ticker;
  bool hide = true, syncing = false;
  String error = '';
  @override
  void initState() {
    super.initState();
    user.text = widget.username;
    load();
    ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && client?.retryAt != null) setState(() {});
    });
  }

  Future<void> load() async {
    try {
      final templates =
          jsonDecode(await rootBundle.loadString('assets/login_payloads.json'))
              as Map<String, dynamic>;
      transport = Http2Transport();
      client = LoginClient(
        crypto: AndroidLoginCrypto(),
        authenticatorOnly: widget.authenticatorOnly,
        send: transport!.send,
        templates: templates,
        clearCookies: transport!.clearCookies,
        onChange: () {
          if (mounted) setState(() {});
        },
        onCheckpoint: () async {
          final snapshot = client!.snapshot();
          transport!.applyLoginCookies(
            snapshot['session'] as String,
            snapshot['userId'] as String,
          );
          await widget.vault.saveLogin(widget.sessionId, {
            'client': snapshot,
            'cookies': transport!.cookieSnapshot(),
            if (savedHandoff != null) 'handoff': savedHandoff,
          });
        },
      );
      var prior =
          (widget.username.isNotEmpty
              ? widget.vault.login('account:${widget.username.toLowerCase()}')
              : null) ??
          widget.vault.login(widget.sessionId) ??
          (widget.vault['client'] is Map
              ? {
                  'client': widget.vault['client'],
                  'cookies': widget.vault['cookies'],
                }
              : null);
      if (prior != null && widget.username.isNotEmpty && object(prior['client'])['owner']?.toString().toLowerCase() != widget.username.toLowerCase()) prior = null;
      if (prior != null && prior['client'] is Map) {
        client!.restore(Map<String, dynamic>.from(prior['client']));
        transport!.restoreCookies(prior['cookies']);
        user.text = client!.username;
        if (prior['handoff'] is Map)
          savedHandoff = Map<String, dynamic>.from(prior['handoff']);
        if (savedHandoff == null && client!.userId.isNotEmpty)
          savedHandoff = await client!.serverSession(allowStored: true);
      }
      if (mounted) setState(() {});
    } catch (_) {
      if (mounted)
        setState(
          () => error = 'Não foi possível carregar o login do aplicativo.',
        );
    }
  }

  Future<void> login() async {
    if (client == null || client!.busy || syncing) return;
    final pass = password.text;
    password.clear();
    code.clear();
    savedHandoff = null;
    setState(() => error = '');
    FocusScope.of(context).unfocus();
    await client!.login(user.text.trim(), pass);
    if (mounted && client!.stage == LoginStage.success) await sync();
  }

  Future<void> otherAccount() async {
    transport?.close();
    client = null;
    savedHandoff = null;
    user.clear();
    password.clear();
    code.clear();
    await widget.vault.saveLogin(widget.sessionId, {});
    if (!mounted) return;
    setState(() {});
    final templates =
        jsonDecode(await rootBundle.loadString('assets/login_payloads.json'))
            as Map<String, dynamic>;
    transport = Http2Transport();
    client = LoginClient(
      crypto: AndroidLoginCrypto(),
      send: transport!.send,
      templates: templates,
      authenticatorOnly: widget.authenticatorOnly,
      clearCookies: transport!.clearCookies,
      onChange: () {
        if (mounted) setState(() {});
      },
      onCheckpoint: () async {
        final snapshot = client!.snapshot();
        transport!.applyLoginCookies(
          snapshot['session'] as String,
          snapshot['userId'] as String,
        );
        await widget.vault.saveLogin(widget.sessionId, {
          'client': snapshot,
          'cookies': transport!.cookieSnapshot(),
          if (savedHandoff != null) 'handoff': savedHandoff,
        });
      },
    );
    if (mounted) setState(() {});
  }

  Future<void> submit() async {
    final entered = code.text;
    code.clear();
    FocusScope.of(context).unfocus();
    await client!.submitCode(entered);
    if (mounted && client!.stage == LoginStage.success) await sync();
  }

  Future<void> sync({bool reuse = false}) async {
    if (syncing) return;
    setState(() {
      syncing = true;
      error = '';
    });
    try {
      final body = reuse ? savedHandoff! : await client!.serverSession();
      savedHandoff = body;
      await widget.vault.saveLogin(widget.sessionId, {
        'client': client!.snapshot(),
        'cookies': transport!.cookieSnapshot(),
        'handoff': body,
      });
      await widget.api.post('/sessions/${widget.sessionId}/import', body);
      if (mounted) Navigator.pop(context, true);
    } catch (failure) {
      if (mounted)
        setState(
          () => error = failure is BackendFailure
              ? failure.message
              : failure is LoginFailure
              ? failure.message
              : 'Não foi possível conectar a sessão ao servidor.',
        );
    } finally {
      if (mounted) setState(() => syncing = false);
    }
  }

  @override
  void dispose() {
    ticker?.cancel();
    transport?.close();
    user.dispose();
    password.dispose();
    code.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = client;
    final busy = syncing || c?.busy == true;
    final stage = c?.stage ?? LoginStage.preparing;
    final isCode = stage == LoginStage.code;
    return PopScope(
      canPop: !busy,
      child: Dialog(
        insetPadding: const EdgeInsets.all(18),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(26),
          child: RamosTexture(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(23),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      const Expanded(child: RamosBrand(size: 19)),
                      IconButton(
                        tooltip: 'Fechar',
                        onPressed: busy
                            ? null
                            : () => Navigator.pop(context, false),
                        icon: const Icon(Icons.close),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  Center(
                    child: Container(
                      padding: const EdgeInsets.all(17),
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: purple.withValues(alpha: .15),
                        border: Border.all(
                          color: purple.withValues(alpha: .45),
                        ),
                      ),
                      child: Icon(
                        isCode
                            ? Icons.verified_user_outlined
                            : Icons.person_outline,
                        size: 36,
                        color: const Color(0xFFE1B3FA),
                      ),
                    ),
                  ),
                  const SizedBox(height: 18),
                  Text(
                    isCode
                        ? 'Verificação em duas etapas'
                        : 'Login do Instagram',
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 23,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 7),
                  Text(
                    widget.sessionName,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: muted),
                  ),
                  const SizedBox(height: 24),
                  if (c == null)
                    const Center(
                      child: CircularProgressIndicator(color: purple),
                    )
                  else if (busy) ...[
                    const Center(
                      child: SizedBox(
                        width: 34,
                        height: 34,
                        child: CircularProgressIndicator(
                          strokeWidth: 3,
                          color: purple,
                        ),
                      ),
                    ),
                    const SizedBox(height: 18),
                    Text(
                      syncing ? 'Conectando a sessão ao servidor…' : c.message,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: muted, height: 1.4),
                    ),
                  ] else if (isCode) ...[
                    Text(
                      c.message,
                      style: const TextStyle(color: muted, height: 1.4),
                    ),
                    const SizedBox(height: 18),
                    TextField(
                      controller: code,
                      autofocus: true,
                      autocorrect: false,
                      enableSuggestions: false,
                      decoration: InputDecoration(
                        labelText: c.codeHint,
                        prefixIcon: const Icon(Icons.shield_outlined),
                      ),
                      onSubmitted: (_) => submit(),
                    ),
                    const SizedBox(height: 18),
                    GlowButton(
                      'Confirmar código',
                      icon: Icons.verified_outlined,
                      onPressed: submit,
                    ),
                  ] else if (stage == LoginStage.approval) ...[
                    MessageBox(c.message),
                    GlowButton(
                      'Verificar aprovação',
                      onPressed: () async {
                        await c.checkApproval();
                        if (mounted && c.stage == LoginStage.success)
                          await sync();
                      },
                    ),
                  ] else if (stage == LoginStage.success) ...[
                    const MessageBox(
                      'Login confirmado no celular. Conecte esta sessão ao servidor para continuar.',
                    ),
                    GlowButton(
                      'Conectar ao servidor',
                      icon: Icons.cloud_done_outlined,
                      onPressed: () => sync(),
                    ),
                  ] else ...[
                    TextField(
                      controller: user,
                      autocorrect: false,
                      enableSuggestions: false,
                      keyboardType: TextInputType.emailAddress,
                      decoration: const InputDecoration(
                        labelText: 'Usuário do Instagram',
                        prefixIcon: Icon(Icons.person_outline),
                      ),
                    ),
                    const SizedBox(height: 14),
                    TextField(
                      controller: password,
                      obscureText: hide,
                      autocorrect: false,
                      enableSuggestions: false,
                      decoration: InputDecoration(
                        labelText: 'Senha',
                        prefixIcon: const Icon(Icons.lock_outline),
                        suffixIcon: IconButton(
                          tooltip: hide ? 'Mostrar senha' : 'Ocultar senha',
                          onPressed: () => setState(() => hide = !hide),
                          icon: Icon(
                            hide
                                ? Icons.visibility_outlined
                                : Icons.visibility_off_outlined,
                          ),
                        ),
                      ),
                      onSubmitted: (_) => login(),
                    ),
                    if (stage == LoginStage.failed)
                      MessageBox(c.message, error: true),
                    const SizedBox(height: 20),
                    GlowButton(
                      'Entrar',
                      icon: Icons.login,
                      onPressed: c.retrySeconds > 0 ? null : login,
                    ),
                    if (savedHandoff != null)
                      TextButton(
                        onPressed: () => sync(reuse: true),
                        child: const Text('Reutilizar login salvo'),
                      ),
                    if (c.hasSavedIdentity)
                      TextButton(
                        onPressed: otherAccount,
                        child: const Text('Usar outra conta'),
                      ),
                  ],
                  if (error.isNotEmpty) MessageBox(error, error: true),
                  const SizedBox(height: 16),
                  const Text(
                    'Sua senha e o código 2FA são usados apenas no celular.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: muted, fontSize: 12, height: 1.4),
                  ),
                  if (c != null && !busy)
                    TextButton(
                      onPressed: () async {
                        await Clipboard.setData(
                          ClipboardData(
                            text: const JsonEncoder.withIndent(
                              '  ',
                            ).convert(c.diagnostic()),
                          ),
                        );
                      },
                      child: const Text(
                        'Copiar diagnóstico',
                        style: TextStyle(fontSize: 12),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
