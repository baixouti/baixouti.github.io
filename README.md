# Baixou

Curadoria de ofertas para quem trabalha com TI. O bot **vigia preço**, não escreve texto —
e por isso não cai nas políticas de conteúdo em escala do Google.

- **Canal:** [t.me/baixouti](https://t.me/baixouti)
- **Site:** https://baixouti.github.io
- **E-mail do projeto:** baixoutecnologia@gmail.com

---

## Tudo funciona pelo navegador

Você **não precisa instalar nada** nem abrir terminal. Todo comando virou um botão na aba
**Actions** do GitHub. Dá para operar o projeto inteiro do celular.

| Botão na aba Actions | Para que serve |
|---|---|
| **0 · Radar automático** | Roda sozinho a cada 2h. Você só olha se quiser. |
| **1 · Configurar** | Descobre seu chat ID, testa o canal, e diz o que ainda falta |
| **2 · Catálogo** | Monta o catálogo, adiciona ou testa um produto |
| **3 · Ver demonstração** | Mostra o sistema funcionando com dados falsos, hoje |

Para apertar qualquer um: aba **Actions** → clique no nome do botão na lista da esquerda →
**Run workflow** (canto direito) → preencha se tiver campo → **Run workflow** verde.

Depois que rodar, clique na execução e leia o quadro de **Summary** — o resultado aparece
formatado ali, você não precisa caçar nos logs.

---

## Passo 0 — Colocar os arquivos no GitHub

Esta é a única coisa que você faz fora do navegador, e é só descompactar um zip.

1. Baixe o `baixou.zip` e **descompacte** (duplo clique)
2. No GitHub, abra o seu repositório → **Add file** → **Upload files**
3. Arraste **todo o conteúdo** da pasta descompactada para a área de upload
4. Desça a página e clique em **Commit changes**

> Confira que a pasta `.github` subiu junto. Se o seu sistema esconde pastas que começam
> com ponto, ative "mostrar arquivos ocultos" antes de arrastar. Sem ela, os botões não
> aparecem na aba Actions.

---

## Passo 1 — Criar o bot no Telegram (no celular mesmo)

O canal `@baixouti` você já tem. Falta o **bot**: a conta de robô que vai postar nele.

1. No Telegram, procure **@BotFather** e abra a conversa
2. Envie `/newbot`
3. Ele pergunta o nome que aparece na tela → responda `Baixou`
4. Ele pergunta o usuário, que precisa terminar em `bot` → tente `baixou_ofertas_bot`
   (se já existir, ele avisa e você tenta outro)
5. Ele responde com uma linha assim:

   ```
   123456789:AAH8kQm2xPlNvR7tYuIoP1aSdFgHjKlZxCv
   ```

   **Isso é o token** — a senha do seu bot. Copie.

---

## Passo 2 — Guardar o token no GitHub

No repositório: **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**.

Crie estes dois agora:

| Name | Secret |
|---|---|
| `TELEGRAM_TOKEN` | o token que o BotFather te deu |
| `TELEGRAM_CANAL` | `@baixouti` |

Secret é o cofre do GitHub: fica guardado, ninguém vê o conteúdo depois de salvo, e nunca
aparece no código. É o equivalente online do arquivo `.env` — **você não precisa criar
nenhum arquivo `.env`.**

---

## Passo 3 — Descobrir o seu chat ID (botão)

O bot precisa saber o número da conversa entre você e ele, senão não sabe para onde mandar
as ofertas para você aprovar.

1. **No Telegram**, procure o *seu* bot (`@baixou_ofertas_bot`, ou o nome que você criou),
   abra e mande qualquer coisa. Um `oi` resolve.

   > Tem que ser o **seu** bot, não o BotFather. O Telegram não deixa um bot escrever para
   > quem nunca falou com ele antes — por isso o `oi` vem primeiro.

2. **No GitHub**: Actions → **1 · Configurar** → Run workflow →
   escolha `descobrir_meu_chat_id` → Run workflow
3. Quando terminar, clique na execução e leia o **Summary**. Vai estar lá:

   ```
   chat_id=987654321  tipo=private  nome=Rodrigo
   ```

4. Volte em Settings → Secrets → **New repository secret** e crie:

   | Name | Secret |
   |---|---|
   | `TELEGRAM_ADMIN_CHAT_ID` | `987654321` (o número que apareceu) |

---

## Passo 4 — Ligar o bot ao canal

1. No Telegram, abra o canal `@baixouti` → **Administradores** → *Adicionar administrador*
2. Escolha o seu bot
3. Marque a permissão **Publicar mensagens**

Teste: Actions → **1 · Configurar** → `mandar_mensagem_de_teste_no_canal` → Run.
Se a mensagem aparecer no canal, está certo. Pode apagar a mensagem depois.

---

## Passo 5 — Liberar as permissões e o site

Duas configurações no repositório, uma vez só:

1. **Settings** → **Actions** → **General** → role até *Workflow permissions* →
   marque **Read and write permissions** → **Save**

   Sem isso o robô não consegue gravar o histórico de preços e tudo falha.

2. **Settings** → **Pages** → em *Source*, escolha **GitHub Actions**

3. Se o repositório ainda se chama `radar-de-precos`, vale renomear para
   **`baixouti.github.io`** em Settings → *Repository name*. A URL fica limpa
   (`https://baixouti.github.io`) e parece site de verdade na revisão da Amazon, lá no
   Passo 10. O `config.yaml` já aponta para a URL limpa.

---

## Passo 6 — Montar o catálogo (botão)

Actions → **2 · Catálogo** → Run workflow → escolha `montar_em_massa` → Run.

Ele busca os 44 termos de `termos.yaml` no Mercado Livre, filtra o lixo (usado, kit, combo,
preço fora da faixa, título repetido) e grava o `catalogo.csv` **com IDs e preços reais**.
O arquivo é salvo no repositório sozinho.

Os termos foram escolhidos por um critério só: **oscilação de preço**. Um bot de preço só
serve se o preço se mexer. SSD, memória RAM e teclado mecânico oscilam quase toda semana;
produto de preço estável não gera post nenhum, por mais popular que seja.

**Depois de gerar, revise.** Abra o `catalogo.csv` no próprio GitHub, clique no ícone de
lápis (*Edit this file*), apague as linhas de produtos que você não recomendaria para um
amigo, e clique em *Commit changes*. O que sobrar é o seu catálogo — essa revisão é a
diferença entre curadoria e feed automático.

**Para adicionar um produto específico** (é assim que se cadastra Amazon, que não tem busca
em massa): Actions → 2 · Catálogo → `adicionar_uma_url` → cole a URL nos campos → Run.

**Meta:** 30 a 50 produtos agora, 200 a 400 até o fim da segunda semana.

---

## Passo 7 — Conferir se está tudo certo (botão)

Actions → **1 · Configurar** → `diagnostico` → Run.

Ele confere um por um: secrets salvos, token aceito pelo Telegram, bot com acesso ao canal,
catálogo carregando, quanto histórico já existe. O Summary mostra uma lista com `[ok]` no
que está certo e `[X]` no que falta, com a dica de como resolver cada um.

**Sempre que algo não funcionar, rode isto primeiro.**

---

## Passo 8 — Esperar (sério)

O detector exige **60 leituras de cada produto nos últimos 30 dias** antes de opinar — cerca
de 5 dias coletando de 2 em 2 horas. É de propósito: sem histórico não existe "queda de
preço", existe chute.

Nesses dias você não faz nada. O **0 · Radar automático** roda sozinho. Se quiser
acompanhar, entre em Actions e leia o Summary da última rodada.

Quer ver como vai ficar antes disso? Actions → **3 · Ver demonstração** → Run. Ele gera 90
dias de histórico falso, mostra o que o detector encontraria, e deixa o site de exemplo para
baixar no fim da página. Não mexe nos dados reais.

---

## Passo 9 — Aprovar ofertas (no Telegram, do celular)

Quando houver histórico, cada rodada te manda no privado:

```
Teclado mecânico 75% ABNT2
perifericos · MÍNIMA HISTÓRICA

Agora: R$ 333,93
Média 30d: R$ 418,62  (20% abaixo)
Mínima 90d: R$ 333,37
Economia: R$ 84,69
Amostras: 312 leituras

[ Publicar ]  [ Descartar ]
```

**Antes de apertar Publicar, responda a mensagem com uma frase sua.** Algo como
*"é o teclado que eu uso; ABNT2 nessa faixa é raro"*. Essa frase entra no post do canal.
É ela que o canal vende, e é ela que nenhum concorrente automatizado copia.

A publicação sai na rodada seguinte, em até 2 horas. Para sair na hora, aperte
**0 · Radar automático** → Run workflow.

---

## Passo 10 — Os programas de afiliados

### Agora: Mercado Livre e Shopee

Nenhum dos dois tem prazo para a primeira venda, então não custa começar já.

- **Mercado Livre** — https://www.mercadolivre.com.br/l/afiliados-home
  *Quero fazer parte agora* → login → aceitar termos → formulário. Pagamento no Mercado
  Pago em até 60 dias. É a fonte de leitura mais confiável do bot, porque tem API.
- **Shopee** — https://affiliate.shopee.com.br
  Pessoa Física ou Jurídica, redes sociais, telefone, e-mail, e o código de 6 dígitos.
  Aprovação em até 3 dias úteis.

> **Mudança de agosto de 2026:** MEI deixou de ser aceito para quem atua como PJ, passou a
> exigir Simples Nacional, e cada marca que paga comissão extra exige NFS-e separada.
> Resolva a situação fiscal antes de faturar o primeiro real.

### Depois: Amazon, e só depois mesmo

**A Amazon te dá 180 dias para fazer 3 vendas qualificadas. Se não fizer, a inscrição é
cancelada — e não dá para reativar, só reaplicar.**

O relógio começa no dia do cadastro. Com o canal vazio e o site sem produto, ou eles
rejeitam na hora, ou aceitam e você queima semanas do prazo justamente quando ainda não tem
para quem vender. **Cadastre-se só quando** o site tiver páginas com histórico real e o
canal tiver os primeiros membros.

Quando chegar a hora, em https://associados.amazon.com.br:

1. Cadastre **todos** os canais onde os links vão aparecer: o site e o canal do Telegram
2. **O campo "aplicativo móvel" deixe em branco.** Ali é só para app publicado no Google
   Play, App Store ou Amazon Appstore — exige aprovação separada e paga comissão diferente.
   Telegram não é app: vai na lista de sites/canais.
3. Escolha o ID de associado (ex.: `baixou-20`) e salve como o secret `AMAZON_TAG`
4. Preencha dados fiscais e conta bancária

> A regra que mais derruba conta: a Amazon exige canais cadastrados e só permite links em
> e-mail, SMS e mensagem direta quando a comunicação foi *solicitada*. Grupo fechado é
> terreno perigoso. Por isso este projeto manda os links da Amazon para o **site**, e o
> canal aponta para o site.

---

## Como editar as configurações sem terminal

Todo arquivo se edita direto no GitHub: abra o arquivo → ícone de **lápis** → mude →
**Commit changes**.

| Arquivo | O que dá para mudar |
|---|---|
| `catalogo.csv` | Os produtos monitorados. Apagar linha tira do ar. |
| `termos.yaml` | Os termos de busca que o botão 2 usa para montar o catálogo |
| `config.yaml` | As regras do detector, título do site, rodapé dos posts |

---

## Como o detector decide

Uma oferta só vira candidato se **as duas** condições forem verdadeiras:

- **A.** O preço está pelo menos **15% abaixo da média dos últimos 30 dias**
- **B.** O preço está a no máximo **5% acima da mínima dos últimos 90 dias**

A condição B mata o *"de R$ 899 por R$ 499"* que nunca custou R$ 899: se o produto vive
oscilando, a mínima de 90 dias denuncia. Mais: mínimo de 60 amostras, preço mínimo de R$ 25,
e 14 dias de silêncio antes de repetir o mesmo produto.

Tudo isso está em `config.yaml`, editável pelo navegador.

---

## Expansão em fases (para não virar canal genérico)

O nicho é sobre a **pessoa**, não sobre a categoria. As colunas `categoria` e `faixa` do
catálogo existem para você medir o que converte e só então abrir.

- **Mês 1–4** · TI puro. Primeiros 1.000 membros e a primeira receita.
- **Mês 5–8** · adjacências da mesma pessoa: mochila, garrafa térmica, fone bluetooth,
  livro não-técnico, material de estudo. Já está em `termos.yaml`, categoria `estudo`.
- **Mês 9+** · categorias de comissão alta, só as que a fase anterior provou que convertem.

---

## Se der problema

**Rode o `diagnostico` primeiro** (Actions → 1 · Configurar). Ele aponta o que falta.

**Os botões não aparecem na aba Actions.**
A pasta `.github` não subiu no upload. Ela começa com ponto e alguns sistemas escondem.
Ative "mostrar arquivos ocultos" e suba de novo.

**Alguma execução falha com erro no `git push`.**
Settings → Actions → General → **Read and write permissions**.

**`descobrir_meu_chat_id` não mostra número nenhum.**
Você não mandou mensagem para o bot antes, ou mandou para o BotFather em vez do seu bot.

**Muitos produtos com "não foi possível ler".**
A loja monta o preço por JavaScript. Prefira Mercado Livre (API). Teste antes com
Actions → 2 · Catálogo → `so_testar_uma_url`.

**Nenhum candidato aparece há dias.**
Normal. Com regras honestas, a maioria das rodadas não encontra nada. Se depois de duas
semanas continuar zerado, o catálogo é pequeno demais ou tem produtos de preço estável.

**O site dá 404.**
Settings → Pages → Source → **GitHub Actions**, e confira se o Radar automático já rodou
com sucesso pelo menos uma vez.

---

## O que este projeto deliberadamente NÃO faz

- Não gera texto com IA para encher página — é exatamente o que a política de spam do
  Google chama de conteúdo em escala.
- Não publica sem a sua aprovação.
- Não inventa preço "de/por": todo número mostrado foi medido por ele mesmo.
- Não busca produto sozinho. O catálogo é curadoria sua, e é onde está o valor.

---

<details>
<summary>Para quem quiser rodar no computador (opcional)</summary>

Nada aqui é necessário — os botões fazem tudo. Mas se preferir terminal:

```bash
pip install -r requirements.txt
cp .env.example .env        # e preencha os tokens
python -m radar diagnostico
python -m radar rodada
python scripts/testes.py    # 21 verificações
```

| Comando | O que faz |
|---|---|
| `python -m radar coletar` | Lê o preço de todo o catálogo |
| `python -m radar detectar` | Avalia as regras e manda candidatos |
| `python -m radar aprovacoes` | Publica o que você aprovou |
| `python -m radar site` | Gera o site em `site/` |
| `python -m radar adicionar URL [categoria] [faixa]` | Acrescenta um produto |
| `python -m radar testar URL` | Testa a leitura de preço |
| `python -m radar diagnostico` | Confere a configuração |
| `python scripts/montar_catalogo.py` | Monta o catálogo em massa |

</details>
