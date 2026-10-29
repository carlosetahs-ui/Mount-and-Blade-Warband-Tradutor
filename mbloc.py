"""Leitura/escrita dos arquivos de idioma do Mount & Blade: Warband
e proteção dos códigos do jogo ({s0}, {reg1}, {playername}, ^ ...).

Formato dos arquivos em Modules/<Mod>/languages/<lang>/*.csv:
    id|texto
Exemplo:
    dlga_start:close_window|Hello {playername}, welcome to {s1}.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Códigos do jogo que nunca podem ser alterados.
#  {s0} {reg12} {playername} {s1?a:b} {sir/madam}
#  ^  (quebra de linha no Warband)
#  %d %s (raros, em alguns mods)
MARKER_RE = re.compile(r"\{[^{}]*\}|\^|%[0-9]*[ds](?:%%)?|<c=[^>]*>|</c>|\\n")

# {reg1?plural:singular} / {s3?texto:outro}  -> ramos traduzíveis
COND_RE = re.compile(r"^\{(!?(?:reg|s)\d+|playername)\?([^{}:?]*):([^{}:?]*)\}$")
# {sir/madam} {his/her}  -> escolha por gênero, ramos traduzíveis
SLASH_RE = re.compile(r"^\{([A-Za-z][^{}/?:]*)/([^{}/?:]*)\}$")

TOKEN_FMT = "⟦{}⟧"
TOKEN_RE = re.compile(r"⟦(\d+)⟧")


@dataclass
class Entry:
    key: str | None          # None = linha que não é id|texto (mantida igual)
    text: str
    raw: str


@dataclass
class LocFile:
    path: Path
    entries: list[Entry] = field(default_factory=list)
    bom: bool = False


def read_csv(path: Path) -> LocFile:
    data = path.read_bytes()
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    lf = LocFile(path=path, bom=bom)
    for raw in text.splitlines():
        if "|" in raw and not raw.lstrip().startswith("#"):
            key, _, val = raw.partition("|")
            lf.entries.append(Entry(key.strip(), val, raw))
        else:
            lf.entries.append(Entry(None, raw, raw))
    return lf


def write_csv(path: Path, entries: list[tuple[str | None, str]], bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, val in entries:
        lines.append(val if key is None else f"{key}|{val}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8-sig" if bom else "utf-8")
    tmp.replace(path)  # escrita atômica: nunca deixa arquivo pela metade


def markers(text: str) -> list[str]:
    return MARKER_RE.findall(text)


def mask(text: str, glossary: dict[str, str] | None = None):
    """Troca códigos do jogo (e termos do glossário) por ⟦N⟧.
    Retorna (texto_mascarado, lista_de_substitutos)."""
    subs: list[str] = []

    def put(value: str) -> str:
        subs.append(value)
        return TOKEN_FMT.format(len(subs) - 1)

    out = MARKER_RE.sub(lambda m: put(m.group(0)), text)
    if glossary:
        for src in sorted(glossary, key=len, reverse=True):
            pat = re.compile(r"(?<![\w⟦])" + re.escape(src) + r"(?![\w⟧])")
            out = pat.sub(lambda m, s=src: put(glossary[s]), out)
    return out, subs


def unmask(text: str, subs: list[str]) -> str:
    return TOKEN_RE.sub(lambda m: subs[int(m.group(1))] if int(m.group(1)) < len(subs) else m.group(0), text)


def tokens_ok(masked_src: str, translated: str) -> bool:
    """Plano B: o tradutor manteve exatamente os mesmos ⟦N⟧ (sem apagar/duplicar)?"""
    return sorted(TOKEN_RE.findall(masked_src)) == sorted(TOKEN_RE.findall(translated))


def split_segments(masked: str) -> list[tuple[bool, str]]:
    """Plano C: separa texto e ⟦N⟧. (True, texto) = traduzível."""
    parts, pos = [], 0
    for m in TOKEN_RE.finditer(masked):
        if m.start() > pos:
            parts.append((True, masked[pos:m.start()]))
        parts.append((False, m.group(0)))
        pos = m.end()
    if pos < len(masked):
        parts.append((True, masked[pos:]))
    return parts


def translatable_branches(marker: str):
    """Para {s1?a:b} e {sir/madam}: devolve (formato, [ramos]) ou None."""
    m = COND_RE.match(marker)
    if m:
        return ("{%s?%%s:%%s}" % m.group(1)), [m.group(2), m.group(3)]
    m = SLASH_RE.match(marker)
    if m and not re.fullmatch(r"(reg|s)\d+", m.group(1)):
        return "{%s/%s}", [m.group(1), m.group(2)]
    return None


def has_letters(s: str) -> bool:
    return bool(re.search(r"[A-Za-zÀ-ÿ]", s))


def clean_output(s: str) -> str:
    """O Warband quebra se houver | ou quebra de linha real no texto."""
    s = s.replace("\r", " ").replace("\n", " ").replace("|", "/")
    return re.sub(r"[ \t]{2,}", " ", s).strip()
