"""Saude do robo (27/11): detecta quando a execucao TRAVA e se corrige sozinha.

Causa real do travamento de 27/11 (execucao #208): o loop do Telegram ficou ~20 min sem rodar enquanto o robo
abria links do Pechinchou no navegador (~42 s cada, um atras do outro). O Telegram descarta mensagens com mais
de 5 min ("Server sent a very old message ... Security error ... Too many messages had to be ignored") e a
conexao fica quebrada: os bots do Divulgador param de responder para o robo.

Duas defesas aqui:
1) pulso(): o robo marca que esta vivo (log, espera, leitura de tela). Se ficar mais de LIMITE_MIN sem pulso,
   uma linha de execucao separada pede a proxima execucao ao GitHub e encerra esta (conexao nova, sem travar).
2) contador de erros de seguranca do Telethon: se vierem muitos em pouco tempo, faz o mesmo reinicio.
"""
import logging
import os
import threading
import time

LIMITE_MIN = 15          # sem nenhum sinal de vida por 15 min = travado
ERROS_MAX = 15           # 15 erros de seguranca do Telethon...
ERROS_JANELA = 180       # ...em 3 min = conexao quebrada

_ESTADO = {"pulso": time.time(), "erros": [], "parado": False}


def pulso():
    _ESTADO["pulso"] = time.time()


def ultimo_pulso():
    return _ESTADO["pulso"]


class _ContaErros(logging.Handler):
    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # noqa
            return
        if "very old message" in msg or "Too many messages had to be ignored" in msg or "Security error while unpacking" in msg:
            agora = time.time()
            _ESTADO["erros"] = [t for t in _ESTADO["erros"] if agora - t < ERROS_JANELA] + [agora]


def conexao_quebrada(agora=None):
    agora = agora or time.time()
    return len([t for t in _ESTADO["erros"] if agora - t < ERROS_JANELA]) >= ERROS_MAX


def travado(agora=None, limite_min=LIMITE_MIN):
    agora = agora or time.time()
    return agora - _ESTADO["pulso"] > limite_min * 60


def vigiar(log, reiniciar, salvar=None, intervalo=30, limite_min=LIMITE_MIN, sair=os._exit):
    """Liga a vigilancia numa linha de execucao propria (nao depende do loop do Telegram, que pode estar preso)."""
    logging.getLogger("telethon").addHandler(_ContaErros())
    pulso()

    def laco():
        while not _ESTADO["parado"]:
            time.sleep(intervalo)
            motivo = None
            if conexao_quebrada():
                motivo = "conexao com o Telegram quebrada (%d erros de seguranca em %d s)" % (len(_ESTADO["erros"]), ERROS_JANELA)
            elif travado(limite_min=limite_min):
                motivo = "sem sinal de vida ha %d min" % ((time.time() - _ESTADO["pulso"]) // 60)
            if not motivo:
                continue
            try:
                log("AUTOCORRECAO: %s -> peco uma execucao nova e encerro esta" % motivo)
            except Exception:  # noqa
                pass
            try:
                if salvar:
                    salvar()
            except Exception:  # noqa
                pass
            try:
                reiniciar()
            except Exception:  # noqa
                pass
            sair(0)          # sai "ok": a execucao nova ja foi pedida
            return

    t = threading.Thread(target=laco, name="saude", daemon=True)
    t.start()
    return t


def parar():
    _ESTADO["parado"] = True
