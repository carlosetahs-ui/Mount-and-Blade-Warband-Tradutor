"""revisao.py - pós-edição do texto traduzido (roda sobre o texto com marcadores ⟦N⟧, então códigos do jogo,
termos do glossário nunca são tocados).

1. Troca formas de português de Portugal por português do Brasil (os serviços online misturam as duas):
   vocabulário (partilhar->compartilhar, equipa->equipe, facto->fato...), grafias antigas (acção->ação),
   "está a fazer" -> "está fazendo" e pronome depois do verbo ("impressionam-me" -> "me impressionam").
2. Aplica as correções do arquivo de substituições configurado ("errado = certo", uma por linha), se existir.
"""
from __future__ import annotations

import re
from typing import Callable

# --- vocabulário: palavra inteira, sem diferenciar maiúsculas/minúsculas, preserva a capitalização
PT_PARA_BR = {
    "partilhar": "compartilhar", "partilhando": "compartilhando", "partilhado": "compartilhado",
    "partilhada": "compartilhada", "partilham": "compartilham", "partilhou": "compartilhou",
    "partilhamos": "compartilhamos", "partilhados": "compartilhados", "partilhadas": "compartilhadas",
    "ecrã": "tela", "ecrãs": "telas", "equipa": "equipe", "equipas": "equipes",
    "facto": "fato", "factos": "fatos", "contacto": "contato", "contactos": "contatos",
    "rapariga": "garota", "raparigas": "garotas", "miúdo": "garoto", "miúdos": "garotos",
    "miúda": "garota", "miúdas": "garotas", "autocarro": "ônibus", "comboio": "trem",
    "desporto": "esporte", "desportos": "esportes",
    # grafias pré-acordo (Portugal) comuns em traduções automáticas
    "acção": "ação", "acções": "ações", "direcção": "direção", "direcções": "direções",
    "protecção": "proteção", "reacção": "reação", "reacções": "reações", "correcção": "correção",
    "actual": "atual", "actuais": "atuais", "actualmente": "atualmente", "actividade": "atividade",
    "actividades": "atividades", "actor": "ator", "actores": "atores", "óptimo": "ótimo",
    "óptima": "ótima", "objectivo": "objetivo", "objectivos": "objetivos", "projecto": "projeto",
    "projectos": "projetos", "arquitectura": "arquitetura", "espectáculo": "espetáculo",
    "espectáculos": "espetáculos", "adopção": "adoção", "afectar": "afetar", "afecta": "afeta",
    "afectado": "afetado", "efectivo": "efetivo", "efectivamente": "efetivamente",
    "exacto": "exato", "exactamente": "exatamente", "directo": "direto",
    "directa": "direta", "directamente": "diretamente", "director": "diretor", "colectivo": "coletivo",
    "colecção": "coleção", "infecção": "infecção", "perspectiva": "perspectiva",
}
# Só trocamos o que difere: remove entradas idênticas para não gastar tempo.
PT_PARA_BR = {k: v for k, v in PT_PARA_BR.items() if k != v}

_ESTAR = r"(?:está|estão|estava|estavam|estou|estamos|estás|estive|estiveram|estarão|estará|estaria|estariam)"
_IRREG = {"ver": "vendo", "ser": "sendo", "ter": "tendo", "ler": "lendo", "vir": "vindo", "pôr": "pondo",
          "ir": "indo", "dar": "dando", "crer": "crendo", "rir": "rindo", "pôr-se": "pondo-se"}
_GERUND = re.compile(rf"\b({_ESTAR})\s+a\s+([a-záàâãéêíóôõúç]+?)(ar|er|ir)\b", re.I)
# verbo (3ª pessoa do plural / pretérito perfeito) + pronome, só no MEIO da frase (no início o jeito de Portugal também é correto no Brasil): "impressionam-me" -> "me impressionam"
_ENCLIT = re.compile(r"\b([A-Za-zÀ-ÿ]+(?:am|ou|aram|eram|iram|eu|iu|ão))-(me|te|nos|lhe|lhes)\b")


def _case_like(found: str, repl: str) -> str:
    if len(found) > 1 and found.isupper():
        return repl.upper()
    if found[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _word_map(pairs: dict[str, str]) -> Callable[[str], str]:
    if not pairs:
        return lambda s: s
    low = {k.lower(): v for k, v in pairs.items()}
    rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(low, key=len, reverse=True)) + r")(?!\w)", re.I)
    return lambda s: rx.sub(lambda m: _case_like(m.group(0), low[m.group(0).lower()]), s)


def _gerund(m: re.Match) -> str:
    estar, stem, end = m.group(1), m.group(2), m.group(3)
    inf = stem + end
    if inf.lower() in _IRREG:
        g = _IRREG[inf.lower()]
    elif len(stem) < 2:
        return m.group(0)                                # "está a mar/lar/par": é substantivo, não verbo
    else:
        g = stem + {"ar": "ando", "er": "endo", "ir": "indo"}[end.lower()]
    return f"{estar} {_case_like(inf, g) if inf[:1].isupper() else g}"


def ptbr(text: str) -> str:
    """Só o vocabulário e a gramática de Portugal -> Brasil (sem arquivo do usuário)."""
    text = _GERUND.sub(_gerund, text)

    def enclit(m: re.Match) -> str:
        antes = text[:m.start()].rstrip()
        if not antes or antes[-1] in '.!?:"\u201c\u2014':                 # começo de frase: não mexe
            return m.group(0)
        return f"{m.group(2)} {m.group(1)}"
    text = _ENCLIT.sub(enclit, text)
    return _BASE(text)


_GENERO = _word_map({"o ecrã": "a tela", "do ecrã": "da tela", "no ecrã": "na tela", "ao ecrã": "à tela",
                     "um ecrã": "uma tela", "os ecrãs": "as telas", "dos ecrãs": "das telas", "nos ecrãs": "nas telas"})
_WORDS = _word_map(PT_PARA_BR)


def _BASE(text: str) -> str:
    return _WORDS(_GENERO(text))


def build(ptbr_fix: bool = True, replacements: dict[str, str] | None = None) -> Callable[[str], str] | None:
    """Monta a função de pós-edição (ou None se nada está ativo). As substituições do usuário rodam primeiro."""
    user = _word_map(replacements or {})
    if not ptbr_fix and not replacements:
        return None

    def post(text: str) -> str:
        text = user(text) if replacements else text
        return ptbr(text) if ptbr_fix else text
    return post


def load_replacements(path) -> dict[str, str]:
    """Carrega substituições de um arquivo (uma por linha, formato 'errado = certo' ou 'errado\tcerto')."""
    repl: dict[str, str] = {}
    if not path or not path.exists():
        return repl
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if "\t" in line:
            a, b = line.split("\t", 1)
        elif "=" in line:
            a, b = line.split("=", 1)
        else:
            continue
        repl[a.strip()] = b.strip()
    return repl
