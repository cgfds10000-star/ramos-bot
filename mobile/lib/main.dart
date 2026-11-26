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
  BackendApi? api;
  WebViewController? web;
  Map<String, dynamic> state = {};
  bool loading = true, locked = false, popup = false;
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
      await api!.setBase('https://ramos-bot-production.up.railway.app');
      final link = await native.invokeMethod<String>('initialLink');
      if (link != null)
        pendingSession = Uri.tryParse(link)?.queryParameters['session_id'];
      await openSite();
    } catch (_) {
      if (mounted)
        setState(() {
          loading = false;
          error =
              'Não foi possível abrir os dados protegidos. Feche e abra o aplicativo novamente.';
        });
    }
  }

  Future<bool> refresh() async {
    if (api == null || web == null) return false;
    try {
      await syncPanelCookie();
      state = await api!.get('/state');
      if (!mounted) return false;
      setState(() {
        locked = false;
        loading = false;
        error = '';
      });
      await publishState();
      final id = pendingSession;
      pendingSession = null;
      if (id != null && mounted) await login(id);
      return true;
    } on BackendFailure catch (failure) {
      if (mounted)
        setState(() {
          locked = failure.locked;
          loading = false;
          error = failure.locked ? '' : failure.message;
        });
      return false;
    } on PlatformException {
      if (mounted)
        setState(
          () => error = 'Não foi possível sincronizar o acesso do site.',
        );
      return false;
    }
  }

  Future<void> syncPanelCookie() async {
    if (api == null) return;
    final cookies = await WebViewCookieManager().getCookies(
      domain: Uri.parse(api!.base),
    );
    final access = cookies
        .where((item) => item.name == 'ramos_access')
        .firstOrNull;
    final value = access == null ? '' : '${access.name}=${access.value}';
    if (value != api!.cookie) {
      api!.cookie = value;
      api!.csrf = '';
      await vault.put('backend', {'base': api!.base, 'cookie': value});
    }
  }

  Future<void> openSite() async {
    final address = Uri.parse(api!.base);
    final prior = await WebViewCookieManager().getCookies(domain: address);
    if (!prior.any((item) => item.name == 'ramos_access') &&
        api!.cookie.isNotEmpty) {
      await native.invokeMethod<void>('setPanelCookie', {
        'url': api!.base,
        'cookie': api!.cookie,
      });
    }
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
          onPageFinished: (_) async {
            await refresh();
          },
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
    if (mounted)
      setState(() {
        loading = false;
        error = '';
      });
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
    if (!mounted || web == null || locked || state['sessions'] is! List) return;
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
    final ready = await refresh();
    if (!mounted) return;
    if (!ready) {
      notice(
        locked
            ? 'Entre no painel pelo site aberto para gerenciar suas contas.'
            : error,
      );
      return;
    }
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
    if (web == null)
      return Scaffold(
        body: RamosTexture(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  MessageBox(error, error: true),
                  TextButton(
                    onPressed: boot,
                    child: const Text('Tentar novamente'),
                  ),
                ],
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
                child: _RamosAccountsFab(onPressed: accounts),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RamosAccountsFab extends StatelessWidget {
  final VoidCallback onPressed;
  const _RamosAccountsFab({required this.onPressed});

  @override
  Widget build(BuildContext context) => Semantics(
    label: 'Gerenciar contas',
    button: true,
    child: Tooltip(
      message: 'Contas e logins',
      child: SizedBox.square(
        dimension: 64,
        child: DecoratedBox(
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: const LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color(0xFFF0ABFF), Color(0xFFC54DEB), Color(0xFF773494)],
              stops: [0, .4, 1],
            ),
            boxShadow: const [
              BoxShadow(
                color: Color(0x66310052),
                blurRadius: 22,
                spreadRadius: 2,
              ),
              BoxShadow(
                color: Color(0x8505000B),
                blurRadius: 12,
                offset: Offset(0, 5),
              ),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.all(1.3),
            child: ClipOval(
              child: Material(
                color: Colors.transparent,
                child: Ink(
                  decoration: const BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        Color(0xFFC62CEF),
                        Color(0xFF9509D0),
                        Color(0xFF61009D),
                      ],
                      stops: [0, .42, 1],
                    ),
                  ),
                  child: InkWell(
                    onTap: onPressed,
                    customBorder: const CircleBorder(),
                    splashColor: const Color(0x44F2C6FF),
                    highlightColor: const Color(0x24FFFFFF),
                    focusColor: const Color(0x35F0BFFF),
                    child: const CustomPaint(painter: _AccountsFabArtwork()),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

class _AccountsFabArtwork extends CustomPainter {
  const _AccountsFabArtwork();

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide / 2;
    canvas.save();
    canvas.clipPath(
      Path()..addOval(Rect.fromCircle(center: center, radius: radius)),
    );
    final texture = Paint()
      ..color = const Color(0x0CFFE3FF)
      ..strokeWidth = .65;
    for (double x = 0; x < size.width + size.height; x += 5) {
      canvas.drawLine(
        Offset(x, 0),
        Offset(x - size.height, size.height),
        texture,
      );
    }
    canvas.drawCircle(
      center,
      radius - 1.3,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = .8
        ..shader = const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0x65FFE3FF), Color(0x00FFE3FF)],
        ).createShader(Offset.zero & size),
    );
    canvas.restore();

    const iconSize = 33.0;
    canvas.save();
    canvas.translate((size.width - iconSize) / 2, (size.height - iconSize) / 2);
    canvas.scale(iconSize / 24);
    final outline = Paint()
      ..color = const Color(0xFFFFF8FF)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.8
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    canvas.drawCircle(const Offset(9, 7.8), 4.4, outline);
    canvas.drawPath(
      Path()
        ..moveTo(1.6, 21)
        ..cubicTo(1.6, 11.7, 16.4, 11.7, 16.4, 21),
      outline,
    );
    canvas.drawPath(
      Path()
        ..moveTo(16.1, 3.8)
        ..cubicTo(21.3, 4.8, 21.3, 11.1, 17.3, 12.2)
        ..moveTo(18.3, 15)
        ..cubicTo(20.8, 16.2, 22.2, 18.3, 22.2, 21),
      outline,
    );
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
