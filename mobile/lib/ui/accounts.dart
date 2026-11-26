import 'package:flutter/material.dart';
import '../data/backend.dart';
import 'theme.dart';

class AccountsPopup extends StatefulWidget {
  final BackendApi api;
  final SecureVault vault;
  final Map<String, dynamic> initial;
  const AccountsPopup({
    super.key,
    required this.api,
    required this.vault,
    required this.initial,
  });
  @override
  State<AccountsPopup> createState() => _AccountsPopupState();
}

class _AccountsPopupState extends State<AccountsPopup> {
  late Map<String, dynamic> state;
  bool busy = false;
  String error = '';
  @override
  void initState() {
    super.initState();
    state = widget.initial;
  }

  Future<void> remove(Map<String, dynamic> account) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remover login?'),
        content: Text(
          'Remover @${account['username']} do aplicativo e do servidor? As sessões do painel serão mantidas, sem esta conta.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancelar'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Remover'),
          ),
        ],
      ),
    );
    if (confirm != true || !mounted) return;
    setState(() {
      busy = true;
      error = '';
    });
    try {
      if (account['id'] != null)
        await widget.api.delete('/accounts/${account['id']}');
      final logins = object(widget.vault['logins']);
      logins.removeWhere(
        (_, value) =>
            object(
              object(value)['client'],
            )['owner']?.toString().toLowerCase() ==
            account['username'].toString().toLowerCase(),
      );
      if (object(widget.vault['client'])['owner']?.toString().toLowerCase() ==
          account['username'].toString().toLowerCase()) {
        widget.vault.data.remove('client');
        widget.vault.data.remove('cookies');
      }
      await widget.vault.put('logins', logins);
      state = await widget.api.get('/state');
    } on BackendFailure catch (failure) {
      error = failure.message;
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sessions = rows(state['sessions']);
    final accounts = rows(state['accounts']);
    for (final value in object(widget.vault['logins']).values) {
      final owner = object(object(value)['client'])['owner']?.toString();
      if (owner != null &&
          owner.isNotEmpty &&
          !accounts.any(
            (row) =>
                row['username'].toString().toLowerCase() == owner.toLowerCase(),
          )) {
        accounts.add({'username': owner, 'has_session': true});
      }
    }
    final free = sessions.where((s) => s['authenticated'] != true);
    return Padding(
      padding: EdgeInsets.only(
        top: 70,
        bottom: MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: ClipRRect(
        borderRadius: const BorderRadius.vertical(top: Radius.circular(30)),
        child: RamosTexture(
          child: SafeArea(
            top: false,
            child: ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.all(22),
              children: [
                Row(
                  children: [
                    const Icon(
                      Icons.manage_accounts_outlined,
                      color: purple,
                      size: 28,
                    ),
                    const SizedBox(width: 12),
                    const Expanded(
                      child: Text(
                        'Contas e logins',
                        style: TextStyle(
                          fontSize: 23,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    IconButton(
                      tooltip: 'Fechar',
                      onPressed: busy ? null : () => Navigator.pop(context),
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
                const Text(
                  'Faça o login no celular e use a mesma sessão no site.',
                  style: TextStyle(color: muted, height: 1.5),
                ),
                const SizedBox(height: 22),
                GlowButton(
                  'Adicionar login',
                  icon: Icons.person_add_alt_1_outlined,
                  onPressed: busy
                      ? null
                      : () => Navigator.pop(
                          context,
                          free.isEmpty
                              ? '__new__'
                              : free.first['id'].toString(),
                        ),
                ),
                const SizedBox(height: 24),
                Text(
                  'MINHAS SESSÕES (${sessions.length})',
                  style: const TextStyle(
                    color: muted,
                    fontSize: 12,
                    letterSpacing: 1.3,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 10),
                for (final session in sessions)
                  Container(
                    margin: const EdgeInsets.only(bottom: 10),
                    decoration: BoxDecoration(
                      border: Border.all(color: const Color(0xFF503464)),
                      borderRadius: BorderRadius.circular(20),
                      gradient: const LinearGradient(
                        colors: [Color(0xFF25192E), Color(0xFF120F1C)],
                      ),
                    ),
                    child: ListTile(
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 7,
                      ),
                      leading: const Icon(
                        Icons.desktop_windows_outlined,
                        color: muted,
                      ),
                      title: Text(
                        session['name'].toString(),
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      subtitle: Text(
                        session['authenticated'] == true
                            ? '@${accounts.where((a) => a['id'] == session['account_id']).map((a) => a['username']).firstOrNull ?? ''} · Conectada'
                            : object(
                                    widget.vault.login(
                                      session['id'].toString(),
                                    )?['client'],
                                  )['owner']?.toString() ??
                                  'Pronta para receber um login',
                        style: const TextStyle(color: muted, fontSize: 12),
                      ),
                      trailing: Icon(
                        session['authenticated'] == true
                            ? Icons.check_circle_outline
                            : Icons.login,
                        color: session['authenticated'] == true
                            ? const Color(0xFF64D4BB)
                            : purple,
                      ),
                      onTap: busy
                          ? null
                          : () => Navigator.pop(
                              context,
                              session['id'].toString(),
                            ),
                    ),
                  ),
                TextButton.icon(
                  onPressed: busy
                      ? null
                      : () => Navigator.pop(context, '__new__'),
                  icon: const Icon(Icons.add),
                  label: const Text('Criar outra sessão'),
                ),
                if (accounts.isNotEmpty) ...[
                  const SizedBox(height: 18),
                  const Text(
                    'LOGINS ARMAZENADOS',
                    style: TextStyle(
                      color: muted,
                      fontSize: 12,
                      letterSpacing: 1.3,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 10),
                  for (final account in accounts)
                    ListTile(
                      leading: const CircleAvatar(
                        backgroundColor: Color(0xFF3B194E),
                        child: Icon(Icons.person_outline, color: Colors.white),
                      ),
                      title: Text('@${account['username']}'),
                      onTap: busy
                          ? null
                          : () => Navigator.pop(
                              context,
                              'account:${account['username']}',
                            ),
                      subtitle: Text(
                        account['has_session'] == true
                            ? 'Sessão salva'
                            : 'Aguardando login',
                        style: const TextStyle(color: muted),
                      ),
                      trailing: IconButton(
                        tooltip: 'Remover @${account['username']}',
                        onPressed: busy ? null : () => remove(account),
                        icon: const Icon(
                          Icons.delete_outline,
                          color: Color(0xFFFF8AA5),
                        ),
                      ),
                    ),
                ],
                if (busy)
                  const Center(child: CircularProgressIndicator(color: purple)),
                if (error.isNotEmpty) MessageBox(error, error: true),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
