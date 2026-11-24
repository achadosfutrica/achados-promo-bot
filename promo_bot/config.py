import os
import yaml

PADRAO = {
    "mode": "off",
    "canais": ["fumotom", "promotom", "juaocupons"],
    "bot_divulgador": "di48291bot",
    "bots_divulgador": [],
    "robo_cupons": "AchadosFutricaBot",
    "lojas": {"amazon": True, "mercadolivre": False, "shopee": False, "magalu": False, "aliexpress": False},
    "avisar_esgotado": True,
    "cupons_no_divulgador": True,
    "ml_vendedor_extra": True,
    "copiar_extra": True,
    "plataforma": {"_padrao": ["awin", "rakuten", "afilio"]},
    "inteligencia": {"ligada": False},
    "bonus_loja": {},
    "vigia": {"ligado": False, "canais": [], "comando": "/canais ja", "espera_seg": 15, "apagar_seg": 45,
              "min_entre_seg": 20, "poll_seg": 20, "duracao_min": 330, "ciclo_min": 10,
              "noite_inicio": "00:30", "noite_fim": "07:00", "noite_ciclo_min": 30},
    "templates": {"ligado": True, "bot_manual": "di48291bot", "robo_templates": "achadosfutricatemplatesbot"},
    "repetidos": {"ligado": True, "horas": 24, "desconto_min": 0.15},
    "bloquear_sacola_laranja": True,
    "sacola_liberada_canais": [],
    "trava_preco_pct": 0.3,
    "bloquear_texto": [],
    "preco": {"centavos_quando_inteiro": "00", "suspeito_abaixo_de": 10, "politica_suspeito": "publicar"},
    "limites": {"max_por_execucao": 8, "pausa_entre_promocoes_seg": 25, "max_por_dia": 150, "primeira_vez_ler_ultimos": 0, "max_idade_min": 90},
    "bloquear": [], "liberar": [],
    "remover_linhas_que_comecam": ["-anuncio", "anuncio"],
    "remover_trechos": [],
}


def _mescla(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _mescla(out[k], v)
        else:
            out[k] = v
    return out


def carregar(caminho=None):
    caminho = caminho or os.environ.get("PROMO_CONFIG", "config.yaml")
    with open(caminho, encoding="utf-8") as f:
        cfg = _mescla(PADRAO, yaml.safe_load(f) or {})
    # a variável de ambiente PROMO_MODE (do GitHub) pode forçar o modo
    forcado = os.environ.get("PROMO_MODE", "").strip().lower()
    if forcado in ("off", "dry", "test", "live"):
        cfg["mode"] = forcado
    if cfg["mode"] not in ("off", "dry", "test", "live"):
        raise ValueError("mode inválido: %r" % cfg["mode"])
    return cfg
