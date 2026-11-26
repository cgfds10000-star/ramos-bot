import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ramos_bot/data/backend.dart';
import 'package:ramos_bot/ui/accounts.dart';
import 'package:ramos_bot/ui/local_login.dart';
import 'package:ramos_bot/ui/theme.dart';

class MemoryVault extends SecureVault {
  @override
  Future<void> put(String key, dynamic value) async {
    data[key] = value;
  }
}

final example = <String, dynamic>{
  'sessions': [
    {
      'id': 'one',
      'name': 'Sessão 1',
      'authenticated': false,
      'account_id': null,
    },
    {
      'id': 'two',
      'name': 'Sessão 2',
      'authenticated': true,
      'account_id': 'account',
    },
  ],
  'accounts': [
    {'id': 'account', 'username': 'teste', 'has_session': true},
  ],
};

class TestApi extends BackendApi {
  final List<String> writes = [];
  final Map<String, dynamic> current = jsonDecode(jsonEncode(example));
  TestApi(super.vault);
  @override
  Future<Map<String, dynamic>> get(String path) async => current;
  @override
  Future<Map<String, dynamic>> post(
    String path, [
    Map<String, dynamic> body = const {},
  ]) async {
    writes.add(path);
    return {'ok': true};
  }

  @override
  Future<Map<String, dynamic>> delete(String path) async {
    writes.add(path);
    current['accounts'] = [];
    for (final session in current['sessions']) {
      session['authenticated'] = false;
      session['account_id'] = null;
    }
    return {'ok': true};
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    final fonts = FontLoader('Ramos')
      ..addFont(rootBundle.load('assets/fonts/roboto-400.ttf'))
      ..addFont(rootBundle.load('assets/fonts/roboto-700.ttf'));
    await fonts.load();
    final icons = FontLoader('MaterialIcons')
      ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
    await icons.load();
  });
  testWidgets(
    'Native login starts with username/password and sends nothing while typing',
    (tester) async {
      final vault = MemoryVault();
      final api = TestApi(vault);
      await tester.pumpWidget(
        MaterialApp(
          theme: ramosTheme(),
          home: Scaffold(
            body: LocalLogin(
              api: api,
              vault: vault,
              sessionId: 'one',
              sessionName: 'Sessão 1',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Login do Instagram'), findsOneWidget);
      expect(find.byType(TextField), findsNWidgets(2));
      expect(find.text('Confirmar código'), findsNothing);
      await tester.enterText(find.byType(TextField).first, 'teste');
      await tester.enterText(find.byType(TextField).last, 'synthetic-password');
      expect(api.writes, isEmpty);
      expect(jsonEncode(vault.data), isNot(contains('synthetic-password')));
      await tester.pumpWidget(const SizedBox());
      api.close();
    },
  );
  testWidgets(
    'Accounts popup uses the same server sessions and confirms deletion',
    (tester) async {
      tester.view.physicalSize = const Size(412, 892);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final vault = MemoryVault();
      final api = TestApi(vault);
      final boundary = GlobalKey();
      final updates = <Map<String, dynamic>>[];
      await tester.pumpWidget(
        MaterialApp(
          theme: ramosTheme(),
          home: Scaffold(
            body: RepaintBoundary(
              key: boundary,
              child: AccountsPopup(
                api: api,
                vault: vault,
                initial: example,
                onChanged: (state) async => updates.add(state),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Contas e logins'), findsOneWidget);
      expect(find.text('Sessão 1'), findsOneWidget);
      expect(find.text('Sessão 2'), findsOneWidget);
      expect(find.text('Adicionar login'), findsOneWidget);
      expect(tester.takeException(), isNull);
      final preview = Platform.environment['RAMOS_PREVIEW'];
      if (preview != null) {
        await tester.runAsync(() async {
          final image =
              await (boundary.currentContext!.findRenderObject()
                      as RenderRepaintBoundary)
                  .toImage(pixelRatio: 2);
          final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
          File(preview).writeAsBytesSync(bytes!.buffer.asUint8List());
          image.dispose();
        });
      }
      await tester.ensureVisible(find.byTooltip('Remover @teste'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Remover @teste'));
      await tester.pumpAndSettle();
      expect(api.writes, isEmpty);
      await tester.tap(find.text('Remover').last);
      await tester.pumpAndSettle();
      expect(api.writes, ['/accounts/account']);
      expect(updates, hasLength(1));
      expect(updates.single['accounts'], isEmpty);
      expect(updates.single['sessions'][1]['authenticated'], false);
      expect(find.text('Contas e logins'), findsOneWidget);
      expect(find.text('@teste'), findsNothing);
      await tester.pumpWidget(const SizedBox());
      api.close();
    },
  );
}
