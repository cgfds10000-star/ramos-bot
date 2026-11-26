# Ramos Bot · Mobile

Painel React com o visual roxo neon da referência e backend Python real. O login do Instagram e o código de verificação são informados na própria página.

## Testar no Windows

1. Baixe o projeto em **Code → Download ZIP** e extraia a pasta.
2. Instale Python 3.11+ e Node.js 22+ se necessário.
3. Abra **INICIAR-RAMOS-BOT.cmd**.
4. Acesse **http://127.0.0.1:8765**. Escolha a sessão e conecte sua conta.

A primeira execução instala as dependências. O ZIP de entrega também inclui a interface compilada. Nenhum login pessoal, senha, código ou token do GitHub faz parte do projeto.

## Fluxo do Instagram

- Criar, selecionar, pesquisar e remover sessões. O login abre em um popup com abas que podem ser deslizadas horizontalmente, arrastadas com o mouse ou selecionadas pelas setas do teclado.
- Conectar com usuário/senha, importar um `login.txt` escolhido por você ou reutilizar uma sessão salva.
- Quando o Instagram solicita 2FA no login, o popup pede o código e segue a mesma continuação automática do script Python, usando o contexto da mesma tentativa. O Instagram determina se é autenticador, SMS/e-mail ou código de backup. O código é informado na página e enviado uma única vez; a senha não é reenviada.
- A tela inicial pede usuário e senha. O campo de 2FA aparece quando a mesma tentativa de login solicita verificação. O núcleo Python acompanha as continuações do APK de referência: contexto Bloks, identificador nativo do autenticador e transição de AP para 2FA. Cada tela usa o contexto atualizado pela resposta anterior; clientes antigos da API ainda podem fornecer `verification_code` numérico opcional.
- O popup 2FA tem um campo único: digite o código ou cole a chave Base32 com espaços e toque em **Confirmar**. O site mostra uma prévia do TOTP. Ao confirmar uma chave, ela é entregue por HTTPS ao worker da mesma tentativa, que usa a lógica do script para gerar o código no instante do envio ao Instagram, depois das telas de preparação. Chave, código e senha não são gravados em disco nem aparecem no estado público ou nos logs. Não há envio automático durante a digitação nem repetição após falha. SMS/e-mail continuam aceitando somente o código recebido; backup aceita oito dígitos.
- A opção de aprovação por outro aparelho foi removida. Quando o servidor não fornece um contexto de código compatível, a tentativa é encerrada e seu diagnóstico pode ser exportado. Isso não contorna verificações obrigatórias do Instagram.
- `buscar "nome" --limite 10`, `buscar "nome" --geral`, `perfil usuario`, `perfil --id 123456`, `seguir usuario`, `seguir --id 123456`, `curtir URL_OU_ID`.
- Macros iniciadas pelo usuário, interrupção e proxy. A atividade aparece na aba Logs; a tela de sessões não abre um terminal ou campo de comandos. O popup mantém login e 2FA.
- Diagnóstico exportável sem senhas ou tokens. Escritas e códigos não são reenviados automaticamente.

O núcleo usa o `instagram_login_apk.py` fornecido, com `instagrapi==3.2.19`. A revisão `caa-5-web-apk-2fa` corrige a continuação do 2FA em modo automático conforme o APK fornecido; o worker adapta `input` e `getpass` do terminal para o popup e respeita o método de autenticação selecionado no painel. Uma sessão só aparece como conectada depois de receber a confirmação autenticada do Instagram. Os testes usam processos e dados sintéticos; não houve login em uma conta real durante esta entrega. O login do site continua saindo pela conexão configurada no servidor; no APK, sai pelo aparelho.

## Fila do Viralize em Macros

1. Conecte suas contas do Instagram em **Sessões**. Abra **Macros**, informe o token do Viralize (`vzs_…`), selecione as sessões conectadas e clique em **Aplicar plugin**.
2. Escolha **Seguir perfis**, **Curtir publicações** ou **Seguir e curtir**, o limite da rodada e o **Ritmo das ações**, e clique em **Iniciar**. Cada sessão processa uma tarefa por vez, até o limite escolhido. Se já houver uma ação pendente, ela é retomada em vez de aceitar outra oferta. No modo contínuo, uma fila vazia mantém o executor aguardando novas tarefas.
3. O executor Python usa o perfil ou a publicação da tarefa. O Viralize só recebe a ação para análise após a confirmação do Instagram. Solicitações pendentes, curtidas que já existiam e ações inconclusivas não são enviadas. A recompensa depende da análise do Viralize.
4. **Pular tarefa** aparece quando é possível ignorar a oferta ou encerrar a ação aceita. **Interromper** cancela a rodada quando possível; não desfaz solicitações já enviadas. O andamento aparece na aba Logs, com horário, descrição e identificação da sessão.

O token é enviado por Bearer à API oficial apenas nas ações do Viralize. O navegador conserva a chave localmente; a execução no servidor também a conserva em `executions.json`, usando a proteção de armazenamento descrita abaixo, para permitir a retomada. O token não é incluído nos dados públicos da sessão nem nos logs. Só Instagram é executado nesta integração; não são postados comentários nem executadas tarefas de TikTok. O login e o 2FA existentes não mudam.

Se o painel mostrar um bloqueio do Cloudflare, o fato de o site abrir no navegador não garante que o servidor do Ramos Bot consiga acessar a API. Envie o código do bloqueio e o ID da requisição, quando exibido, ao suporte do Viralize para verificar o acesso da integração. Não envie seu token ao suporte.

## Ritmo das ações em Macros

O seletor **Ritmo das ações** controla o fluxo normal: consultar a tarefa → aguardar → aceitar → aguardar → executar no Instagram → aguardar → confirmar no Viralize → aguardar a próxima tarefa. O ritmo padrão é **Moderado**. As durações variam dentro das faixas abaixo, sorteadas a cada etapa, em segundos.

| Ritmo | Antes de aceitar e confirmar | Antes de executar | Entre tarefas | Entre rodadas contínuas |
| --- | --- | --- | --- | --- |
| Moderado | 2–4 s | 4–8 s | 30–60 s | 60–120 s |
| Pausado | 3–6 s | 6–12 s | 60–90 s | 120–180 s |
| Lento | 4–8 s | 8–15 s | 90–150 s | 180–300 s |

O painel mostra **Em intervalo** e a contagem regressiva de cada sessão. **Interromper** encerra a pausa imediatamente. O ritmo escolhido fica salvo no navegador e em cada execução no servidor; prazos pendentes sobrevivem ao reinício. O intervalo entre tarefas também se aplica no modo de rodada única. Ao atingir o limite, a rodada única termina imediatamente; o modo contínuo usa o intervalo entre rodadas.

As faixas são configurações iniciais de espaçamento, sem garantir um tempo ideal para todas as contas ou reproduzir uma pessoa. A consulta de fila vazia continua a cada 30 segundos. Erros, bloqueios e verificações continuam pausando o executor, sem novas tentativas automáticas. Uma interrupção depois de executar a ação no Instagram e antes de confirmar continua exigindo revisão, para evitar repetir a ação.

A API de início individual ou em lote aceita `speed` com `moderate`, `calm` ou `slow`. Clientes antigos que omitem o campo usam `moderate`; valores desconhecidos são recusados antes de iniciar qualquer sessão. O snapshot de estado inclui `viralize_pacing` com as faixas usadas pelo servidor e, por execução, `speed`, `phase` e `next_check_at`.

## Histórico de atividades

Logs usa uma tabela com **Hora** e **Descrição**, busca, filtros de sessão e período, filtros de severidade e ordenação. A faixa de cada linha é azul para informações, verde para sucessos, amarela para avisos, vermelha para erros e roxa para comandos. O tipo também fica disponível para leitores de tela. Os horários usam o fuso do navegador; registros novos incluem a data para os filtros Hoje e Últimos 7 dias. A exportação conserva as mensagens completas.

## Desenvolvimento

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
npm ci
npm run dev
```

O comando de desenvolvimento inicia Vite e Python juntos. Para compilar: `npm run build`. Para executar a compilação: `.venv/bin/python backend/app.py`. A interface fica em `dist/client`.

```bash
npm test
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## Executar em um servidor próprio

O `Dockerfile` compila o React e executa o Python no mesmo serviço. Ao expor o painel, configure `RAMOS_ADMIN_PASSWORD` e `RAMOS_ENCRYPTION_KEY`; o servidor recusa inicializar publicamente sem proteção. O acesso ao painel usa a senha configurada no formulário da própria página. Depois, o login do Instagram e o código 2FA são informados dentro da sessão. Não há janela de autenticação do navegador. Utilize HTTPS no proxy de entrada.

Gere a chave de criptografia com:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Monte um volume persistente em `/data`. A chave de criptografia precisa permanecer a mesma entre reinicializações. Com a chave configurada, os dados e sessões são gravados com Fernet. No uso local sem chave, os arquivos ficam privados na pasta `data`; senhas e códigos não são gravados em disco. A remoção de uma conta apaga a sessão salva. Logs ficam em memória e podem ser exportados antes de reiniciar.

`RAMOS_API_KEY` é uma alternativa para acesso por proxy privado. Nunca coloque segredos em variáveis `VITE_*`, no código React ou no repositório.

## Proxy das sessões

A aba Proxy usa campos separados de host, porta, usuário e senha, com configuração avançada para HTTP, HTTPS ou SOCKS5. Ela salva uma conexão única para Instagram (login, 2FA, API privada, pública e GraphQL) e Viralize. Aceita URLs HTTP/HTTPS/SOCKS5 e o formato `host:porta:usuario:senha`. SOCKS5 usa DNS remoto. As credenciais ficam no servidor e não retornam ao navegador. Campos de autenticação não editados mantêm os valores salvos; a opção de remover credenciais fica na configuração avançada. Ativar, trocar ou desativar o proxy desconecta as sessões; operações em andamento precisam terminar primeiro.

“Salvar e aplicar” verifica o IP pelo servidor usando HTTPS com ipify. “Verificar conexão” repete somente essa verificação. O IP exibido é o último observado, com data e hora; proxies rotativos podem mudar o IP entre conexões. Falhas do proxy encerram a chamada, sem repetir diretamente. A comunicação do navegador com o painel continua usando HTTPS normalmente.

A saída padrão é a conexão direta do servidor Railway. Na primeira inicialização desta revisão, a configuração antiga de proxy/saída pelo PC é limpa, inclusive no volume persistente; `RAMOS_INITIAL_PROXY` não é mais importada. O painel não depende de um agente, túnel ou navegador do PC. A aba Proxy permanece disponível somente para uma nova configuração explícita posterior.

## Execução no servidor

Configurações informa o modo `server`: todas as sessões selecionadas são enviadas ao servidor juntas ao tocar em Iniciar. Os processos e a fila continuam após a resposta HTTP e independem do navegador. Fechar a página ou perder a internet do celular não interrompe a rodada aceita pelo servidor. Ao reabrir o painel, o estado atual volta por `/api/state`. Em Configurações, Execução contínua mantém o executor esperando por novas tarefas e iniciando novas rodadas depois de alcançar o limite. A consulta à fila vazia aguarda 30 segundos e pode ser interrompida imediatamente. Desative essa opção antes de iniciar para executar apenas uma rodada. Erros da API, limites e verificações pausam a execução, sem repetir tentativas bloqueadas.

O andamento e a chave da API ficam em `executions.json`, protegido com a mesma criptografia do servidor. Ao reiniciar, o servidor valida a sessão salva e retoma execuções que estavam aguardando ou entre tarefas, sem precisar do navegador. Uma ação interrompida durante aceite, execução ou confirmação fica pausada para revisão, sem reenvio automático. O botão Interromper cancela também a retomada futura. O servidor continua precisando de uma conexão própria ativa.


## Verificar esta revisão do login

`GET /api/health` identifica o fluxo `script-web-apk-2fa-v4` e o SHA implantado. Os testes cobrem o worker real com respostas externas simuladas, incluindo usuário/senha → desafio → chave Base32 agrupada → TOTP renovado no envio → sessão conectada, consumo único do desafio e ausência de segredos no disco/estado público. As regressões do APK verificam o identificador nativo em modo automático, troca de contexto entre telas, transição de AP para TOTP/nativo, backup, sessão recebida durante preparação e interrupção sem reenvio após timeout. Não é executado login real durante os testes.


O diagnostico do servidor inclui o transporte privado/publico realmente usado, versoes de dependencias HTTP, presenca de proxy, classificacao da ultima rota e apenas indicadores booleanos da resposta de chave de senha. Mensagens do provedor sao classificadas em categorias fixas; URLs completas, cookies, tokens e valores de chaves nao sao exportados. Isso permite distinguir um 401 em `launcher/sync/` de uma falha no envio do codigo, sem presumir bloqueio de IP.
