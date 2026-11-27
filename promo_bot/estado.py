"""Estado persistente (dedupe) em JSON, versionado no repositório."""
import json
import os
import time


class Estado:
    def __init__(self, caminho="state/estado.json"):
        self.caminho = caminho
        self.d = {"ultimo_id": {}, "feitos": {}, "dia": "", "contagem_dia": 0}
        if os.path.exists(caminho):
            with open(caminho, encoding="utf-8") as f:
                self.d.update(json.load(f))

    def hoje(self):
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 3600))

    def contagem_hoje(self):
        if self.d["dia"] != self.hoje():
            self.d["dia"], self.d["contagem_dia"] = self.hoje(), 0
        return self.d["contagem_dia"]

    def somar(self):
        self.contagem_hoje()
        self.d["contagem_dia"] += 1

    def ultimo(self, canal):
        return self.d["ultimo_id"].get(canal)

    def set_ultimo(self, canal, i):
        self.d["ultimo_id"][canal] = max(i, self.d["ultimo_id"].get(canal, 0))

    def ja_fez(self, chave):
        return chave in self.d["feitos"]

    def marcar(self, chave, resultado):
        self.d["feitos"][chave] = {"r": resultado, "t": int(time.time())}
        if len(self.d["feitos"]) > 1500:  # poda os mais antigos
            for k, _ in sorted(self.d["feitos"].items(), key=lambda x: x[1]["t"])[:300]:
                del self.d["feitos"][k]

    def salvar(self):
        os.makedirs(os.path.dirname(self.caminho) or ".", exist_ok=True)
        # 27/11: grava num arquivo temporario e troca de uma vez (se a execucao cair no meio, o estado nao corrompe)
        dados = json.dumps({k: v for k, v in list(self.d.items()) if not k.startswith("_")}, ensure_ascii=False, indent=1, sort_keys=True)
        tmp = self.caminho + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(dados)
        os.replace(tmp, self.caminho)
