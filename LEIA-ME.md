# Ramos Bot · Mobile

Painel React com o visual roxo neon da referência e backend Python real. O login do Instagram e o código de verificação são informados na própria página.

## Testar no Windows

1. Baixe o projeto em **Code → Download ZIP** e extraia a pasta.
2. Instale Python 3.11+ e Node.js 22+ se necessário.
3. Abra **INICIAR-RAMOS-BOT.cmd**.
4. Acesse **http://127.0.0.1:8765**. Escolha a sessão e conecte sua conta.

A primeira execução instala as dependências. O ZIP de entrega também inclui a interface compilada. Nenhum login pessoal, senha, código ou token do GitHub faz parte do projeto.

## Fluxo do Instagram

- Criar, selecionar, pesquisar e remover sessões.
- Conectar com usuário/senha, importar um `login.txt` escolhido por você ou reutilizar uma sessão salva.
- 2FA por aplicativo autenticador, usando o contexto recebido na mesma tentativa de login. SMS/e-mail são apresentados quando o Instagram solicita um código de desafio.
- No desafio do autenticador, aceite um código de 6 dígitos ou uma chave Base32 com ou sem espaços. Ao terminar de digitar/colar uma chave válida, o navegador gera o TOTP e envia o código após uma breve pausa. A chave não é enviada ao backend nem salva. O horário acompanha o servidor; se o código estiver prestes a expirar, aguarda a próxima janela. SMS/e-mail continuam aceitando somente códigos numéricos. Não há reenvio automático após uma falha.
- A opção de aprovação por outro aparelho foi removida. Quando o servidor não fornece um contexto de código compatível, a tentativa é encerrada e seu diagnóstico pode ser exportado. Isso não contorna verificações obrigatórias do Instagram.
- `buscar "nome" --limite 10`, `buscar "nome" --geral`, `perfil usuario`, `perfil --id 123456`, `seguir usuario`, `seguir --id 123456`, `curtir URL_OU_ID`.
- Macros iniciadas pelo usuário, interrupção, proxy, logs e exportação do último resultado em JSON.
- Diagnóstico exportável sem senhas ou tokens. Escritas e códigos não são reenviados automaticamente.

O núcleo usa o `instagram_login_apk.py` fornecido, com `instagrapi==3.2.19`. Uma sessão só aparece como conectada depois de ser validada pelo servidor do Instagram. Os testes usam processos e dados sintéticos; não houve login em uma conta real durante esta entrega.

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
