"""Orquestrador: lê canais -> filtra -> limpa -> manda ao Divulgador."""
import os
import asyncio
import re
import subprocess
import sys
import time

from . import config as C
from . import filtro, inteligencia, links, lojas_extra, ml_vendedor, repetidos, templates, texto as T, vigia, painel, sentinela
from . import cadastro_canais
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
                   avisar_robo=_sem, divulgador=None, ml_extra=None, resolver=None, midia=None):
    chave = "%s:%s" % (canal, post_id)
    if est.ja_fez(chave):
        return "repetido"
    _ant = next((x for x in (est.d.get("retry") or []) if x.get("canal") == canal and x.get("id") == post_id), None)
    if _ant:
        est.d["retry"] = [x for x in est.d["retry"] if x is not _ant]
    if cfg.get("avisar_esgotado", True):
        r = avisar_esgotado(chave, canal, texto, cfg, est, modo, log, avisar_robo)
        if r:
            return r
    limpo = T.limpar(texto, cfg)
    cfg_f = cfg
    if canal in (cfg.get("sacola_liberada_canais") or []):       # ex.: Pincei usa 🛍️ em todo post
        cfg_f = dict(cfg, bloquear_sacola_laranja=False)
    ok, motivo, info = filtro.avaliar(limpo, cfg_f, resolver=resolver)
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
    antigo = inteligencia.preco_antigo(limpo)
    if painel.trava_bloqueia(est, cfg, p, antigo):
        log("  TRAVA %s: preco R$ %s muito abaixo do De R$ %s" % (chave, preco_txt, T.formatar_preco(antigo)))
        if modo != "dry":
            est.marcar(chave, "pulado:trava preco")
            notificar("🛑 Trava de preço: não gerei %s\nPreço lido: R$ %s | De: R$ %s (menos de %d%%). Confira se não é erro de leitura.\nLink: %s\n\nPara desligar a trava: /trava desligar"
                      % (chave, preco_txt, T.formatar_preco(antigo), int(float(cfg.get("trava_preco_pct", 0.3)) * 100), info["link"]))
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
    if cfg.get("copiar_extra", True):
        do_post = T.extra_do_post(limpo)
        if do_post:
            extra = (extra + "\n\n" + do_post) if extra else do_post
            log("  extra do post: " + do_post.replace("\n", " / ")[:120])

    det_cupom = T.cupom_detalhes(limpo) if info["cupom"] else {}
    if info["cupom"] and not T.cupom_vale(p, det_cupom):
        log("  cupom %s NAO usado: compra minima R$ %s e o produto custa R$ %s" % (info["cupom"], det_cupom.get("minimo"), preco_txt))
        info["cupom"] = None
    log("  %s -> %s loja=%s preco=%s cupom=%s" % (chave, link, info["loja"], preco_txt, info["cupom"]))
    if modo == "dry":
        return "dry"          # dry NUNCA consome o post: o modo live ainda poderá processá-lo
    # mesmo produto já saiu hoje (pelo robô ou no seu grupo)? só repete se o preço caiu bastante
    rcfg = cfg.get("repetidos") or {}
    reg = repetidos.registro(limpo, link, info.get("preco"))
    if rcfg.get("ligado", True):
        hist = (est.d.get("postados") or []) + (est.d.get("_grupo_hoje") or [])
        rep, motivo = repetidos.ja_postado(reg, hist, rcfg.get("faixas"), rcfg.get("horas", 24), acima=rcfg.get("acima", repetidos.ACIMA))
        if rep:
            log("  pulado %s (%s)" % (chave, motivo))
            est.marcar(chave, "pulado:repetido")
            return "pulado"
    chave_link = "link:" + link
    ant = est.d.get("feitos", {}).get(chave_link)
    if ant and modo == "live" and time.time() - ant.get("t", 0) < 12 * 3600:   # canal repostou o MESMO produto em 12 h: não tenta de novo
        log("  pulado %s (mesmo link ja tentado: %s)" % (chave, ant.get("r")))
        est.marcar(chave, "pulado:mesmo link")
        return "pulado"
    est.marcar(chave, "em_andamento")   # marca ANTES: se algo travar, nunca posta em duplicidade
    est.salvar()
    # cupom: cadastra/atualiza na página de cupons do Divulgador (desconto, acima de, máximo, código)
    if info["cupom"] and info["loja"] in filtro.LOJAS and modo == "live" and divulgador is not None and getattr(divulgador, "ligado", False) and cfg.get("cupons_no_divulgador", True):
        det = det_cupom
        if det and det.get("minimo"):
            # 21/11: o Divulgador não guarda compra mínima; o piloto automático aplicaria em produto barato
            log("  cupom %s NAO cadastrado no Divulgador (tem compra minima R$ %s)" % (info["cupom"], det["minimo"]))
        elif det:
            try:
                log("  cupom %s no Divulgador: %s" % (info["cupom"], divulgador.salvar_cupom(info["loja"], info["cupom"], det)))
            except Exception as e:  # noqa
                log("  aviso: cupom no Divulgador falhou: %s" % e)
    try:
        dados = {
            "titulo": inteligencia.titulo(limpo),
            "preco_antigo": (T.formatar_preco(inteligencia.preco_antigo(limpo), cent) if inteligencia.preco_antigo(limpo) else None),
            "loja_nome": info["loja"],
            "foto": (lambda: midia(canal, post_id)) if midia else None,
        }
        res, det = driver.gerar(link, preco_txt, info["cupom"], extra, modo=modo, loja=info["loja"], dados=dados)
        if res in ("gerado", "duplicado") and modo == "live":
            est.d["postados"] = repetidos.limpar_historico((est.d.get("postados") or []) + [reg])
    except Exception as e:  # Falha, TimeoutError, limite do Telegram etc.
        log("  FALHA %s: %r" % (chave, e))
        try:
            driver.conv.cancelar_tudo()
        except Exception:  # noqa
            pass
        msg_e = str(e)
        definitiva = bool(re.search(r"VALOR NAO|cupom desconhecido|recusei clicar|nao tem foto|FloodWait", msg_e))
        tents = est.d.setdefault("tentativas", {})
        if len(tents) > 400:
            tents.clear()
        tent = tents.get(chave, 0) + 1
        if modo == "live" and not definitiva and tent < 3:
            # falha passageira (tela velha, bot lento...): NAO perde a promocao, tenta de novo em ~8 min (ate 3 vezes)
            tents[chave] = tent
            est.d["feitos"].pop(chave, None)
            est.d["feitos"].pop(chave_link, None)
            est.d["retry"] = (est.d.get("retry") or []) + [{"canal": canal, "id": post_id, "txt": texto, "t": (_ant or {}).get("t", time.time()), "quando": time.time() + 480, "n": tent}]
            log("  vai tentar de novo %s (tentativa %d de 3) em ~8 min" % (chave, tent + 1))
            est.salvar()
            return "falha"
        notificar("⚠️ Robô de promoções: NAO gerei %s%s\n%s\nLink: %s" % (chave, (" (depois de %d tentativas)" % tent) if tent > 1 else "", e, link))
        est.marcar(chave, "falha")
        est.marcar(chave_link, "falha")
        est.salvar()
        return "falha"
    est.marcar(chave, res)
    est.marcar(chave_link, res)
    if res == "gerado":
        est.somar()
    est.salvar()
    log("  resultado %s: %s" % (chave, det))
    return res


def rodar(cfg, posts_por_canal, est, driver, log=print, notificar=lambda m: None, dormir=time.sleep,
          avisar_robo=_sem, divulgador=None, ml_extra=None, resolver=None, perfil=None, agora=None, midia=None):
    """posts_por_canal: {canal: [(id, texto), ...]} em ordem crescente de id.

    1) avisos de esgotado e posts que não passam no filtro são tratados na hora;
    2) as promoções válidas recebem uma NOTA (inteligencia.nota) e são geradas da maior para a menor,
       no máximo `max_por_canal` de cada canal por execução;
    3) as que sobrarem vão para a FILA e concorrem de novo na próxima execução (até max_idade_min).
    """
    modo = cfg["mode"]
    if modo == "off":
        log("modo OFF: nada a fazer.")
        return {"off": 1}
    agora = agora or time.time()
    lim = cfg["limites"]
    ic = cfg.get("inteligencia") or {}
    cont, feitos, por_canal = {}, 0, {}

    def conta(r):
        cont[r] = cont.get(r, 0) + 1

    def fazer(canal, pid, txt):
        return processar_post(pid, canal, txt, cfg, est, driver, modo, log, notificar,
                              avisar_robo=avisar_robo, divulgador=divulgador, ml_extra=ml_extra, resolver=resolver,
                              midia=midia)

    # fila da execução anterior + posts novos
    itens = []
    max_idade = lim.get("max_idade_min", 90) * 60
    for f in (est.d.get("fila") or []):
        if not max_idade or agora - f.get("t", agora) <= max_idade:
            itens.append((f["canal"], f["id"], f["txt"], f.get("t", agora)))
    for f in (est.d.get("retry") or []):          # promocoes que falharam por motivo passageiro e voltam para nova tentativa
        if agora >= f.get("quando", 0) and (not max_idade or agora - f.get("t", agora) <= max_idade * 2):
            itens.append((f["canal"], f["id"], f["txt"], f.get("t", agora)))
    vistos = {(c, i) for c, i, _, _ in itens}
    for canal, posts in posts_por_canal.items():
        for p in posts:
            pid, txt = p[0], p[1]
            if (canal, pid) not in vistos:
                itens.append((canal, pid, txt, p[2] if len(p) > 2 and p[2] else agora))
            if modo != "dry":
                est.set_ultimo(canal, pid)

    candidatos = []
    for ordem, (canal, pid, txt, t) in enumerate(itens):
        chave = "%s:%s" % (canal, pid)
        if est.ja_fez(chave):
            continue
        limpo = T.limpar(txt, cfg)
        ok = False
        if not (cfg.get("avisar_esgotado", True) and T.cupons_esgotados(txt)):
            ok, _, info = filtro.avaliar(limpo, cfg, resolver=resolver)
        if not ok:
            conta(fazer(canal, pid, txt))            # pulado / esgotado (não usa o bot)
            continue
        if ic.get("ligada", True):
            n, det = inteligencia.nota(limpo, info, perfil, cfg)
        else:
            n, det = 0, "inteligencia desligada"
        if ic.get("ligada", True) and perfil and perfil.get("n") and n < ic.get("nota_minima", 0):
            log("  pulado %s (nota %s < %s: %s)" % (chave, n, ic.get("nota_minima"), det))
            if modo != "dry":
                est.marcar(chave, "pulado:nota")
            conta("pulado")
            continue
        candidatos.append((-n, ordem, canal, pid, txt, t, det))
    candidatos.sort()
    if candidatos and ic.get("ligada", True):
        log("ranking: " + " | ".join("%s:%s=%s" % (c[2], c[3], -c[0]) for c in candidatos[:10]))

    fila = []
    max_canal = lim.get("max_por_canal", 0)
    for neg, _, canal, pid, txt, t, det in candidatos:
        cheio = feitos >= lim["max_por_execucao"] or est.contagem_hoje() >= lim["max_por_dia"]
        if cheio or (max_canal and por_canal.get(canal, 0) >= max_canal):
            fila.append({"canal": canal, "id": pid, "txt": txt, "t": t})
            continue
        if ic.get("ligada", True):
            log("  %s:%s nota %s (%s)" % (canal, pid, -neg, det))
        r = fazer(canal, pid, txt)
        conta(r)
        if r in ("gerado", "cancelado_teste", "duplicado", "falha"):
            feitos += 1
            por_canal[canal] = por_canal.get(canal, 0) + 1
            dormir(lim["pausa_entre_promocoes_seg"])
    if modo != "dry":
        est.d["fila"] = fila[:lim.get("fila_max", 200)]
    if fila:
        log("%d promoções ficaram na fila para a próxima execução" % len(fila))
    return cont


def grupo_hoje(client, cfg, log):
    """O que saiu no SEU grupo nas últimas 24 h (para não repetir o mesmo produto no dia)."""
    grupo = (cfg.get("inteligencia") or {}).get("meu_grupo")
    if not grupo or not (cfg.get("repetidos") or {}).get("ligado", True):
        return []
    try:
        desde = time.time() - 24 * 3600
        out = []
        for m in client.iter_messages(int(grupo), limit=2000):
            if m.date and m.date.timestamp() < desde:
                break
            if m.message:
                p, _, _ = T.preco(m.message)
                out.append(repetidos.registro(m.message, "", p, m.date.timestamp()))
        log("seu grupo: %d posts nas ultimas 24 h" % len(out))
        return out
    except Exception as e:  # noqa
        log("aviso: nao li o seu grupo: %r" % e)
        return []


def atualizar_perfil(client, cfg, est, log):
    """1x por dia: lê os SEUS posts no seu grupo do Telegram e refaz o perfil da inteligência."""
    ic = cfg.get("inteligencia") or {}
    grupo = ic.get("meu_grupo")
    if not ic.get("ligada", True) or not grupo:
        return est.d.get("perfil")
    perfil = est.d.get("perfil")
    if not inteligencia.perfil_vencido(perfil, ic.get("atualizar_horas", 24)):
        return perfil
    try:
        client.get_dialogs(limit=300)            # carrega o grupo no cache da sessão
        desde = time.time() - ic.get("dias_perfil", 7) * 86400
        textos = []
        for m in client.iter_messages(int(grupo), limit=ic.get("max_posts_perfil", 6000)):
            if m.date and m.date.timestamp() < desde:
                break
            if m.message:
                textos.append(m.message)
        novo = inteligencia.montar_perfil(textos)
        if novo["n"] >= 30:
            est.d["perfil"] = novo
            top = list(novo["palavras"])[:15]
            log("perfil atualizado: %d posts seus; mais comuns: %s" % (novo["n"], ", ".join(top)))
            return novo
        log("perfil: poucos posts (%d); mantive o anterior" % novo["n"])
    except Exception as e:  # noqa
        log("aviso: nao consegui montar o perfil: %r" % e)
    return perfil


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



def _verificar_cadastros_canais(posts_por_canal, est, log):
    """v73: manda cada post novo dos canais pro cadastro (Apps Script); se bater, avisa o Oscar
    pelo bot do monitor de precos (NAO cria a promocao sozinho -- so avisa)."""
    for canal, posts in posts_por_canal.items():
        for p in posts:
            pid, txt = p[0], p[1]
            chave = "cad:%s:%s" % (canal, pid)
            if est.ja_fez(chave):
                continue
            try:
                achados = cadastro_canais.verificar(txt, canal, log=log)
                if achados:
                    link = "https://t.me/%s/%s" % (canal, pid)
                    log("  cadastro bateu em %s:%s: %s" % (canal, pid, ", ".join(a.get("nome") or "?" for a in achados)))
                    cadastro_canais.avisar(achados, canal, txt, link, log=log)
            except Exception as e:  # noqa
                log("  aviso: verificacao de cadastro falhou em %s:%s: %r" % (canal, pid, e))
            est.marcar(chave, "cad_verificado")
    return out


def recentes_apenas(msgs, max_idade_min, est, canal, log, agora=None):
    """Posts velhos (ex.: o robô ficou parado) NÃO são postados: só avançam o ponteiro."""
    agora = agora or time.time()
    out = []
    for pid, txt, quando in msgs:
        if max_idade_min and quando and agora - quando > max_idade_min * 60:
            est.set_ultimo(canal, pid)
            continue
        out.append((pid, txt, quando))
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


def usar_vigia(cfg, agora=None):
    """Vigia só no modo live, de dia, e quando não é um teste manual."""
    vc = cfg.get("vigia") or {}
    return bool(vc.get("ligado")) and cfg["mode"] == "live" and not os.environ.get("PROMO_TESTE_LINK", "").strip() \
        and not vigia.noite(cfg, agora)


_VERSAO = {"t": 0.0}


def codigo_novo():
    """True se alguém publicou código/config novo depois que esta execução começou (confere a cada 5 min)."""
    if not os.environ.get("GITHUB_ACTIONS") or time.time() - _VERSAO["t"] < 300:
        return False
    _VERSAO["t"] = time.time()
    r = subprocess.run(["git", "fetch", "-q", "origin", "main"], capture_output=True, text=True, timeout=60)
    if r.returncode:
        return False
    r = subprocess.run(["git", "diff", "--quiet", "HEAD", "FETCH_HEAD", "--", "promo_bot", "config.yaml",
                        "requirements.txt", ".github"], capture_output=True, text=True, timeout=60)
    return r.returncode == 1


def proxima_execucao(log):
    """Pede ao GitHub a próxima execução (fica na fila e começa assim que esta terminar)."""
    tok, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo):
        return False
    import json
    import urllib.request
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/actions/workflows/promo-bot.yml/dispatches" % repo,
        data=json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main"), "inputs": {"mode": "", "link": ""}}).encode(),
        headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
        log("proxima execucao pedida ao GitHub")
        return True
    except Exception as e:  # noqa
        log("aviso: nao pedi a proxima execucao: %r" % e)
        return False


def salvar_no_git(est, log):
    """No GitHub, grava o estado no repositório durante o dia (se o robô cair, não perde o histórico)."""
    est.salvar()
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    try:
        cmds = [["git", "add", "state/estado.json"],
                ["git", "-c", "user.name=promo-bot", "-c", "user.email=promo-bot@users.noreply.github.com",
                 "commit", "-q", "-m", "estado"],
                ["git", "pull", "-q", "--rebase"], ["git", "push", "-q"]]
        for c in cmds:
            r = subprocess.run(c, capture_output=True, text=True, timeout=60)
            if r.returncode and c[1] == "commit":
                return            # nada mudou
    except Exception as e:  # noqa
        log("aviso: nao salvei o estado no git: %r" % e)


def teste_link_env():
    return bool(os.environ.get("PROMO_TESTE_LINK", "").strip())


def main():
    cfg = C.carregar()
    cfg["_manual"] = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    log = lambda s: print(time.strftime("%H:%M:%S"), s, flush=True)
    if cfg["mode"] == "off":
        log("modo OFF (config.yaml / PROMO_MODE). Nada foi feito. Para ligar: mode: dry, test ou live.")
        return 0
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    from .telethon_conv import ConvTelethon, MultiConv, resolver_bots

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
        dormir = lambda seg: client.loop.run_until_complete(asyncio.sleep(seg))   # o vigia continua ouvindo
        sent = None
        if cfg["mode"] == "live" and not teste_link_env():
            try:                       # v74: apaga do grupo e avisa quando o Divulgador aplica cupom abaixo do mínimo
                canais_s = list(dict.fromkeys(list(cfg.get("canais") or []) + list((cfg.get("vigia") or {}).get("canais") or [])))
                sent = sentinela.Sentinela(client, cfg, log, canais_s, memoria=est.d.setdefault("cupom_min", {}))
                sent.ligar()
            except Exception as e:  # noqa
                log("aviso: sentinela nao ligou: %r" % e)
        from .divulgador_api import Divulgador
        teste_link = os.environ.get("PROMO_TESTE_LINK", "").strip()
        def midia(canal, pid):
            """Foto do post original (para o cadastro manual do Divulgador)."""
            try:
                m = client.get_messages(canal, ids=pid)
                return m.photo if m and m.photo else None
            except Exception as e:  # noqa
                log("aviso: nao peguei a foto de %s:%s: %r" % (canal, pid, e))
                return None

        _conv = {}

        def conv_divulgador():
            """UM conjunto de bots do Divulgador por execucao: o rodizio e o 'castigo' dos bots lentos valem entre os ciclos."""
            if "c" not in _conv:
                bots = resolver_bots(client, cfg.get("bots_divulgador") or [cfg["bot_divulgador"]], log)
                if not bots:
                    bots = [cfg["bot_divulgador"]]
                log("bots do Divulgador (rodizio): %s" % ", ".join("@" + x for x in bots))
                convs = [ConvTelethon(client, x) for x in bots]
                _conv["c"] = convs[0] if len(convs) == 1 else MultiConv(convs)
            return _conv["c"]

        def painel_agora():
            painel.ler_comandos(client, est, log, cfg)
            painel.resumo_se_hora(client, est, log)

        def ciclo(posts=None):
            painel_agora()
            templates.encaminhar_manuais(client, cfg, est, log, cfg["mode"])   # @di48291bot -> não vira arte
            if posts is None:
                posts = _ler_canais(client, cfg, est, log)
                _verificar_cadastros_canais(posts, est, log)
            drv = Driver(conv_divulgador(), cfg, log)
            perfil = atualizar_perfil(client, cfg, est, log)
            est.d["_grupo_hoje"] = grupo_hoje(client, cfg, log)
            cont = rodar(cfg, posts, est, drv, log, notificar, dormir=dormir, avisar_robo=avisar_robo,
                         divulgador=Divulgador(), resolver=lojas_extra.resolver, perfil=perfil, midia=midia)
            est.d["ult_ciclo"] = time.time()
            log("resumo: %s" % cont)
            est.salvar()

        post_tg = re.match(r"https?://t\.me/(?:s/)?([A-Za-z0-9_]+)/(\d+)", teste_link or "")
        if teste_link and cfg["mode"] == "test" and post_tg:
            # teste com um post REAL de um canal (texto + foto), ex.: https://t.me/teniscertocupons/63531
            canal_t, pid_t = post_tg.group(1), int(post_tg.group(2))
            m = client.get_messages(canal_t, ids=pid_t)
            est.d.get("feitos", {}).pop("%s:%s" % (canal_t, pid_t), None)
            log("teste com o post %s/%s" % (canal_t, pid_t))
            ciclo({canal_t: [(pid_t, (m.message or "") if m else "")]})
        elif teste_link and cfg["mode"] == "test":
            # teste manual: usa só este link (não lê os canais, não mexe no ponteiro dos canais)
            log("teste com o link informado: %s" % teste_link)
            ciclo({"teste": [(int(time.time()), "Produto de teste por R$ 99,90 no Pix\n" + teste_link)]})
        elif usar_vigia(cfg):
            if vigia.rodar_vigia(client, cfg, log, ciclo, lambda: salvar_no_git(est, log), dormir, painel_agora, codigo_novo) in ("duracao", "atualizar"):
                proxima_execucao(log)          # não depende do agendamento do GitHub (que atrasa)
        elif vigia.noite(cfg) and not cfg.get("_manual"):
            intervalo = float((cfg.get("vigia") or {}).get("noite_ciclo_min", 30)) * 60
            if time.time() - float(est.d.get("ult_ciclo") or 0) < intervalo - 90:
                log("madrugada: ciclo a cada %d min; ainda nao e hora." % (intervalo / 60))
            else:
                ciclo()
            falta = vigia.segundos_para_dia(cfg)
            if cfg["mode"] == "live" and (cfg.get("vigia") or {}).get("ligado") and 0 < falta <= 40 * 60:
                # perto das 07:00: esta execução espera e já liga o vigia (o agendamento do GitHub atrasa)
                log("madrugada acabando: espero %d min e ligo o vigia" % (falta // 60 + 1))
                dormir(falta + 5)
                if vigia.rodar_vigia(client, cfg, log, ciclo, lambda: salvar_no_git(est, log), dormir, painel_agora, codigo_novo) in ("duracao", "atualizar"):
                    proxima_execucao(log)
        else:
            ciclo()
        if sent:
            sent.desligar()
    est.salvar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
