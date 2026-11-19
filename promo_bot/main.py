"""Orquestrador: lê canais -> filtra -> limpa -> manda ao Divulgador."""
import os
import sys
import time

from . import config as C
from . import filtro, links, texto as T
from .driver import Driver, Falha
from .estado import Estado


def processar_post(post_id, canal, texto, cfg, est, driver, modo, log, notificar):
    chave = "%s:%s" % (canal, post_id)
    if est.ja_fez(chave):
        return "repetido"
    limpo = T.limpar(texto, cfg)
    ok, motivo, info = filtro.avaliar(limpo, cfg)
    if not ok:
        log("  pulado %s (%s)" % (chave, motivo))
        if modo != "dry":
            est.marcar(chave, "pulado:" + motivo)
        return "pulado"

    cent = cfg["preco"]["centavos_quando_inteiro"]
    p = info["preco"]
    preco_txt = T.formatar_preco(p, cent)
    if p < cfg["preco"]["suspeito_abaixo_de"]:
        log("  aviso: preco suspeito R$ %s (bug?)" % preco_txt)
        if cfg["preco"]["politica_suspeito"] == "pular":
            if modo != "dry":
                est.marcar(chave, "pulado:suspeito")
            return "pulado"
    link = links.preparar(info["link"], info["loja"])
    extra = None  # reservado: infos extras do post (ex.: "frete grátis") — ver extras.py no futuro

    log("  %s -> %s loja=%s preco=%s cupom=%s" % (chave, link, info["loja"], preco_txt, info["cupom"]))
    if modo == "dry":
        return "dry"          # dry NUNCA consome o post: o modo live ainda poderá processá-lo
    est.marcar(chave, "em_andamento")   # marca ANTES: se algo travar, nunca posta em duplicidade
    est.salvar()
    try:
        res, det = driver.gerar(link, preco_txt, info["cupom"], extra, modo=modo)
    except Exception as e:  # Falha, TimeoutError, limite do Telegram etc.
        log("  FALHA %s: %r" % (chave, e))
        try:
            driver.conv.cancelar_tudo()
        except Exception:  # noqa
            pass
        notificar("⚠️ Robô de promoções: falha em %s\n%s\nLink: %s" % (chave, e, link))
        est.marcar(chave, "falha")
        est.salvar()
        return "falha"
    est.marcar(chave, res)
    if res == "gerado":
        est.somar()
    est.salvar()
    log("  resultado %s: %s" % (chave, det))
    return res


def rodar(cfg, posts_por_canal, est, driver, log=print, notificar=lambda m: None, dormir=time.sleep):
    """posts_por_canal: {canal: [(id, texto), ...]} em ordem crescente de id."""
    modo = cfg["mode"]
    if modo == "off":
        log("modo OFF: nada a fazer.")
        return {"off": 1}
    lim = cfg["limites"]
    cont, feitos = {}, 0
    for canal, posts in posts_por_canal.items():
        for pid, txt in posts:
            if feitos >= lim["max_por_execucao"] or est.contagem_hoje() >= lim["max_por_dia"]:
                log("limite atingido; o resto fica para a próxima execução")
                return cont
            r = processar_post(pid, canal, txt, cfg, est, driver, modo, log, notificar)
            if modo != "dry":
                est.set_ultimo(canal, pid)
            cont[r] = cont.get(r, 0) + 1
            if r in ("gerado", "cancelado_teste", "duplicado", "falha"):
                feitos += 1
                dormir(lim["pausa_entre_promocoes_seg"])
    return cont


def _ler_canais(client, cfg, est, log):
    out = {}
    for canal in cfg["canais"]:
        ultimo = est.ultimo(canal)
        if ultimo is None:
            recentes = client.loop.run_until_complete(_buscar(client, canal, None, max(cfg["limites"]["primeira_vez_ler_ultimos"], 1)))
            n = cfg["limites"]["primeira_vez_ler_ultimos"]
            base = recentes[-1][0] if recentes else 0
            out[canal] = recentes[-n:] if n else []
            if not n and recentes:
                est.set_ultimo(canal, base)
            log("canal %s: primeira leitura (base id=%s)" % (canal, base))
        else:
            out[canal] = client.loop.run_until_complete(_buscar(client, canal, ultimo, 100))
    return out


async def _buscar(client, canal, min_id, limite):
    msgs = []
    async for m in client.iter_messages(canal, limit=limite, min_id=min_id or 0):
        if m.message:
            msgs.append((m.id, m.message))
    return sorted(msgs)


def main():
    cfg = C.carregar()
    log = lambda s: print(time.strftime("%H:%M:%S"), s, flush=True)
    if cfg["mode"] == "off":
        log("modo OFF (config.yaml / PROMO_MODE). Nada foi feito. Para ligar: mode: dry, test ou live.")
        return 0
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    from .telethon_conv import ConvTelethon

    api_id, api_hash, sess = os.environ["TG_API_ID"], os.environ["TG_API_HASH"], os.environ["TG_SESSION"]
    est = Estado(os.environ.get("PROMO_STATE", "state/estado.json"))
    with TelegramClient(StringSession(sess), int(api_id), api_hash) as client:
        def notificar(msg):
            try:
                client.send_message("me", msg)  # Mensagens Salvas do seu Telegram
            except Exception as e:  # noqa
                log("aviso: nao consegui notificar: %s" % e)
        posts = _ler_canais(client, cfg, est, log)
        drv = Driver(ConvTelethon(client, cfg["bot_divulgador"]), cfg, log)
        cont = rodar(cfg, posts, est, drv, log, notificar)
        log("resumo: %s" % cont)
    est.salvar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
