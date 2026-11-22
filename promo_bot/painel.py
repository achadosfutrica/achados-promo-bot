"""Painel pelas suas Mensagens Salvas do Telegram (21/11).

Comandos (digite nas Mensagens Salvas, a conversa consigo mesmo):
  /trava desligar   /trava ligar    -> trava de preço (não gera se o preço ficar muito abaixo do "De")
  /resumo desligar  /resumo ligar   -> resumo diário das 23h
  /robo status                      -> mostra como está
  /robo auditar [horas]             -> confere os posts do seu grupo (padrão 72 h) e lista os suspeitos
O robô responde ali mesmo com ✅.
"""
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
CHAT = "me"


def flags(est):
    f = est.d.setdefault("flags", {})
    f.setdefault("trava", True)
    f.setdefault("resumo", True)
    return f


def trava_bloqueia(est, cfg, preco, antigo):
    """True se o preço está abaixo de X% do preço 'De' (provável erro de leitura)."""
    if not flags(est).get("trava") or not preco or not antigo:
        return False
    pct = float(cfg.get("trava_preco_pct", 0.3))
    return preco < antigo * pct


def _texto_cmd(txt):
    p = (txt or "").strip().lower().split()
    if len(p) >= 2 and p[0] in ("/trava", "/resumo", "/robo"):
        if p[1] == "auditar":
            return p[0][1:], "auditar:" + (p[2] if len(p) > 2 and p[2].isdigit() else "72")
        return p[0][1:], p[1]
    return None


def _lojas_nome():
    from .texto import _LOJAS_NOME
    return _LOJAS_NOME


def suspeitas_do_post(txt, pct=0.3):
    """Lista os problemas de um post já publicado (preço muito abaixo do De, cupom que é nome de loja)."""
    import re
    from . import texto as T, inteligencia as I
    prob = []
    p = T.preco(txt or "")[0]
    antigo = I.preco_antigo(txt or "")
    if p and antigo and p < antigo * pct:
        prob.append("preço R$ %s vs De R$ %s" % (T.formatar_preco(p), T.formatar_preco(antigo)))
    m = re.search(r"cupom\W{0,3}\s*[:\-]?\s*`?([A-Za-z0-9]{3,25})", txt or "", re.I)
    if m and m.group(1).upper() in _lojas_nome():
        prob.append("cupom \"%s\" (nome de loja)" % m.group(1).upper())
    return prob


def auditar(client, cfg, horas=72, log=print):
    grupo = (cfg.get("inteligencia") or {}).get("meu_grupo")
    if not grupo:
        return "⚠️ Grupo não configurado (inteligencia.meu_grupo)."
    desde = time.time() - horas * 3600
    achados, n = [], 0
    for m in client.iter_messages(int(grupo), limit=5000):
        if m.date and m.date.timestamp() < desde:
            break
        if not m.message:
            continue
        n += 1
        pr = suspeitas_do_post(m.message, float(cfg.get("trava_preco_pct", 0.3)))
        if pr:
            tit = (m.message.strip().split("\n")[0])[:60]
            cid = str(grupo).replace("-100", "", 1)
            achados.append("• %s — %s\n  %s\n  https://t.me/c/%s/%s" % (
                datetime.fromtimestamp(m.date.timestamp(), BRT).strftime("%d/%m %H:%M"), tit, "; ".join(pr), cid, m.id))
    log("auditoria: %d posts, %d suspeitos" % (n, len(achados)))
    cab = "🔎 Auditoria do grupo (últimas %d h): %d posts, %d suspeitos" % (horas, n, len(achados))
    return cab + ("\n\n" + "\n".join(achados[:40]) if achados else "\n✅ Nenhum preço/cupom suspeito.")


def aplicar_comando(est, nome, acao):
    """Muda o estado e devolve a resposta (ou None se não for comando)."""
    f = flags(est)
    if nome in ("trava", "resumo") and acao in ("ligar", "desligar"):
        f[nome] = acao == "ligar"
        rot = {"trava": "Trava de preço", "resumo": "Resumo diário das 23h"}[nome]
        return "✅ %s %s." % (rot, "LIGADA(O)" if f[nome] else "DESLIGADA(O)")
    if nome == "robo" and acao == "status":
        return "🤖 Robô de promoções\nTrava de preço: %s\nResumo das 23h: %s\n\nComandos: /trava ligar|desligar · /resumo ligar|desligar · /robo auditar" % (
            "ligada" if f["trava"] else "desligada", "ligado" if f["resumo"] else "desligado")
    return None


def ler_comandos(client, est, log, cfg=None):
    """Lê os comandos novos das Mensagens Salvas e responde."""
    cfg = cfg or {}
    try:
        ult = int(est.d.get("cmd_ultimo") or 0)
        msgs = client.get_messages(CHAT, limit=15, min_id=ult) if ult else client.get_messages(CHAT, limit=1)
        if not ult:                          # 1ª vez: só marca o ponto de partida
            est.d["cmd_ultimo"] = msgs[0].id if msgs else 1
            return
        for m in sorted([x for x in msgs if x], key=lambda x: x.id):
            est.d["cmd_ultimo"] = max(int(est.d.get("cmd_ultimo") or 0), m.id)
            c = _texto_cmd(m.message)
            if not c:
                continue
            if c[1].startswith("auditar:"):
                resp = auditar(client, cfg, int(c[1].split(":")[1]), log)
            else:
                resp = aplicar_comando(est, *c)
            if resp:
                log("comando: /%s %s" % c)
                r = client.send_message(CHAT, resp)
                est.d["cmd_ultimo"] = max(est.d["cmd_ultimo"], getattr(r, "id", 0))
    except Exception as e:  # noqa
        log("aviso: comandos: %r" % e)


def montar_resumo(est, agora=None):
    agora = agora or time.time()
    rec = {k: v for k, v in (est.d.get("feitos") or {}).items()
           if not k.startswith("link:") and agora - v.get("t", 0) < 24 * 3600}
    cont = Counter(v["r"].split(":")[0] for v in rec.values())
    motivos = Counter(v["r"].split(":", 1)[1].split("(")[0].strip() for v in rec.values() if v["r"].startswith("pulado:"))
    falhas = [k for k, v in rec.items() if v["r"] == "falha"]
    trav = [k for k, v in rec.items() if v["r"].startswith("pulado:trava")]
    linhas = ["📊 Resumo do robô (últimas 24 h)",
              "✅ Geradas: %d" % cont.get("gerado", 0),
              "🔁 Já existiam no Divulgador: %d" % cont.get("duplicado", 0),
              "⏭️ Puladas: %d" % cont.get("pulado", 0)]
    for mtv, n in motivos.most_common(6):
        linhas.append("   • %s: %d" % (mtv, n))
    linhas.append("❌ Falhas: %d%s" % (len(falhas), (" (" + ", ".join(falhas[:8]) + ")") if falhas else ""))
    if trav:
        linhas.append("🛑 Travadas por preço: " + ", ".join(trav[:8]))
    linhas.append("\nPara desligar este resumo: /resumo desligar")
    return "\n".join(linhas)


def resumo_se_hora(client, est, log, agora=None):
    """Manda 1 resumo por dia, a partir das 23h."""
    agora = agora or time.time()
    t = datetime.fromtimestamp(agora, BRT)
    if t.hour < 23 or not flags(est).get("resumo"):
        return False
    dia = t.strftime("%Y-%m-%d")
    if est.d.get("resumo_dia") == dia:
        return False
    try:
        client.send_message(CHAT, montar_resumo(est, agora))
        est.d["resumo_dia"] = dia
        log("resumo diario enviado")
        return True
    except Exception as e:  # noqa
        log("aviso: resumo: %r" % e)
        return False
