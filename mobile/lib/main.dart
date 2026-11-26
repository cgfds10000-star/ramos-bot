import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'data/backend.dart';
import 'ui/theme.dart';
import 'ui/local_login.dart';
import 'ui/accounts.dart';

const native = MethodChannel('ramos/login_crypto');
void main() => runApp(const RamosApp());

class RamosApp extends StatelessWidget {
  const RamosApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'RAMOS BOT',
    debugShowCheckedModeBanner: false,
    theme: ramosTheme(),
    home: const RamosShell(),
  );
}

class RamosShell extends StatefulWidget {
  const RamosShell({super.key});
  @override
  State<RamosShell> createState() => _RamosShellState();
}

class _RamosShellState extends State<RamosShell> with WidgetsBindingObserver {
  final vault = SecureVault();
  final panelPassword = TextEditingController(),
      server = TextEditingController();
  BackendApi? api;
  WebViewController? web;
  Map<String, dynamic> state = {};
  bool loading = true, locked = false, busy = false, popup = false;
  int progress = 0;
  String error = '', pageError = '';
  String? pendingSession;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    native.setMethodCallHandler((call) async {
      if (call.method == 'openSession' && call.arguments is String)
        await handleLink(call.arguments as String);
    });
    boot();
  }

  Future<void> boot() async {
    try {
      await vault.load();
      api = BackendApi(vault);
      server.text = api!.base;
      final link = await native.invokeMethod<String>('initialLink');
      if (link != null)
        pendingSession = Uri.tryParse(link)?.queryParameters['session_id'];
      await refresh();
    } catch (_) {
      if (mounted)
        setState(() {
          loading = false;
          error =
              'Não foi possível abrir os dados protegidos. Feche e abra o aplicativo novamente.';
        });
    }
  }

  Future<void> refresh() async {
    if (api == null) return;
    try {
      state = await api!.get('/state');
      if (!mounted) return;
      setState(() {
        locked = false;
        loading = false;
        error = '';
      });
      if (web == null) await openSite();
      await publishState();
      final id = pendingSession;
      pendingSession = null;
      if (id != null && mounted) await login(id);
    } on BackendFailure catch (failure) {
      if (mounted)
        setState(() {
          locked = failure.locked;
          loading = false;
          error = failure.locked ? '' : failure.message;
        });
    }
  }

  Future<void> panelLogin() async {
    if (busy || api == null) return;
    setState(() {
      busy = true;
      error = '';
    });
    final pass = panelPassword.text;
    panelPassword.clear();
    try {
      final old = api!.base;
      await api!.setBase(server.text);
      if (old != api!.base) web = null;
      await api!.post('/access/login', {'password': pass});
      await native.invokeMethod<void>('setPanelCookie', {
        'url': api!.base,
        'cookie': api!.cookie,
      });
      await refresh();
      await web?.reload();
    } on BackendFailure catch (failure) {
      if (mounted) setState(() => error = failure.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> openSite() async {
    final address = Uri.parse(api!.base);
    await native.invokeMethod<void>('setPanelCookie', {
      'url': api!.base,
      'cookie': api!.cookie,
    });
    final controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(ink)
      ..enableZoom(false)
      ..addJavaScriptChannel(
        'RamosApp',
        onMessageReceived: (message) async {
          final current = await web?.currentUrl();
          if (current == null ||
              Uri.tryParse(current)?.origin != address.origin ||
              popup)
            return;
          try {
            final data = object(jsonDecode(message.message));
            if (data['action'] == 'login' && data['session_id'] is String) {
              await refresh();
              await login(data['session_id'] as String);
            } else if (data['action'] == 'accounts') {
              await accounts();
            } else if (data['action'] == 'export' &&
                data['text'] is String &&
                (data['text'] as String).length <= 4 * 1024 * 1024) {
              await native.invokeMethod<void>('exportText', {
                'text': data['text'],
                'name': data['name'],
              });
            }
          } on FormatException {
            /* Ignore malformed bridge messages. */
          }
        },
      )
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) {
            final uri = Uri.tryParse(request.url);
            if (uri?.scheme == 'ramosbot') {
              handleLink(request.url);
              return NavigationDecision.prevent;
            }
            return uri?.scheme == 'https' && uri?.origin == address.origin
                ? NavigationDecision.navigate
                : NavigationDecision.prevent;
          },
          onProgress: (value) {
            if (mounted) setState(() => progress = value);
          },
          onPageStarted: (_) {
            if (mounted) setState(() => pageError = '');
          },
          onPageFinished: (_) => publishState(),
          onWebResourceError: (failure) {
            if (failure.isForMainFrame == true && mounted)
              setState(
                () => pageError =
                    'Não foi possível carregar o site. Verifique sua conexão.',
              );
          },
        ),
      );
    web = controller;
    if (mounted) setState(() {});
    await controller.loadRequest(address);
  }

  Future<void> handleLink(String value) async {
    final uri = Uri.tryParse(value);
    if (uri?.scheme != 'ramosbot' || uri?.host != 'login') return;
    final id = uri?.queryParameters['session_id'];
    if (id == null) return;
    if (locked || loading || api == null) {
      pendingSession = id;
      return;
    }
    await refresh();
    if (mounted) await login(id);
  }

  Future<void> publishState({String? sessionId}) async {
    if (!mounted || web == null) return;
    try {
      await web!.runJavaScript(
        'window.dispatchEvent(new CustomEvent("ramos:state", {detail: ${jsonEncode({'state': state, if (sessionId != null) 'session_id': sessionId})}}));',
      );
    } on PlatformException {
      // The website also polls its state if the page is still loading.
    }
  }

  Future<void> login(String id, {String? username}) async {
    if (popup || api == null) return;
    final candidates = rows(state['sessions']).where((row) => row['id'] == id);
    if (candidates.isEmpty) {
      notice('A sessão não existe mais no servidor.');
      return;
    }
    final session = candidates.first;
    if ([
          'running',
          'connecting',
          'awaiting_input',
        ].contains(session['status']) ||
        activeRun(object(state['viralize'])[id])) {
      notice('Interrompa a operação desta sessão antes de reconectar.');
      return;
    }
    final match = rows(
      state['accounts'],
    ).where((row) => row['id'] == session['account_id']);
    popup = true;
    if (!mounted) {
      popup = false;
      return;
    }
    final connected = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) => LocalLogin(
        api: api!,
        vault: vault,
        sessionId: id,
        sessionName: session['name'].toString(),
        username:
            username ??
            (match.isEmpty ? '' : match.first['username'].toString()),
        authenticatorOnly:
            object(state['settings'])['two_factor_method'] == 'autenticador',
      ),
    );
    popup = false;
    if (connected == true) {
      await refresh();
      await publishState(sessionId: id);
      notice('Conta conectada. As tarefas podem ser iniciadas no site.');
    }
  }

  Future<void> accounts() async {
    if (popup || api == null) return;
    await refresh();
    if (!mounted || locked) return;
    popup = true;
    final result = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => AccountsPopup(
        api: api!,
        vault: vault,
        initial: state,
        onChanged: (next) async {
          if (!mounted) return;
          setState(() => state = next);
          await publishState();
        },
      ),
    );
    popup = false;
    await refresh();
    if (result == null) return;
    if (result.startsWith('account:')) {
      final username = result.substring(8);
      final account = rows(state['accounts'])
          .where(
            (row) =>
                row['username'].toString().toLowerCase() ==
                username.toLowerCase(),
          )
          .firstOrNull;
      final candidates = rows(state['sessions']);
      final target =
          candidates
              .where(
                (row) => account != null && row['account_id'] == account['id'],
              )
              .firstOrNull ??
          candidates.where((row) => row['authenticated'] != true).firstOrNull;
      if (target != null) {
        await login(target['id'].toString(), username: username);
      } else {
        try {
          final created = await api!.post('/sessions');
          await refresh();
          await login(created['session_id'].toString(), username: username);
        } on BackendFailure catch (failure) {
          notice(failure.message);
        }
      }
      return;
    }
    if (result == '__new__') {
      try {
        final response = await api!.post('/sessions');
        await refresh();
        if (mounted) await login(response['session_id'].toString());
      } on BackendFailure catch (failure) {
        notice(failure.message);
      }
    } else if (mounted) {
      await login(result);
    }
  }

  void notice(String text) {
    if (mounted)
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState value) {
    if (value == AppLifecycleState.resumed && api != null && !popup) refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    native.setMethodCallHandler(null);
    api?.close();
    panelPassword.dispose();
    server.dispose();
    super.dispose();
  }

  Future<void> back() async {
    if (web != null && await web!.canGoBack()) {
      await web!.goBack();
    } else {
      SystemNavigator.pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (loading)
      return const Scaffold(
        body: RamosTexture(
          child: Center(child: CircularProgressIndicator(color: purple)),
        ),
      );
    if (locked || web == null)
      return Scaffold(
        body: RamosTexture(
          child: SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(28),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 440),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      const Center(child: RamosBrand()),
                      const SizedBox(height: 48),
                      const Icon(Icons.fingerprint, color: purple, size: 55),
                      const SizedBox(height: 24),
                      const Text(
                        'Seu espaço de trabalho.',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: 30,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 10),
                      const Text(
                        'Entre no painel para gerenciar suas contas.',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: muted, height: 1.5),
                      ),
                      const SizedBox(height: 30),
                      TextField(
                        controller: panelPassword,
                        obscureText: true,
                        decoration: const InputDecoration(
                          labelText: 'Senha do painel',
                          prefixIcon: Icon(Icons.lock_outline),
                        ),
                        onSubmitted: (_) => panelLogin(),
                      ),
                      const SizedBox(height: 18),
                      GlowButton(
                        'Entrar no painel',
                        icon: Icons.login,
                        busy: busy,
                        onPressed: panelLogin,
                      ),
                      if (error.isNotEmpty) MessageBox(error, error: true),
                      if (!locked && error.isNotEmpty)
                        TextButton(
                          onPressed: refresh,
                          child: const Text('Tentar novamente'),
                        ),
                      const SizedBox(height: 22),
                      ExpansionTile(
                        title: const Text(
                          'Servidor',
                          style: TextStyle(color: muted, fontSize: 13),
                        ),
                        children: [
                          TextField(
                            controller: server,
                            keyboardType: TextInputType.url,
                            autocorrect: false,
                            decoration: const InputDecoration(
                              labelText: 'Endereço HTTPS',
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) back();
      },
      child: Scaffold(
        body: SafeArea(
          child: Stack(
            children: [
              Positioned.fill(child: WebViewWidget(controller: web!)),
              if (progress < 100)
                LinearProgressIndicator(
                  value: progress / 100,
                  minHeight: 2,
                  color: purple,
                  backgroundColor: ink,
                ),
              if (pageError.isNotEmpty)
                Positioned(
                  left: 16,
                  right: 16,
                  top: 16,
                  child: Material(
                    color: ink,
                    borderRadius: BorderRadius.circular(18),
                    child: Column(
                      children: [
                        MessageBox(pageError, error: true),
                        TextButton(
                          onPressed: () => web?.reload(),
                          child: const Text('Recarregar'),
                        ),
                      ],
                    ),
                  ),
                ),
              Positioned(
                right: 18,
                bottom: 102,
                child: Semantics(
                  label: 'Gerenciar contas',
                  button: true,
                  child: Container(
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      boxShadow: [
                        BoxShadow(
                          color: purple.withValues(alpha: .45),
                          blurRadius: 22,
                        ),
                      ],
                    ),
                    child: FloatingActionButton(
                      heroTag: 'accounts',
                      tooltip: 'Contas e logins',
                      onPressed: accounts,
                      backgroundColor: purple,
                      foregroundColor: Colors.white,
                      shape: const CircleBorder(
                        side: BorderSide(color: Color(0xFFEDACFF)),
                      ),
                      child: const Icon(
                        Icons.manage_accounts_outlined,
                        size: 29,
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
