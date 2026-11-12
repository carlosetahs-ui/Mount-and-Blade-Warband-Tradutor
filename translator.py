"""Backends de tradução (ollama, argos, translators), cache SQLite,
limitador anti-ban e as 3 camadas de proteção dos códigos do jogo."""
from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import mbloc

PROMPT_VERSION = "mb1"


class BlockedError(Exception):
    """Serviço recusou (429/403/captcha)."""

class TextTooLongError(Exception):
    """Texto maior que max_chars do backend (vai para o Plano C)."""


# ---------------------------------------------------------------- limitador
class RateLimiter:
    def __init__(self, per_minute=20, per_hour=600, pause=(1.5, 4.0),
                 long_pause=(40, 90), long_every=80,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.per_minute, self.per_hour = per_minute, per_hour
        self.pause, self.long_pause, self.long_every = pause, long_pause, long_every
        self.clock, self.sleep = clock, sleep
        self.hits: deque[float] = deque()
        self.count = 0

    def wait(self):
        if self.count and self.long_every and self.count % self.long_every == 0:
            self.sleep(random.uniform(*self.long_pause))
        elif self.count:
            self.sleep(random.uniform(*self.pause))
        while True:
            now = self.clock()
            while self.hits and now - self.hits[0] >= 3600:
                self.hits.popleft()
            last_min = [t for t in self.hits if now - t < 60]
            waits = [0.0]
            if self.per_minute and len(last_min) >= self.per_minute:
                waits.append(60 - (now - last_min[-self.per_minute]))
            if self.per_hour and len(self.hits) >= self.per_hour:
                waits.append(3600 - (now - self.hits[-self.per_hour]))
            delay = max(waits)
            if delay <= 0:
                break
            self.sleep(delay + random.uniform(0.5, 2))
        self.hits.append(self.clock())
        self.count += 1


# ---------------------------------------------------------------- backends
class Backend:
    name = "base"
    accepts_context = False

    def translate(self, text: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def next_provider(self) -> bool:
        """Troca de serviço (Plano B). False se não houver outro."""
        return False

    @property
    def engine_id(self) -> str:
        return self.name


class OllamaBackend(Backend):
    name = "ollama"
    accepts_context = True

    def __init__(self, model="qwen2.5:7b", url="http://localhost:11434",
                 src="English", dst="Brazilian Portuguese", context="",
                 glossary_hint: dict | None = None, temperature=0.2, timeout=180):
        self.model, self.url, self.src, self.dst = model, url.rstrip("/"), src, dst
        self.context, self.temperature, self.timeout = context, temperature, timeout
        self.glossary_hint = glossary_hint or {}

    @property
    def engine_id(self):
        server = hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:12]
        return f"ollama:{self.model}:{server}"

    def _prompt(self, text):
        g = ""
        if self.glossary_hint:
            g = "Glossary (always use):\n" + "\n".join(f"- {k} = {v}" for k, v in list(self.glossary_hint.items())[:60]) + "\n"
        return (
            f"You translate Mount & Blade: Warband mod text from {self.src} to {self.dst}.\n"
            f"Context: {self.context}\n{g}"
            "Rules: keep every token like ⟦0⟧ exactly as is, same count, never translate or remove them. "
            "Keep a medieval tone. Do not add quotes, notes or explanations. Output only the translation.\n\n"
            f"Text: {text}\nTranslation:"
        )

    def translate(self, text):
        body = json.dumps({"model": self.model, "prompt": self._prompt(text), "stream": False,
                           "options": {"temperature": self.temperature}}).encode()
        req = urllib.request.Request(self.url + "/api/generate", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = json.loads(r.read())["response"].strip()
        prefix = r"^(?:Translation|Tradução)\s*:\s*"
        # O rótulo também pode fazer parte do texto do mod. Nesse caso,
        # preservá-lo é mais seguro que confundi-lo com uma nota do modelo.
        if not re.match(prefix, text.lstrip(), flags=re.I):
            out = re.sub(prefix, "", out, count=1, flags=re.I)
        # Aspas externas são ambíguas: podem pertencer ao próprio diálogo.
        # strip de caracteres isolados também removeria apenas uma das aspas
        # de frases como: Ele gritou "Ataque!"
        return out.strip()


class ArgosBackend(Backend):
    name = "argos"

    def __init__(self, src="en", dst="pt", auto_install=True):
        import argostranslate.package as pkg
        import argostranslate.translate as tr
        self.src, self.dst = src, dst

        def installed_translation():
            languages = {language.code: language for language in tr.get_installed_languages()}
            if src not in languages or dst not in languages:
                return None
            # Argos inclui aqui os caminhos por idiomas intermediários. Ter os
            # dois idiomas instalados, por si só, não garante a direção src->dst.
            return languages[src].get_translation(languages[dst])

        self._translation = installed_translation()
        if self._translation is None and auto_install:
            pkg.update_package_index()
            model = next((p for p in pkg.get_available_packages()
                          if p.from_code == src and p.to_code == dst
                          and getattr(p, "type", "translate") == "translate"), None)
            if model is None:
                raise ValueError(f"Argos: nenhum pacote de tradução {src} -> {dst} disponível no catálogo.")
            pkg.install_from_path(model.download())
            self._translation = installed_translation()
        if self._translation is None:
            raise ValueError(f"Argos: não há tradução instalada para {src} -> {dst}. "
                             "Instale um pacote compatível ou ative engine.argos.auto_install.")

    @property
    def engine_id(self):
        return f"argos:{self.src}-{self.dst}"

    def translate(self, text):
        return self._translation.translate(text)


class TranslatorsBackend(Backend):
    """Biblioteca `translators` com rotação de serviços."""
    name = "translators"

    def __init__(self, providers=("bing", "alibaba", "yandex", "baidu"), src="en", dst="pt",
                 limiter: RateLimiter | None = None, cooldown=600, max_retries=4, max_chars=3000,
                 timeout=15, api: Callable | None = None, clock=time.monotonic, sleep=time.sleep):
        self.providers = list(providers)
        if not self.providers:
            raise ValueError("Nenhum provider configurado.")
        if max_retries < 0 or max_chars < 1 or cooldown < 0 or timeout <= 0:
            raise ValueError("Limites inválidos do backend translators.")
        self.src, self.dst = src, dst
        self.limiter = limiter or RateLimiter()
        self.cooldown, self.max_retries, self.max_chars = cooldown, max_retries, max_chars
        self.timeout = timeout
        self.clock, self.sleep = clock, sleep
        self.blocked_until: dict[str, float] = {}
        self.idx = 0
        if api is None:
            import translators as ts
            api = lambda text, p: ts.translate_text(text, translator=p, from_language=self.src,
                                                   to_language=self.dst, timeout=self.timeout)
        self.api = api

    @property
    def current(self):
        return self.providers[self.idx]

    @property
    def engine_id(self):
        return f"translators:{self.current}"

    def next_provider(self):
        if len(self.providers) < 2:
            return False
        self.idx = (self.idx + 1) % len(self.providers)
        return True

    def _pick(self):
        while True:
            now = self.clock()
            for _ in range(len(self.providers)):
                if self.blocked_until.get(self.current, 0) <= now:
                    return self.current
                self.idx = (self.idx + 1) % len(self.providers)
            wait = min(self.blocked_until[p] for p in self.providers) - now
            self.sleep(max(wait, 0.01))

    @staticmethod
    def _is_block(err: Exception) -> bool:
        s = str(err).lower()
        return any(k in s for k in ("429", "403", "too many", "captcha", "blocked", "forbidden"))

    def translate(self, text):
        if len(text) > self.max_chars:
            raise TextTooLongError(f"texto com {len(text)} caracteres (máximo {self.max_chars})")
        for attempt in range(self.max_retries + 1):
            p = self._pick()
            self.limiter.wait()
            try:
                out = self.api(text, p)
                if not isinstance(out, str) or not out.strip():
                    raise RuntimeError("resposta vazia")
                return out
            except Exception as e:  # noqa: BLE001
                if self._is_block(e):
                    self.blocked_until[p] = self.clock() + self.cooldown
                if attempt == self.max_retries:
                    raise
                if not self._is_block(e):
                    self.sleep(min(60, 2 ** (attempt + 1)) + random.uniform(0, 1.5))
                self.idx = (self.idx + 1) % len(self.providers)


# ---------------------------------------------------------------- cache
@dataclass(frozen=True)
class OutputRecord:
    source: str
    policy: str | None = None
    generated_hash: str | None = None


class Cache:
    def __init__(self, path: Path, commit_every=1):
        if commit_every < 1:
            raise ValueError("cache.commit_every precisa ser positivo")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, base TEXT, engine TEXT, out TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS t_base ON t(base)")
        # A origem associada à saída permite detectar atualizações do mod,
        # mesmo quando os IDs e códigos do jogo continuam iguais.
        self.db.execute("CREATE TABLE IF NOT EXISTS output_sources "
                        "(path TEXT, id TEXT, source TEXT NOT NULL, PRIMARY KEY (path, id))")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(output_sources)")}
        for name in ("policy", "generated_hash"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE output_sources ADD COLUMN {name} TEXT")
        self.commit_every, self.pending = commit_every, 0

    @staticmethod
    def _h(*parts):
        return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()

    def keys(self, engine, masked, context, gl_hash):
        base = self._h(PROMPT_VERSION, masked, context, gl_hash)
        return self._h(base, engine), base

    def get(self, engine, masked, context, gl_hash, reuse_any=True):
        k, base = self.keys(engine, masked, context, gl_hash)
        row = self.db.execute("SELECT out FROM t WHERE k=?", (k,)).fetchone()
        if not row and reuse_any:
            row = self.db.execute("SELECT out FROM t WHERE base=? LIMIT 1", (base,)).fetchone()
        return row[0] if row else None

    def put(self, engine, masked, context, gl_hash, out):
        k, base = self.keys(engine, masked, context, gl_hash)
        self.db.execute("INSERT OR REPLACE INTO t VALUES (?,?,?,?)", (k, base, engine, out))
        self.pending += 1
        if self.pending >= self.commit_every:
            self.flush()

    def flush(self):
        self.db.commit()
        self.pending = 0

    def output_sources(self, path: Path) -> dict[str, str]:
        return {key: record.source for key, record in self.output_records(path).items()}

    @staticmethod
    def _output_records(db, path: Path) -> dict[str, OutputRecord]:
        columns = {row[1] for row in db.execute("PRAGMA table_info(output_sources)")}
        if not {"path", "id", "source"} <= columns:
            return {}
        policy = "policy" if "policy" in columns else "NULL"
        generated = "generated_hash" if "generated_hash" in columns else "NULL"
        rows = db.execute(f"SELECT id, source, {policy}, {generated} FROM output_sources WHERE path=?",
                          (str(path.resolve()),))
        return {key: OutputRecord(source, settings, output) for key, source, settings, output in rows}

    def output_records(self, path: Path) -> dict[str, OutputRecord]:
        return self._output_records(self.db, path)

    @classmethod
    def read_output_records(cls, cache_path: Path, path: Path) -> dict[str, OutputRecord]:
        """Consulta o histórico sem criar arquivos nem migrar um cache antigo."""
        if not cache_path.is_file():
            return {}
        db = sqlite3.connect(cache_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
        try:
            return cls._output_records(db, path)
        finally:
            db.close()

    def remember_output(self, path: Path, records: dict[str, OutputRecord], *, replace=False) -> None:
        """Persiste apenas os registros alterados após a gravação do CSV."""
        output = str(path.resolve())
        with self.db:
            if replace:
                self.db.execute("DELETE FROM output_sources WHERE path=?", (output,))
            self.db.executemany(
                "INSERT OR REPLACE INTO output_sources (path, id, source, policy, generated_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                ((output, key, record.source, record.policy, record.generated_hash)
                 for key, record in records.items()))
        self.pending = 0

    def remember_sources(self, path: Path, sources: dict[str, str]) -> None:
        """Registra a origem somente depois de gravar a saída com sucesso."""
        self.remember_output(path, {key: OutputRecord(value) for key, value in sources.items()}, replace=True)

    def close(self):
        try:
            self.flush()
        finally:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------- tradutor com proteção
class Translator:
    def __init__(self, backend: Backend, cache: Cache | None = None, context="",
                 glossary: dict[str, str] | None = None, reuse_cache=True,
                 translate_branches=True, log=print, post_edit: Callable[[str], str] | None = None,
                 source_lang="en", target_lang="pt"):
        self.b, self.cache, self.context = backend, cache, context
        # Preserva as chaves existentes de en->pt; outros pares não podem
        # reutilizar uma tradução em português ao trocar apenas o idioma.
        self.cache_context = context if (source_lang, target_lang) == ("en", "pt") else (
            context + "\x1flanguages=" + json.dumps([source_lang, target_lang])
        )
        if isinstance(backend, OllamaBackend):
            # Um nome de destino explícito pode mudar o idioma solicitado sem
            # alterar o código da pasta. Não reutilize traduções de outro prompt.
            standard_src = backend.src == source_lang or (source_lang == "en" and backend.src == "English")
            default_dst = "Brazilian Portuguese" if target_lang.lower() in ("pt", "pt-br", "pt_br") else target_lang
            if not standard_src or backend.dst != default_dst:
                self.cache_context += "\x1fprompt_languages=" + json.dumps([backend.src, backend.dst])
        self.glossary = glossary or {}
        self.gl_hash = (
            hashlib.sha1(json.dumps(self.glossary, sort_keys=True).encode()).hexdigest()[:12]
            if getattr(backend, "accepts_context", False) else ""
        )
        if isinstance(backend, OllamaBackend):
            # O cache anterior pode conter aspas já removidas pela normalização
            # antiga. Essas respostas não permitem reconstruir a pontuação.
            self.gl_hash = hashlib.sha1((self.gl_hash + ":output-quotes-v2").encode()).hexdigest()[:12]
        self.reuse, self.branches, self.log = reuse_cache, translate_branches, log
        self.post_edit = post_edit
        self.stats = {"ok": 0, "cache": 0, "planB": 0, "planC": 0, "fail": 0, "skip": 0}

    def _raw(self, masked: str, *, validate: Callable[[str], bool] | None = None,
             pending: dict[tuple[str, str], str] | None = None) -> str:
        if not mbloc.has_letters(mbloc.TOKEN_RE.sub("", masked)):
            return masked

        def valid(out):
            return self._valid_masked(masked, out) and (validate is None or validate(out))

        if self.cache:
            # Fragmentos repetidos podem reutilizar a mesma candidata enquanto
            # a frase completa ainda aguarda a revisão e a validação finais.
            if pending:
                hit = pending.get((self.b.engine_id, masked))
                if hit is None and self.reuse:
                    hit = next((out for (_, text), out in pending.items() if text == masked), None)
                if hit is not None and valid(hit):
                    self.stats["cache"] += 1
                    return hit
            hit = self.cache.get(self.b.engine_id, masked, self.cache_context, self.gl_hash, self.reuse)
            if hit is not None and valid(hit):
                self.stats["cache"] += 1
                return hit
        out = self.b.translate(masked)
        if self.cache and valid(out):
            if pending is None:
                self.cache.put(self.b.engine_id, masked, self.cache_context, self.gl_hash, out)
            else:
                pending[self.b.engine_id, masked] = out
        return out

    @staticmethod
    def _valid_masked(source: str, out: str) -> bool:
        return (isinstance(out, str) and bool(out.strip()) and mbloc.tokens_ok(source, out)
                and not mbloc.markers(out) and "{" not in out and "}" not in out)

    @staticmethod
    def _ordered_tokens(masked: str, out: str, subs: list[str]) -> bool:
        # Depois de restaurar dois %s, não há como perceber que suas
        # ocorrências trocaram de lugar. Confira a identidade antes de unmask.
        printf_tokens = set()
        conditions: dict[str, set[int]] = {}
        for index, value in enumerate(subs):
            for marker in mbloc.PRINTF_RE.finditer(value):
                code = marker.group(0)
                if code == "%%":
                    continue
                indexed = re.match(r"%[1-9]\d*\$", code)
                unindexed_star = any(not re.match(r"[1-9]\d*\$", code[star.end():])
                                     for star in re.finditer(r"\*", code))
                if not indexed or unindexed_star:
                    printf_tokens.add(index)
                    break
            condition = re.match(r"^\{(!?(?:reg|s)\d+|playername)\?", value)
            if condition:
                conditions.setdefault(condition.group(1), set()).add(index)

        source_order = [int(m.group(1)) for m in mbloc.TOKEN_RE.finditer(masked)]
        output_order = [int(m.group(1)) for m in mbloc.TOKEN_RE.finditer(out)]
        for group in (printf_tokens, *conditions.values()):
            if ([index for index in source_order if index in group]
                    != [index for index in output_order if index in group]):
                return False
        return True

    def _valid_candidate(self, source: str, masked: str, out: str, subs: list[str]) -> bool:
        return (self._valid_masked(masked, out) and self._ordered_tokens(masked, out, subs)
                and not mbloc.check_pair(source, mbloc.unmask(mbloc.clean_output(out), subs)))

    def _store_pending(self, pending: dict[tuple[str, str], str]) -> None:
        if self.cache:
            for (engine, masked), out in pending.items():
                self.cache.put(engine, masked, self.cache_context, self.gl_hash, out)

    def _finish(self, source: str, masked: str, out: str, subs: list[str]) -> str:
        if self.post_edit:
            out = "".join(self.post_edit(part) if is_text else part
                          for is_text, part in mbloc.split_segments(out))
        if not self._valid_masked(masked, out):
            raise ValueError("a tradução ou revisão alterou códigos protegidos")
        if not self._ordered_tokens(masked, out, subs):
            raise ValueError("a tradução ou revisão alterou a ordem de formatadores ou condições")
        result = mbloc.unmask(mbloc.clean_output(out), subs)
        problems = mbloc.check_pair(source, result)
        if problems:
            raise ValueError("; ".join(problems))
        return result

    def _plain(self, text: str, *, pending: dict[tuple[str, str], str] | None = None) -> str:
        """Traduz texto puro em pedaços menores que o limite do serviço."""
        limit = getattr(self.b, "max_chars", None)
        chunks = mbloc.split_text(text, limit) if limit else [text]
        output = []
        for chunk in chunks:
            lead = chunk[:len(chunk) - len(chunk.lstrip())]
            trail = chunk[len(chunk.rstrip()):]
            core = chunk.strip()
            if not mbloc.has_letters(core):
                output.append(chunk)
                continue
            out = self._raw(core, pending=pending)
            if not self._valid_masked(core, out):
                raise ValueError("resposta inválida ao traduzir um fragmento")
            output.append(lead + self._fragment_shape(core, out.strip()) + trail)
        return "".join(output)

    @staticmethod
    def _fragment_shape(source: str, out: str) -> str:
        if (not source.endswith((".", "!", "?")) and out.endswith(".")
                and not out.endswith("..")):
            out = out[:-1]
        if source[:1].islower() and out[:1].isupper():
            out = out[0].lower() + out[1:]
        return out

    def _segment(self, text: str) -> str:
        """Traduz um trecho sem tokens (Plano C e ramos de gênero)."""
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if not mbloc.has_letters(core):
            return text
        masked, subs = mbloc.mask(core, self.glossary)
        pending: dict[tuple[str, str], str] = {}
        valid = lambda out: self._valid_candidate(core, masked, out, subs)
        try:
            out = self._raw(masked, validate=valid, pending=pending)
        except TextTooLongError:
            out = ""
        if not valid(out):
            out = "".join(self._plain(part, pending=pending) if is_text else part
                          for is_text, part in mbloc.split_segments(masked))
        out = self._fragment_shape(masked, out.strip())
        result = self._finish(core, masked, out, subs)
        self._store_pending(pending)
        return lead + result + trail

    @staticmethod
    def _branch_text(text: str, reserved: str) -> str:
        # Apenas a prosa pode receber espaços no lugar dos delimitadores da
        # escolha. Uma substituição global de '/' danificaria tags como </c>.
        table = str.maketrans({char: " " for char in reserved})
        pieces, pos = [], 0
        for start, end in mbloc.marker_spans(text):
            pieces.append(text[pos:start].translate(table))
            pieces.append(text[start:end])
            pos = end
        pieces.append(text[pos:].translate(table))
        return "".join(pieces)

    def _branches(self, subs: list[str], original_markers: set[str]) -> list[str]:
        if not self.branches:
            return subs
        res = []
        for s in subs:
            info = mbloc.translatable_branches(s) if s in original_markers else None
            if info:
                fmt, parts = info
                try:
                    tr = [self._segment(p) if p.strip() else p for p in parts]
                    reserved = "/?:" if fmt.startswith("{%s/") else "?:"
                    s = fmt % tuple(self._branch_text(value, reserved) for value in tr)
                except Exception as exc:  # noqa: BLE001
                    self.stats["fail"] += 1
                    self.log(f"  ! falhou ao traduzir escolha {s}, mantendo original: {exc}")
            res.append(s)
        return res

    def translate(self, text: str) -> str:
        # A API também pode receber uma linha CSV completa. O prefixo nunca
        # passa pelo motor, pelo glossário, pelo cache ou pela revisão.
        if "\r" in text or "\n" in text:
            self.stats["fail"] += 1
            self.log("  ! entrada com múltiplas linhas, mantendo original")
            return text
        if "|" in text:
            key, _, value = text.partition("|")
            if text.lstrip().startswith("#") or not key.strip() or "|" in value:
                self.stats["skip"] += 1
                return text
            return key + "|" + self.translate(value)
        has_branches = self.branches and any(mbloc.translatable_branches(m) for m in mbloc.markers(text))
        if not mbloc.has_letters(mbloc.strip_markers(text)) and not has_branches:
            self.stats["skip"] += 1
            return text
        masked, subs = mbloc.mask(text, self.glossary)
        subs = self._branches(subs, set(mbloc.markers(text)))
        cached_before = self.stats["cache"]
        pending: dict[tuple[str, str], str] = {}
        valid = lambda out: self._valid_candidate(text, masked, out, subs)
        try:   # Plano A
            too_long = False
            try:
                out = self._raw(masked, validate=valid, pending=pending)
            except TextTooLongError:
                too_long = True  # frase grande demais: vai direto para o Plano C
                out = ""
            if valid(out):
                result = self._finish(text, masked, out, subs)
                self._store_pending(pending)
                if self.stats["cache"] == cached_before:
                    self.stats["ok"] += 1
                return result
            # Plano B: troca de serviço e tenta de novo
            if not too_long and self.b.next_provider():
                try:
                    out = self.b.translate(masked)
                except Exception as e:  # noqa: BLE001
                    self.log(f"  ! plano B falhou, indo para o plano C: {e}")
                else:
                    if valid(out):
                        result = self._finish(text, masked, out, subs)
                        pending[self.b.engine_id, masked] = out
                        self._store_pending(pending)
                        self.stats["planB"] += 1
                        return result
            # Plano C: só o texto entre os códigos
            parts = [self._plain(t, pending=pending) if is_txt else t for is_txt, t in mbloc.split_segments(masked)]
            result = self._finish(text, masked, "".join(parts), subs)
            self._store_pending(pending)
            self.stats["planC"] += 1
            return result
        except Exception as e:  # noqa: BLE001
            self.stats["fail"] += 1
            self.log(f"  ! falhou, mantendo original: {e}")
            return text
