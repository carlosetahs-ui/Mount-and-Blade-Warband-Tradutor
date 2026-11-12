"""Leitura/escrita dos arquivos de idioma do Mount & Blade: Warband
e proteção dos códigos do jogo ({s0}, {reg1}, {playername}, ^ ...).

Formato dos arquivos em Modules/<Mod>/languages/<lang>/*.csv:
    id|texto
Exemplo:
    dlga_start:close_window|Hello {playername}, welcome to {s1}.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Formatos printf: argumento, flags, largura, precisão, tamanho e conversão.
# %d%% é reconhecido como dois códigos, preservando também o percentual.
PRINTF_RE = re.compile(
    r"%%|%(?:[1-9]\d*\$)?[-+#0 ']*"
    r"(?:\d+|\*(?:[1-9]\d*\$)?)?"
    r"(?:\.(?:\d*|\*(?:[1-9]\d*\$)?))?"
    r"(?:hh|ll|I32|I64|[hljztL])?[diuoxXfFeEgGaAcCsSpn]"
)
_MARKER_START_RE = re.compile(r"[{}]|\^|" + PRINTF_RE.pattern + r"|<c=[^>]*>|</c>|\\n")
_BRACE_RE = re.compile(r"[{}]")

# {reg1?plural:singular} / {s3?texto:outro}  -> ramos traduzíveis
COND_RE = re.compile(r"^\{(!?(?:reg|s)\d+|playername)\?([^{}:?]*):([^{}:?]*)\}$")
# {sir/madam} {his/her}  -> escolha por gênero, ramos traduzíveis
SLASH_RE = re.compile(r"^\{([^\W\d_][^{}/?:]*)/([^{}/?:]*)\}$")

STYLES = {
    "math": ("⟦{}⟧", r"⟦\s*(\d+)\s*⟧"),   # padrão: translators e Ollama
    "xnx":  ("X{}X", r"X\s*(\d+)\s*X"),    # Argos
}
TOKEN_FMT, _pat = STYLES["math"]
TOKEN_RE = re.compile(_pat)
LITERAL_TOKEN_RE = re.compile(r"⟦\s*\d+\s*⟧|X\s*\d+\s*X")


def set_token_style(name: str) -> None:
    """Troca o formato do marcador (chamado uma vez, no início do programa)."""
    global TOKEN_FMT, TOKEN_RE
    TOKEN_FMT, pat = STYLES[name]
    TOKEN_RE = re.compile(pat)


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
    if b"\x00" in data or data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError(f"{path}: contém NUL ou codificação UTF-16/UTF-32; use UTF-8 ou Windows-1252.")
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    lf = LocFile(path=path, bom=bom)
    # Só \n separa linhas. splitlines() também corta em \x0b, \x0c, \x85 e
    # \u2028, que podem aparecer dentro de um texto do mod.
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # a quebra final do arquivo não é uma linha extra
    for raw in lines:
        if "|" in raw and not raw.lstrip().startswith("#"):
            key, _, val = raw.partition("|")
            lf.entries.append(Entry(key, val, raw))
        else:
            lf.entries.append(Entry(None, raw, raw))
    return lf


def write_csv(path: Path, entries: list[tuple[str | None, str]], bom: bool = False,
              *, source: LocFile | None = None) -> None:
    # Valida antes de criar o temporário: nenhuma etapa pode substituir IDs,
    # reordenar linhas ou injetar novas entradas no arquivo do jogo.
    if source is not None:
        if len(entries) != len(source.entries):
            raise ValueError("a saída alterou a quantidade de linhas do original")
        for (key, val), original in zip(entries, source.entries):
            if key != original.key or (key is None and val != original.raw):
                raise ValueError("a saída alterou IDs, ordem ou comentários do original")
            if key is not None and val != original.text:
                problems = check_pair(original.text, val)
                if problems:
                    raise ValueError(f"{key}: " + "; ".join(problems))
    if source is None:
        seen = set()
        for key, val in entries:
            if key is None:
                if "|" in val and not val.lstrip().startswith("#"):
                    raise ValueError("linha sem ID contém uma entrada disfarçada")
            else:
                if not key.strip() or key in seen:
                    raise ValueError("ID vazio ou repetido na saída")
                if key.lstrip().startswith("#"):
                    raise ValueError("ID não pode iniciar um comentário")
                seen.add(key)
                if "|" in val:
                    raise ValueError("texto contém separador extra sem origem para validar")
    for key, val in entries:
        if key is not None and ("|" in key or "\r" in key or "\n" in key):
            raise ValueError("ID contém separador ou quebra de linha")
        if "\r" in val or "\n" in val:
            raise ValueError("linha contém quebra de linha real")
        if "\x00" in val or (key is not None and "\x00" in key):
            raise ValueError("linha contém NUL")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, val in entries:
        lines.append(val if key is None else f"{key}|{val}")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False, newline="\n",
                                         encoding="utf-8-sig" if bom else "utf-8") as fh:
            tmp = Path(fh.name)
            fh.write("\n".join(lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)  # arquivo temporário exclusivo, no mesmo volume
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def marker_spans(text: str):
    """Percorre códigos sem limitar a profundidade das chaves nem usar recursão."""
    pos = 0
    while match := _MARKER_START_RE.search(text, pos):
        start, end = match.span()
        if (match.group(0).startswith("%") and " " in match.group(0)
                and end < len(text) and text[end].isalpha()):
            # Em "50% chance", o começo de "chance" não é um argumento % c.
            pos = start + 1
            continue
        if match.group(0) == "{":
            depth = 1
            end = len(text)
            for brace in _BRACE_RE.finditer(text, match.end()):
                depth += 1 if brace.group(0) == "{" else -1
                if depth == 0:
                    end = brace.end()
                    break
            # Uma abertura incompleta protege o restante da linha, em vez
            # de enviar a expressão parcialmente exposta ao motor.
        yield start, end
        pos = end


def markers(text: str) -> list[str]:
    return [text[start:end] for start, end in marker_spans(text)]


def strip_markers(text: str) -> str:
    pieces, pos = [], 0
    for start, end in marker_spans(text):
        pieces.append(text[pos:start])
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)


@lru_cache(maxsize=32)
def _mask_pattern(terms: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)")


def mask(text: str, glossary: dict[str, str] | None = None):
    """Troca códigos do jogo (e termos do glossário) por ⟦N⟧.
    Retorna (texto_mascarado, lista_de_substitutos)."""
    subs: list[str] = []

    def put(value: str) -> str:
        subs.append(value)
        return TOKEN_FMT.format(len(subs) - 1)

    terms = tuple(sorted((t for t in (glossary or {}) if t), key=lambda t: (-len(t), t)))
    pattern = _mask_pattern(terms) if terms else None
    output: list[str] = []

    def put_terms(start: int, end: int):
        pos = start
        if pattern is not None:
            for match in pattern.finditer(text, start, end):
                # endpos limita a busca, mas não pode inventar uma fronteira
                # de palavra antes de um token literal como X0X.
                if match.end() < len(text) and re.match(r"\w", text[match.end()]):
                    continue
                output.append(text[pos:match.start()])
                output.append(put(glossary[match.group(0)]))
                pos = match.end()
        output.append(text[pos:end])

    def put_text(start: int, end: int):
        pos = start
        for literal in LITERAL_TOKEN_RE.finditer(text, start, end):
            put_terms(pos, literal.start())
            output.append(put(literal.group(0)))
            pos = literal.end()
        put_terms(pos, end)

    pos = 0
    for start, end in marker_spans(text):
        put_text(pos, start)
        output.append(put(text[start:end]))
        pos = end
    put_text(pos, len(text))
    return "".join(output), subs


def unmask(text: str, subs: list[str]) -> str:
    return TOKEN_RE.sub(lambda m: subs[int(m.group(1))] if int(m.group(1)) < len(subs) else m.group(0), text)


def tokens_ok(masked_src: str, translated: str) -> bool:
    """Plano B: o tradutor manteve exatamente os mesmos ⟦N⟧ (sem apagar/duplicar)?"""
    return (Counter(TOKEN_RE.findall(masked_src)) == Counter(TOKEN_RE.findall(translated))
            and masked_src.count("⟦") == translated.count("⟦")
            and masked_src.count("⟧") == translated.count("⟧"))


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
    return any(c.isalpha() for c in s)


def check_pair(src_text: str, dst_text: str) -> list[str]:
    """Valida o texto final, incluindo a identidade de condições traduzíveis."""
    def structure(text):
        codes = Counter()
        printf = []
        colors = []
        choices = {}
        for marker in markers(text):
            info = translatable_branches(marker)
            if info:
                fmt, branches = info
                # Cada ocorrência conserva a estrutura completa de cada ramo.
                # Somar os códigos de condições distintas permite redistribuí-los.
                branch_structure = tuple(structure(branch) for branch in branches)
                codes[(fmt, branch_structure)] += 1
                choices.setdefault(fmt, []).append(branch_structure)
                # A condição também ocupa uma posição no consumo dos argumentos
                # externos: formatos posicionais não podem atravessá-la.
                branch_printf = tuple(branch[1] for branch in branch_structure)
                if any(branch_printf):
                    printf.append((fmt, branch_printf))
                branch_colors = tuple(branch[2] for branch in branch_structure)
                if any(branch_colors):
                    colors.append((fmt, branch_colors))
                continue
            codes[(marker,)] += 1
            if marker.startswith("<c=") or marker == "</c>":
                colors.append(marker)
            elif PRINTF_RE.fullmatch(marker):
                # Argumentos numerados podem mudar de posição, desde que largura
                # e precisão com '*' também indiquem seus próprios argumentos.
                indexed = (re.match(r"%\d+\$", marker) is not None
                           and re.search(r"\*(?!\d+\$)", marker) is None)
                if not indexed:
                    printf.append(marker)
        # Condições com seletores diferentes podem mudar de lugar. Ocorrências
        # do mesmo seletor mantêm sua ordem relativa, inclusive os códigos internos.
        return (frozenset(codes.items()), tuple(printf), tuple(colors),
                frozenset((fmt, tuple(occurrences)) for fmt, occurrences in choices.items()))

    src_structure, dst_structure = structure(src_text), structure(dst_text)
    problems = []
    if src_structure[0] != dst_structure[0] or src_structure[3] != dst_structure[3]:
        problems.append("códigos ou condições diferentes do original")
    if src_structure[1] != dst_structure[1]:
        problems.append("ordem dos formatos printf diferente do original")
    if src_structure[2] != dst_structure[2]:
        problems.append("ordem das tags de cor diferente do original")
    if "|" in dst_text or "\r" in dst_text or "\n" in dst_text:
        problems.append("contém | ou quebra de linha real")
    if (Counter(LITERAL_TOKEN_RE.findall(src_text)) != Counter(LITERAL_TOKEN_RE.findall(dst_text))
            or src_text.count("⟦") != dst_text.count("⟦")
            or src_text.count("⟧") != dst_text.count("⟧")):
        problems.append("sobrou marcador de código")
    if (dst_text.count("{") != src_text.count("{")
            or dst_text.count("}") != src_text.count("}")):
        problems.append("chaves { } diferentes do original")
    if src_text.strip() and not dst_text.strip():
        problems.append("tradução vazia")
    return problems


def split_text(text: str, max_chars: int) -> list[str]:
    """Divide texto puro, preservando espaços e respeitando o limite do motor."""
    if max_chars < 1:
        raise ValueError("max_chars precisa ser positivo")
    parts = []
    while len(text) > max_chars:
        # Prefere limites de palavras, mas também divide palavras muito longas.
        spaces = list(re.finditer(r"\s+", text[:max_chars + 1]))
        end = spaces[-1].start() if spaces and spaces[-1].start() else max_chars
        parts.append(text[:end])
        text = text[end:]
    if text:
        parts.append(text)
    return parts


def clean_output(s: str) -> str:
    """O Warband quebra se houver | ou quebra de linha real no texto."""
    s = s.replace("\r", " ").replace("\n", " ").replace("|", "/")
    return re.sub(r"[ \t]{2,}", " ", s).strip()
