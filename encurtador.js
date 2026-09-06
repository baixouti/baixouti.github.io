/**
 * Encurtador com contagem de cliques - Cloudflare Worker (plano gratuito).
 * Opcional, mas voce precisa medir clique para saber o que cortar.
 *
 * Como subir:
 *   1. https://dash.cloudflare.com -> Workers & Pages -> Create -> Worker
 *   2. Cole este arquivo, Deploy
 *   3. Workers & Pages -> seu worker -> Settings -> Bindings -> Add -> KV namespace
 *      Variable name: LINKS      (crie o namespace em Storage & Databases -> KV)
 *   4. No KV, adicione uma chave por SKU. Exemplo:
 *        chave:  EXEMPLO_AMZ_01
 *        valor:  https://www.amazon.com.br/dp/B00XXXX?tag=seutag-20
 *   5. Ponha a URL do worker em ENCURTADOR_BASE (ex.: https://ir.seudominio.workers.dev)
 *
 * Uso: https://SEU-WORKER/ir/EXEMPLO_AMZ_01  -> conta o clique e redireciona
 *      https://SEU-WORKER/stats              -> quantos cliques por SKU
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/stats") {
      const lista = await env.LINKS.list({ prefix: "cliques:" });
      const linhas = await Promise.all(
        lista.keys.map(async (k) => [
          k.name.replace("cliques:", ""),
          Number(await env.LINKS.get(k.name)) || 0,
        ])
      );
      linhas.sort((a, b) => b[1] - a[1]);
      return new Response(JSON.stringify(Object.fromEntries(linhas), null, 2), {
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }

    const achado = url.pathname.match(/^\/ir\/([A-Za-z0-9_-]+)\/?$/);
    if (!achado) return new Response("Nao encontrado", { status: 404 });

    const sku = achado[1];
    const destino = await env.LINKS.get(sku);
    if (!destino) return new Response("Link nao cadastrado", { status: 404 });

    // Conta o clique sem segurar o redirecionamento.
    const contar = (async () => {
      const chave = `cliques:${sku}`;
      const atual = Number(await env.LINKS.get(chave)) || 0;
      await env.LINKS.put(chave, String(atual + 1));
    })();
    if (typeof request.waitUntil === "function") request.waitUntil(contar);

    return Response.redirect(destino, 302);
  },
};
