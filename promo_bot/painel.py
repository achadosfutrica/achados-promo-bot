"""Painel pelas suas Mensagens Salvas do Telegram (21/11).

Comandos (digite nas Mensagens Salvas, a conversa consigo mesmo):
  /trava desligar   /trava ligar    -> trava de preço (não gera se o preço ficar muito abaixo do "De")
  /resumo desligar  /resumo ligar   -> resumo diário das 23h
  /robo status                      -> mostra como está
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
        return p[0][1:], p[1]
    return None


def aplicar_comando(est, nome, acao):
    """Muda o estado e devolve a resposta (ou None se não for comando)."""
    f = flags(est)
    if nome in ("trava", "resumo") and acao in ("ligar", "desligar"):
        f[nome] = acao == "ligar"
        rot = {"trava": "Trava de preço", "resumo": "Resumo diário das 23h"}[nome]
        return "✅ %s %s." % (rot, "LIGADA(O)" if f[nome] else "DESLIGADA(O)")
    if nome == "robo" and acao == "status":
        return "🤖 Robô de promoções\nTrava de preço: %s\nResumo das 23h: %s\n\nComandos: /trava ligar|desligar · /resumo ligar|desligar" % (
            "ligada" if f["trava"] else "desligada", "ligado" if f["resumo"] else "desligado")
    return None


def ler_comandos(client, est, log):
    """Lê os comandos novos das Mensagens Salvas e responde."""
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
