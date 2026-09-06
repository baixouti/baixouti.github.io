# Radar de Preços

Bot de curadoria de ofertas com link de afiliado. Ele **vigia preço**, não escreve texto —
e por isso não cai nas políticas de conteúdo em escala do Google.

O fluxo é sempre o mesmo:

```
coleta a cada 2h  →  detecta queda real  →  VOCÊ aprova no Telegram  →  publica no canal
                              ↓
                    site com histórico de preço (o ativo durável)
```

O bot **nunca publica sozinho**. Ele te manda o candidato no privado com dois botões.
Essa camada humana custa 10 minutos por dia e é exatamente o que separa isto de um
agregador que o Google apaga.

---

## Veja funcionando agora (3 minutos, sem cadastro nenhum)

```bash
pip install -r requirements.txt
python scripts/simular.py
```

Isso gera 90 dias de histórico falso, roda o detector e monta o site em `site-simulado/`.
Abra `site-simulado/index.html` no navegador. É assim que vai ficar com dados reais.

Para conferir que tudo está sadio depois de qualquer mudança:

```bash
python scripts/testes.py
```

---

## Passo 1 — Cadastros nos programas de afiliados

Faça os três. Nunca dependa de um só: conta de afiliado é encerrada por violação de
política sem aviso e sem recurso prático.

### 1.1 Amazon Associados

1. Entre em **https://associados.amazon.com.br** → *Inscreva-se*
2. Cadastre **todos** os canais onde os links vão aparecer. Cadastre desde já:
   - o site (`https://SEU_USUARIO.github.io/radar-de-precos`)
   - o canal público do Telegram
3. Escolha o **ID de associado** (ex.: `radarprecos-20`). É ele que vai em `AMAZON_TAG`.
4. Preencha dados fiscais e conta bancária.

> **Atenção, e isto derruba muita gente:** a Amazon exige canais cadastrados e só
> permite links em e-mail, SMS e mensagem direta quando a comunicação foi *solicitada*.
> Grupo fechado é terreno perigoso. Por isso este projeto foi desenhado para que o
> **site** seja o destino dos links da Amazon e o canal aponte para o site.
> Leia as regras em https://associados.amazon.com.br/help/operating/policies/
>
> A **PA-API** (API oficial de produtos) só é liberada depois de 3 vendas qualificadas
> em 180 dias. Você não precisa dela: este bot lê o preço da própria página do produto.

### 1.2 Mercado Livre

1. Entre em **https://www.mercadolivre.com.br/l/afiliados-home**
2. Clique em *Quero fazer parte agora*, faça login e aceite os termos
3. Preencha o formulário e aguarde o e-mail de confirmação
4. Comissões variam por categoria; pagamento cai no Mercado Pago em até 60 dias

Vantagem técnica: o Mercado Livre tem **API pública**, então a leitura de preço dele é a
mais confiável do projeto. Prefira links do ML sempre que o produto existir lá.

### 1.3 Shopee

1. Entre em **https://affiliate.shopee.com.br**
2. Escolha Pessoa Física ou Jurídica, informe redes sociais, telefone e e-mail
3. Confirme o código de 6 dígitos que chega por e-mail
4. A aprovação leva até 3 dias úteis

> **Mudança de agosto de 2026:** MEI deixou de ser aceito para quem atua como PJ,
> passou a exigir Simples Nacional, e cada marca que paga comissão extra exige NFS-e
> separada. **Resolva a situação fiscal antes de faturar o primeiro real**, não depois.

---

## Passo 2 — Criar o bot no Telegram

1. No Telegram, abra a conversa com **@BotFather**
2. Envie `/newbot`
3. Escolha um nome visível (ex.: `Radar de Preços`)
4. Escolha um usuário terminando em `bot` (ex.: `radar_precos_bot`)
5. O BotFather devolve um **token** parecido com
   `123456789:AAH8xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` → esse é o `TELEGRAM_TOKEN`
6. Ainda no BotFather, envie `/setprivacy` → escolha seu bot → **Disable**
   (necessário para o bot enxergar as suas respostas de texto)

Agora descubra o seu chat pessoal:

```bash
cp .env.example .env          # cole o token no .env
# mande QUALQUER mensagem para o seu bot no Telegram (um "oi" serve)
python -m radar chatid
```

Ele imprime algo como `chat_id=123456789 tipo=private`. Esse número é o
`TELEGRAM_ADMIN_CHAT_ID` — é para lá que os candidatos vão.

---

## Passo 3 — Criar o canal público

1. Telegram → menu → **Novo canal**
2. Nome e descrição. Marque como **Público** e escolha o link (ex.: `t.me/radardeprecos`)
3. Canal → *Administradores* → **Adicionar administrador** → escolha o seu bot
4. Dê a permissão **Publicar mensagens**
5. Em `.env`, `TELEGRAM_CANAL=@radardeprecos` (com arroba, exatamente como no link)

Canal **público** é importante: a Amazon aceita canal aberto cadastrado; grupo fechado não.

---

## Passo 4 — Subir no GitHub

1. **https://github.com/new** → nome `radar-de-precos` → **Public** → *Create repository*
2. No seu computador:

```bash
git init
git add .
git commit -m "primeira versão do radar"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/radar-de-precos.git
git push -u origin main
```

3. **Secrets** — no repositório: *Settings* → *Secrets and variables* → *Actions* →
   **New repository secret**. Crie um por um:

   | Nome | Valor |
   |---|---|
   | `TELEGRAM_TOKEN` | o token do BotFather |
   | `TELEGRAM_ADMIN_CHAT_ID` | o número do `python -m radar chatid` |
   | `TELEGRAM_CANAL` | `@seucanal` |
   | `AMAZON_TAG` | seu ID de associado, ex. `radarprecos-20` |
   | `ENCURTADOR_BASE` | deixe vazio por enquanto |

4. **Pages** — *Settings* → *Pages* → em *Source* escolha **GitHub Actions**
5. **Permissões** — *Settings* → *Actions* → *General* → em *Workflow permissions*
   marque **Read and write permissions** (o robô precisa gravar o histórico de preços)
6. Edite `config.yaml`: troque `SEU_USUARIO` em `base_url` e ajuste título e subtítulo

A partir daí o workflow roda sozinho a cada 2 horas. Para disparar na hora:
aba **Actions** → *Radar de Preços* → **Run workflow**.

---

## Passo 5 — Montar o catálogo

Abra `catalogo.csv`, apague as linhas de exemplo e ponha as suas.

```csv
sku,nome,url,fonte,categoria,faixa,ativo
LIVRO_CLEANARCH,Clean Architecture,https://produto.mercadolivre.com.br/MLB-1234567890-x,mercadolivre,livros,baixa,sim
TECLADO_KEYCHRON,Teclado Keychron K2,https://www.amazon.com.br/dp/B07XXXXXXX,generico,home office,media,sim
```

Regras que valem a pena seguir:

- **URL limpa.** Sem `?tag=`, sem `utm_`, sem `ref=`. O bot põe a tag na hora de publicar.
- **Um SKU nunca se repete.** O programa recusa o catálogo se houver repetido.
- **Comece com 30 a 50 produtos que você mesmo compraria.** Chegue a 200–400 até o fim
  da semana 2. Esse catálogo é a sua barreira de entrada — é o que ninguém copia numa tarde.
- **Prefira Mercado Livre** quando o produto existir nos dois: a leitura é por API.

Antes de adicionar, teste se o preço é legível:

```bash
python -m radar testar "https://www.amazon.com.br/dp/B07XXXXXXX"
```

Se aparecer `preco=...`, pode cadastrar. Se disser que não conseguiu ler, a loja monta o
preço por JavaScript — procure o mesmo produto em outra loja.

---

## Passo 6 — A primeira semana em modo silencioso

O detector precisa de **60 leituras nos últimos 30 dias** para ter opinião — cerca de
5 dias coletando de 2 em 2 horas. Isso é de propósito: sem histórico não existe
"queda de preço", existe chute.

Nesses primeiros dias, rode só a coleta e confira:

```bash
python -m radar coletar
python -m radar resumo
```

Se algum SKU falha sempre, tire do catálogo. Melhor 200 produtos que leem bem do que
400 com metade quebrada.

---

## Passo 7 — Ligar as publicações

Quando houver histórico, cada rodada te manda no privado os candidatos assim:

```
Teclado mecânico 75% ABNT2
home office · MÍNIMA HISTÓRICA

Agora: R$ 333,93
Média 30d: R$ 418,62  (20% abaixo)
Mínima 90d: R$ 333,37
Economia: R$ 84,69
Amostras: 312 leituras

[ Publicar ]  [ Descartar ]
```

**Antes de apertar Publicar, responda a mensagem com uma frase sua.** Algo como
*"esse é o teclado que eu uso; o layout ABNT2 nessa faixa é raro"*. Essa frase entra no
post do canal. É ela que o canal vende, e é ela que nenhum concorrente automatizado copia.

A publicação acontece na rodada seguinte (até 2 horas). Para publicar na hora, dispare o
workflow na aba **Actions**, ou rode localmente:

```bash
python -m radar aprovacoes
```

---

## Opcional — Encurtador com contagem de cliques

Sem medir clique você não tem como saber o que cortar. `worker/encurtador.js` é um
Cloudflare Worker (plano gratuito) com as instruções de instalação no topo do arquivo.
Depois de subir, preencha o secret `ENCURTADOR_BASE`.

---

## Comandos

| Comando | O que faz |
|---|---|
| `python -m radar coletar` | Lê o preço de todo o catálogo e grava no histórico |
| `python -m radar detectar` | Avalia as regras e manda candidatos para aprovação |
| `python -m radar aprovacoes` | Publica no canal o que você aprovou |
| `python -m radar site` | Gera o site estático em `site/` |
| `python -m radar rodada` | Tudo acima, na ordem certa |
| `python -m radar testar URL` | Testa a leitura de preço de uma página |
| `python -m radar chatid` | Descobre o seu `TELEGRAM_ADMIN_CHAT_ID` |
| `python -m radar resumo` | Estado do catálogo, do histórico e dos alertas |

---

## Como o detector decide

Uma oferta só vira candidato se **as duas** condições forem verdadeiras:

- **A.** O preço está pelo menos **15% abaixo da média dos últimos 30 dias**
- **B.** O preço está a no máximo **5% acima da mínima dos últimos 90 dias**

A condição B é a que mata o *"de R$ 899 por R$ 499"* que nunca custou R$ 899: se o
produto vive oscilando, a mínima de 90 dias denuncia. Além disso: mínimo de 60 amostras,
preço mínimo de R$ 25 e 14 dias de silêncio antes de repetir o mesmo produto.

Tudo isso fica em `config.yaml`. Mexa lá, não no código.

---

## Problemas comuns

**"não foi possível ler" em muitos produtos.**
A loja monta o preço por JavaScript. Prefira Mercado Livre (API) ou lojas que publicam
`schema.org/Product` na página. Teste antes de cadastrar com `python -m radar testar`.

**O workflow falha no `git push`.**
*Settings* → *Actions* → *General* → *Workflow permissions* → **Read and write permissions**.

**O bot não vê as minhas respostas de texto.**
Envie `/setprivacy` ao @BotFather, escolha o bot e marque **Disable**.

**Os botões não fazem nada.**
Eles só são processados quando `python -m radar aprovacoes` roda — na próxima rodada de
2 horas, ou disparando o workflow na mão.

**Nenhum candidato aparece há dias.**
Normal. Com regras honestas, a maioria das rodadas não encontra nada. Se depois de duas
semanas continuar zerado, o catálogo é pequeno demais ou tem produtos de preço estável.

---

## O que este projeto deliberadamente NÃO faz

- Não gera texto com IA para encher página — é exatamente isso que a política de spam
  do Google chama de conteúdo em escala.
- Não publica sem a sua aprovação.
- Não inventa preço "de/por": todo número mostrado foi medido por ele mesmo.
- Não busca produto sozinho. O catálogo é curadoria sua, e é onde está o valor.
