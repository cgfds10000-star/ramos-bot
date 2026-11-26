# Ramos Bot — aplicativo de logins e site integrado

Aplicativo Android Flutter baseado no ZIP de login fornecido. Abre o site existente em uma WebView sem barra de navegador. O FAB circular de contas fica no canto inferior direito, acima do menu do site, e abre o gerenciador e o popup nativo de login.

## Fluxo

1. Entre com a mesma senha de acesso ao painel. O servidor padrão é `https://ramos-bot-production.up.railway.app`.
2. O site abre dentro do aplicativo, com os mesmos ícones, sessões, macros, proxy, logs e configurações.
3. Toque no FAB de contas para adicionar um login. Escolha uma sessão existente ou crie outra.
4. Usuário, senha, 2FA e eventuais aprovações são processados diretamente no celular pelo código do ZIP. A senha e a chave 2FA não são enviadas ao backend Ramos Bot.
5. Após a confirmação local, o aplicativo envia por HTTPS apenas a sessão autenticada, com a identidade pública do dispositivo, ao endpoint protegido `/api/sessions/ID/import`.
6. O backend valida a sessão com `accounts/current_user/`. Só então a sessão do site aparece como conectada. Caso o servidor recuse essa validação, a sessão local fica salva para tentar sincronizar novamente; não há repetição automática do login.
7. Inicie as tarefas no próprio site. Fechar ou colocar o aplicativo em segundo plano não envia comandos de interrupção. A fila e a execução contínua permanecem no servidor; ao reabrir, o site consulta o andamento atual.

O gerenciador mantém os logins salvos com a criptografia Android Keystore já existente no ZIP. As contas podem ser selecionadas e removidas pelo FAB. Remover uma conta também limpa o vínculo e o arquivo correspondente no servidor, preservando as sessões do painel. Operações em andamento são protegidas pelos controles do backend existente.

O login do site passa a abrir o popup nativo quando usado dentro do aplicativo. Fora do aplicativo, o botão usa o deep link `ramosbot://login?session_id=ID`. Esse link contém apenas o identificador da sessão, sem credenciais.

## Compilar no Windows

Use Flutter 3.50.1 ou posterior compatível, JDK 21 e o Android SDK. Abra o terminal nesta pasta, onde está o `pubspec.yaml`:

```powershell
flutter pub get
flutter analyze --no-fatal-infos
flutter test
flutter build apk --debug --target-platform android-arm64
```

O APK de teste assinado fica em `build/app/outputs/flutter-apk/app-debug.apk`. O identificador continua `com.ramos.app.login`, a versão é `1.1.0+4` e o nome exibido é RAMOS BOT. O projeto não inclui senhas do painel, credenciais do Instagram, chaves de API ou chaves de assinatura de produção.

O repositório também contém a rotina `Android app` no GitHub Actions para compilar e disponibilizar o APK no artefato `RamosBot-Android`.

## Backend necessário

Publique também as alterações de `backend/mobile_session.py`, `backend/app.py` e `src/mobile.js`/`src/main.jsx` do mesmo commit. O backend é o mesmo serviço e usa o mesmo volume e as mesmas APIs; o novo endpoint é uma integração adicional. O estado deve informar `mobile_handoff_version: 1`.

O acesso ao painel é compartilhado com a WebView usando cookie HttpOnly/Secure, sem inserir tokens na URL ou no JavaScript da página. A navegação e a ponte JavaScript são limitadas à origem HTTPS configurada. A exportação de logs usa o seletor de arquivos do Android.

O app não exporta a chave privada do Android Keystore. O servidor usa a sessão recebida para ações autenticadas e nunca tenta refazer o login com senha quando essa sessão expira. Uma nova autenticação exige o aplicativo.

## Verificações

- Login original preservado em `lib/auth`, com apenas um método adicional para formar o pacote de sessão autenticada.
- Testes originais de TOTP, persistência e paridade de requisições mantidos em `tool/`.
- Testes de interface para o popup, edição de credenciais sem envio ao servidor e confirmação de remoção.
- Backend com testes de validação, criptografia, preservação dos IDs das sessões e bloqueio de alterações concorrentes durante a importação.
- Não há serviço de execução de tarefas no Android: a execução continua no backend existente.
