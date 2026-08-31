# Transione — Bot Discord

Bot de atendimento e solicitação de viagens construído com Python e `discord.py`.

## Configuração

1. Crie uma aplicação no [Discord Developer Portal](https://discord.com/developers/applications).
2. Crie o bot e adicione o token como Secret no Replit com o nome `DISCORD_TOKEN`.
3. Ative o **Developer Mode** do Discord, copie o ID numérico de `@nikukier` e
   adicione-o como Secret do Replit com o nome `NIKUKIER_USER_ID`.
4. Ao convidar o bot para o servidor, habilite os escopos `bot` e `applications.commands`.
5. Nas permissões, permita pelo menos:
   - Enviar mensagens
   - Incorporar links
   - Usar comandos de aplicação
   - Ler histórico de mensagens
6. Garanta que `nikukier` esteja no mesmo servidor do bot e permita mensagens diretas.

O bot usa `NIKUKIER_USER_ID` para localizar o destinatário e validar os botões
administrativos; não depende apenas do nome de usuário e não precisa de
intents privilegiados.

## Uso

- Execute o workflow do projeto.
- No servidor, use `/painel` para publicar a mensagem principal da Transione.
- `Ver Catálogo` mostra os serviços disponíveis.
- `Solicitar Viagem` abre o formulário com links Discord de origem e destino, data e horário, carga e ID opcional do destinatário.
- Os links precisam seguir o formato `https://discord.com/channels/...` e aparecem como botões clicáveis de localização nos embeds.
- Sem ID de destinatário, a encomenda é destinada ao próprio solicitante; com ID, o bot valida e salva o usuário informado.
- Cada solicitação é encaminhada por DM para o usuário configurado em `NIKUKIER_USER_ID` com um número `TRN-0001`, `TRN-0002` etc.
- O fluxo dos pedidos é `AGUARDANDO → ACEITA → RECEBIDA → EM ANDAMENTO → ENTREGUE`.
- `RECUSADA` só pode ocorrer enquanto o pedido está `AGUARDANDO`.
- Ao marcar como `ENTREGUE`, o destinatário recebe uma mensagem privada com o link do destino para retirada.
- Os pedidos ficam salvos em `orders.json`, para que a numeração e os botões sejam restaurados após reinicializações.

O token nunca é armazenado no código; o bot lê exclusivamente `DISCORD_TOKEN` do ambiente.

## GitHub e Render

O projeto inclui `requirements.txt`, `.python-version` e `render.yaml` para
instalação e execução como **Web Service** no Render. O processo continua
executando o bot Discord e também inicia um servidor HTTP mínimo em
`0.0.0.0:$PORT`; a rota `/` responde `Transione está online` para o health
check do Render.

### Publicar no GitHub

Crie o repositório `adri4nacarvalho-gif/transione-discord-bot` no GitHub e,
na pasta do projeto, execute:

```bash
git init
git add .
git commit -m "Preparar Transione para GitHub e Render"
git branch -M main
git remote add origin https://github.com/adri4nacarvalho-gif/transione-discord-bot.git
git push -u origin main
```

Antes do `git add`, confirme que arquivos locais sensíveis continuam ignorados:

```bash
git status --short --ignored
```

`orders.json`, arquivos `.env` e arquivos de credenciais não devem aparecer
como arquivos rastreados no GitHub. O `orders.json` local não é removido por
esta preparação.

### Publicar no Render

1. Entre em [render.com](https://render.com/) e escolha **New → Blueprint**.
2. Conecte sua conta do GitHub e selecione
   `adri4nacarvalho-gif/transione-discord-bot`.
3. Selecione o arquivo `render.yaml` na raiz e confirme a criação do serviço.
4. No ambiente do serviço, preencha os valores de `DISCORD_TOKEN` e
   `NIKUKIER_USER_ID` em **Environment**. Os valores não devem ser colocados no
   GitHub nem no `render.yaml`.
5. Faça o deploy e confira os logs. O serviço deve iniciar com `python bot.py`
   e mostrar que o bot conectou ao Discord.

O Render trata o `render.yaml` como um Web Service contínuo. O arquivo
`orders.json` é estado local e continua ignorado pelo Git; portanto, os pedidos
existentes permanecem no Replit, mas não são enviados ao GitHub. Para manter
pedidos também após recriações do serviço no Render, será necessário configurar
armazenamento persistente no Render ou migrar essa persistência para um banco,
o que não foi alterado nesta preparação.