"""Orquestrador: lê canais -> filtra -> limpa -> manda ao Divulgador."""
import os
import sys
import time

from . import config as C
from . import filtro, links, ml_vendedor, texto as T
from .driver import Driver, Falha
from .estado import Estado


def _sem(*_a, **_k):
    return None


def avisar_esgotado(chave, canal, texto, cfg, est, modo, log, avisar_robo):
    """Post de 'cupom esgotado' no canal -> manda /cupomacabou ao robô de cupons (ele pergunta se posta)."""
    cods = T.cupons_esgotados(texto)
    if not cods:
        return None
    log("  %s: aviso de ESGOTADO %s" % (chave, ", ".join(cods)))
    if modo != "live":
        return "esgotado_" + modo
    novos = [c for c in cods if not est.ja_fez("esg:" + c)]
    if novos:
        avisar_robo("/cupomacabou %s | %s" % (" ".join(novos), canal))
        for c in novos:
            est.marcar("esg:" + c, "avisado")
    est.marcar(chave, "esgotado_avisado")
    est.salvar()
    return "esgotado_avisado"


def processar_post(post_id, canal, texto, cfg, est, driver, modo, log, notificar,
                   avisar_robo=_sem, divulgador=None, ml_extra=None):
    chave = "%s:%s" % (canal, post_id)
    if est.ja_fez(chave):
        return "repetido"
    if cfg.get("avisar_esgotado", True):
        r = avisar_esgotado(chave, canal, texto, cfg, est, modo, log, avisar_robo)
        if r:
            return r
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
    extra = None
    if info["loja"] == "mercadolivre" and cfg.get("ml_vendedor_extra", True):
        try:
            extra = (ml_extra or ml_vendedor.extra)(info["link"], limpo, log=log)
        except Exception as e:  # noqa
            log("  ML vendedor: %r" % e)
        if extra:
            log("  extra: " + extra.splitlines()[0])

    log("  %s -> %s loja=%s preco=%s cupom=%s" % (chave, link, info["loja"], preco_txt, info["cupom"]))
    if modo == "dry":
        return "dry"          # dry NUNCA consome o post: o modo live ainda poderá processá-lo
    est.marcar(chave, "em_andamento")   # marca ANTES: se algo travar, nunca posta em duplicidade
    est.salvar()
    # cupom: cadastra/atualiza na página de cupons do Divulgador (desconto, acima de, máximo, código)
    if info["cupom"] and modo == "live" and divulgador is not None and getattr(divulgador, "ligado", False) and cfg.get("cupons_no_divulgador", True):
        det = T.cupom_detalhes(limpo)
        if det:
            try:
                log("  cupom %s no Divulgador: %s" % (info["cupom"], divulgador.salvar_cupom(info["loja"], info["cupom"], det)))
            except Exception as e:  # noqa
                log("  aviso: cupom no Divulgador falhou: %s" % e)
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


def rodar(cfg, posts_por_canal, est, driver, log=print, notificar=lambda m: None, dormir=time.sleep,
          avisar_robo=_sem, divulgador=None, ml_extra=None):
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
            r = processar_post(pid, canal, txt, cfg, est, driver, modo, log, notificar,
                               avisar_robo=avisar_robo, divulgador=divulgador, ml_extra=ml_extra)
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
            todos = client.loop.run_until_complete(_buscar(client, canal, ultimo, 100, com_data=True))
            out[canal] = recentes_apenas(todos, cfg["limites"].get("max_idade_min", 90), est, canal, log)
    return out


def recentes_apenas(msgs, max_idade_min, est, canal, log, agora=None):
    """Posts velhos (ex.: o robô ficou parado) NÃO são postados: só avançam o ponteiro."""
    agora = agora or time.time()
    out = []
    for pid, txt, quando in msgs:
        if max_idade_min and quando and agora - quando > max_idade_min * 60:
            est.set_ultimo(canal, pid)
            continue
        out.append((pid, txt))
    if len(out) < len(msgs):
        log("canal %s: %d posts antigos (mais de %s min) ignorados" % (canal, len(msgs) - len(out), max_idade_min))
    return out


async def _buscar(client, canal, min_id, limite, com_data=False):
    msgs = []
    async for m in client.iter_messages(canal, limit=limite, min_id=min_id or 0):
        if m.message:
            if com_data:
                msgs.append((m.id, m.message, m.date.timestamp() if m.date else None))
            else:
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
        def avisar_robo(msg):
            try:
                client.send_message(cfg["robo_cupons"], msg)   # sai pela sua conta, como se você digitasse
                log("  avisado %s: %s" % (cfg["robo_cupons"], msg))
            except Exception as e:  # noqa
                log("aviso: nao consegui avisar o robo de cupons: %s" % e)
                notificar("⚠️ Robô de promoções: não consegui avisar @%s:\n%s" % (cfg["robo_cupons"], msg))
        from .divulgador_api import Divulgador
        posts = _ler_canais(client, cfg, est, log)
        drv = Driver(ConvTelethon(client, cfg["bot_divulgador"]), cfg, log)
        cont = rodar(cfg, posts, est, drv, log, notificar, avisar_robo=avisar_robo, divulgador=Divulgador())
        log("resumo: %s" % cont)
    est.salvar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
