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
        now = self.clock()
        while self.hits and now - self.hits[0] > 3600:
            self.hits.popleft()
        last_min = [t for t in self.hits if now - t < 60]
        if self.per_minute and len(last_min) >= self.per_minute:
            self.sleep(60 - (now - last_min[0]) + random.uniform(0.5, 2))
        if self.per_hour and len(self.hits) >= self.per_hour:
            self.sleep(3600 - (now - self.hits[0]) + random.uniform(1, 5))
        if self.count and self.long_every and self.count % self.long_every == 0:
            self.sleep(random.uniform(*self.long_pause))
        elif self.count:
            self.sleep(random.uniform(*self.pause))
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
        return f"ollama:{self.model}"

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
        out = re.sub(r"^(Translation|Tradução)\s*:\s*", "", out, flags=re.I)
        return out.strip().strip('"').strip("“”")


class ArgosBackend(Backend):
    name = "argos"

    def __init__(self, src="en", dst="pt", auto_install=True):
        import argostranslate.package as pkg
        import argostranslate.translate as tr
        self.src, self.dst, self._tr = src, dst, tr
        installed = {(l.code) for l in tr.get_installed_languages()}
        if auto_install and not {src, dst} <= installed:
            pkg.update_package_index()
            for p in pkg.get_available_packages():
                if p.from_code == src and p.to_code == dst:
                    pkg.install_from_path(p.download())

    @property
    def engine_id(self):
        return f"argos:{self.src}-{self.dst}"

    def translate(self, text):
        return self._tr.translate(text, self.src, self.dst)


class TranslatorsBackend(Backend):
    """Biblioteca `translators` com rotação de serviços."""
    name = "translators"

    def __init__(self, providers=("bing", "alibaba", "yandex", "baidu"), src="en", dst="pt",
                 limiter: RateLimiter | None = None, cooldown=600, max_retries=4, max_chars=3000,
                 api: Callable | None = None, clock=time.monotonic, sleep=time.sleep):
        self.providers = list(providers)
        if not self.providers:
            raise ValueError("Nenhum provider configurado.")
        self.src, self.dst = src, dst
        self.limiter = limiter or RateLimiter()
        self.cooldown, self.max_retries, self.max_chars = cooldown, max_retries, max_chars
        self.clock, self.sleep = clock, sleep
        self.blocked_until: dict[str, float] = {}
        self.idx = 0
        if api is None:
            import translators as ts
            api = lambda text, p: ts.translate_text(text, translator=p, from_language=self.src, to_language=self.dst)
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
        now = self.clock()
        for _ in range(len(self.providers)):
            if self.blocked_until.get(self.current, 0) <= now:
                return self.current
            self.idx = (self.idx + 1) % len(self.providers)
        wait = min(self.blocked_until.values()) - now
        self.sleep(max(wait, 1))
        return self._pick()

    @staticmethod
    def _is_block(err: Exception) -> bool:
        s = str(err).lower()
        return any(k in s for k in ("429", "403", "too many", "captcha", "blocked", "forbidden"))

    def translate(self, text):
        if len(text) > self.max_chars:
            raise TextTooLongError(f"texto com {len(text)} caracteres (máximo {self.max_chars})")
        attempt = 0
        while True:
            p = self._pick()
            self.limiter.wait()
            try:
                out = self.api(text, p)
                if not out:
                    raise RuntimeError("resposta vazia")
                return out
            except Exception as e:  # noqa: BLE001
                if self._is_block(e):
                    self.blocked_until[p] = self.clock() + self.cooldown
                    self.idx = (self.idx + 1) % len(self.providers)
                    continue
                attempt += 1
                if attempt > self.max_retries:
                    raise
                self.sleep(min(60, 2 ** attempt) + random.uniform(0, 1.5))
                self.idx = (self.idx + 1) % len(self.providers)


# ---------------------------------------------------------------- cache
class Cache:
    def __init__(self, path: Path, commit_every=1):
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, base TEXT, engine TEXT, out TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS t_base ON t(base)")
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

    def close(self):
        self.flush()
        self.db.close()


# ---------------------------------------------------------------- tradutor com proteção
class Translator:
    def __init__(self, backend: Backend, cache: Cache | None = None, context="",
                 glossary: dict[str, str] | None = None, reuse_cache=True,
                 translate_branches=True, log=print):
        self.b, self.cache, self.context = backend, cache, context
        self.glossary = glossary or {}
        self.gl_hash = (
            hashlib.sha1(json.dumps(self.glossary, sort_keys=True).encode()).hexdigest()[:12]
            if getattr(backend, "accepts_context", False) else ""
        )
        self.reuse, self.branches, self.log = reuse_cache, translate_branches, log
        self.stats = {"ok": 0, "cache": 0, "planB": 0, "planC": 0, "fail": 0, "skip": 0}

    def _raw(self, masked: str) -> str:
        if self.cache:
            hit = self.cache.get(self.b.engine_id, masked, self.context, self.gl_hash, self.reuse)
            if hit is not None:
                self.stats["cache"] += 1
                return hit
        out = self.b.translate(masked)
        if self.cache and out.strip() and mbloc.tokens_ok(masked, out):
            self.cache.put(self.b.engine_id, masked, self.context, self.gl_hash, out)
        return out

    def _segment(self, text: str) -> str:
        """Traduz um trecho sem tokens (Plano C e ramos de gênero)."""
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if not mbloc.has_letters(core):
            return text
        out = mbloc.TOKEN_RE.sub("", self._raw(core)).strip()
        if not out:
            return text  # tradução vazia: mantém o original
        if (not core.endswith((".", "!", "?")) and out.endswith(".")
                and not out.endswith("..")):
            out = out[:-1]
        if core[:1].islower() and out[:1].isupper():
            out = out[0].lower() + out[1:]
        return lead + out + trail

    def _branches(self, subs: list[str]) -> list[str]:
        if not self.branches:
            return subs
        res = []
        for s in subs:
            info = mbloc.translatable_branches(s)
            if info:
                fmt, parts = info
                try:
                    tr = [self._segment(p) if p.strip() else p for p in parts]
                    if "/" in fmt and fmt.startswith("{%s/"):
                        s = "{%s/%s}" % tuple(x.replace("/", " ") for x in tr)
                    else:
                        s = fmt % tuple(x.replace(":", " ") for x in tr)
                except Exception:  # noqa: BLE001
                    pass
            res.append(s)
        return res

    def translate(self, text: str) -> str:
        if not mbloc.has_letters(mbloc.MARKER_RE.sub("", text)):
            self.stats["skip"] += 1
            return text
        masked, subs = mbloc.mask(text, self.glossary)
        subs = self._branches(subs)
        cached_before = self.stats["cache"]
        try:   # Plano A
            too_long = False
            try:
                out = self._raw(masked)
            except TextTooLongError:
                too_long = True  # frase grande demais: vai direto para o Plano C
                out = ""
            if out.strip() and mbloc.tokens_ok(masked, out):
                if self.stats["cache"] == cached_before:
                    self.stats["ok"] += 1
                return mbloc.clean_output(mbloc.unmask(out, subs))
            # Plano B: troca de serviço e tenta de novo
            if not too_long and self.b.next_provider():
                try:
                    out = self.b.translate(masked)
                    if out.strip() and mbloc.tokens_ok(masked, out):
                        self.stats["planB"] += 1
                        if self.cache:
                            self.cache.put(self.b.engine_id, masked, self.context, self.gl_hash, out)
                        return mbloc.clean_output(mbloc.unmask(out, subs))
                except Exception as e:  # noqa: BLE001
                    self.log(f"  ! plano B falhou, indo para o plano C: {e}")
            # Plano C: só o texto entre os códigos
            parts = [self._segment(t) if is_txt else t for is_txt, t in mbloc.split_segments(masked)]
            self.stats["planC"] += 1
            return mbloc.clean_output(mbloc.unmask("".join(parts), subs))
        except Exception as e:  # noqa: BLE001
            self.stats["fail"] += 1
            self.log(f"  ! falhou, mantendo original: {e}")
            return text
