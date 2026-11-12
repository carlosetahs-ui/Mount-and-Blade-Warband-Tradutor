#!/usr/bin/env python3
"""Tradutor de mods de Mount & Blade: Warband.

Comandos:
  python mbtr.py scan      --config config.toml
  python mbtr.py translate --config config.toml [--limit 30] [--file dialogs.csv]
  python mbtr.py check     --config config.toml
  python mbtr.py glossary  --config config.toml     (sugere nomes para o glossário)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

import mbloc
import revisao
from translator import (ArgosBackend, Cache, OllamaBackend, OutputRecord, RateLimiter,
                        TranslatorsBackend, Translator)

# Arquivos com nomes próprios: bons candidatos a glossário
NAME_FILES = ("troops.csv", "parties.csv", "factions.csv", "party_templates.csv", "item_kinds.csv")


# ---------------------------------------------------------------- config
def load_config(path: str) -> dict:
    config_path = Path(path).resolve()
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    cfg["_dir"] = config_path.parent
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    """Erros de configuração são reportados antes de carregar motores ou gravar arquivos."""
    for name in ("mod", "engine", "glossary", "cache", "revision"):
        if not isinstance(cfg.get(name, {}), dict):
            raise ValueError(f"[{name}] precisa ser uma tabela TOML")
    mod = cfg.get("mod", {})
    if not isinstance(mod.get("path"), str) or not mod["path"].strip():
        raise ValueError("mod.path precisa indicar a pasta do mod")

    def strings(section, key, nonempty=False):
        value = section.get(key, [])
        if (not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value)
                or (nonempty and not value)):
            raise ValueError(f"{key} precisa ser uma lista de textos" + (" não vazia" if nonempty else ""))

    def number(section, key, minimum=0, integer=False):
        if key not in section:
            return
        value = section[key]
        kind = int if integer else (int, float)
        if (isinstance(value, bool) or not isinstance(value, kind)
                or not math.isfinite(value) or value < minimum):
            raise ValueError(f"{key} precisa ser {'inteiro ' if integer else ''}>= {minimum}")

    for key in ("source_lang", "target_lang"):
        value = mod.get(key, "en" if key == "source_lang" else "pt")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value):
            raise ValueError(f"mod.{key} precisa ser um nome de idioma, como en ou pt")
    for section, keys in ((mod, ("install", "keep_existing", "translate_gender_choices")),
                          (cfg.get("revision", {}), ("ptbr_fix",))):
        for key in keys:
            if key in section and not isinstance(section[key], bool):
                raise ValueError(f"{key} precisa ser true ou false")
    for section, keys in ((mod, ("output", "context")),
                          (cfg.get("cache", {}), ("file",)),
                          (cfg.get("glossary", {}), ("file", "do_not_translate")),
                          (cfg.get("revision", {}), ("replacements_file",))):
        for key in keys:
            if key in section and (not isinstance(section[key], str)
                                   or (key in ("output", "file") and not section[key].strip())):
                raise ValueError(f"{key} precisa ser um texto válido")
    for key in ("skip_files", "skip_ids"):
        strings(mod, key)
    try:
        skip_id_patterns(cfg)
    except re.error as exc:
        raise ValueError(f"mod.skip_ids contém uma expressão inválida: {exc}") from exc
    number(mod, "checkpoint_every", 1, integer=True)
    cache = cfg.get("cache", {})
    number(cache, "commit_every", 1, integer=True)
    if cache.get("reuse_cache_from", "auto") not in ("auto", "none"):
        raise ValueError('cache.reuse_cache_from precisa ser "auto" ou "none"')
    engine = cfg.get("engine", {})
    if engine.get("backend", "translators") not in ("translators", "argos", "ollama"):
        raise ValueError("engine.backend: use translators, argos ou ollama")
    if not isinstance(engine.get("token_style", "math"), str) or engine.get("token_style", "math") not in mbloc.STYLES:
        raise ValueError("engine.token_style: use math ou xnx")
    if not isinstance(engine.get("target_code", "pt"), str) or not engine.get("target_code", "pt").strip():
        raise ValueError("engine.target_code precisa ser um código de idioma")
    for name in ("translators", "ollama", "argos"):
        if not isinstance(engine.get(name, {}), dict):
            raise ValueError(f"[engine.{name}] precisa ser uma tabela TOML")
    web = engine.get("translators", {})
    if "providers" in web:
        strings(web, "providers", nonempty=True)
    for key in ("max_per_minute", "max_per_hour", "long_every", "max_retries"):
        number(web, key, integer=True)
    number(web, "max_chars", 1, integer=True)
    number(web, "cooldown")
    number(web, "timeout", 0.001)
    for key in ("pause", "long_pause"):
        if key in web:
            pair = web[key]
            if (not isinstance(pair, list) or len(pair) != 2
                    or any(isinstance(n, bool) or not isinstance(n, (int, float))
                           or not math.isfinite(n) or n < 0 for n in pair)
                    or pair[0] > pair[1]):
                raise ValueError(f"engine.translators.{key} precisa ser [mínimo, máximo], ambos >= 0")
    ollama = engine.get("ollama", {})
    number(ollama, "temperature")
    number(ollama, "timeout", 0.001)
    for key in ("url", "model", "target_name"):
        if key in ollama and (not isinstance(ollama[key], str) or not ollama[key].strip()):
            raise ValueError(f"engine.ollama.{key} precisa ser um texto não vazio")
    argos = engine.get("argos", {})
    if "auto_install" in argos and not isinstance(argos["auto_install"], bool):
        raise ValueError("engine.argos.auto_install precisa ser true ou false")
    revision = cfg.get("revision", {})
    strings(revision, "backends")
    if any(b not in ("translators", "argos", "ollama") for b in revision.get("backends", [])):
        raise ValueError("revision.backends: use translators, argos ou ollama")
    terms = cfg.get("glossary", {}).get("terms", {})
    if (not isinstance(terms, dict)
            or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in terms.items())):
        raise ValueError("glossary.terms precisa conter original = tradução, sem originais vazios")
    paths(cfg)


def rel(cfg, p) -> Path:
    p = Path(p).expanduser()
    return p if p.is_absolute() else cfg["_dir"] / p


def load_glossary(cfg) -> dict[str, str]:
    g: dict[str, str] = {}
    gcfg = cfg.get("glossary", {})
    f = gcfg.get("file")
    if f and rel(cfg, f).exists():
        for number, line in enumerate(rel(cfg, f).read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "\t" in line:
                a, b = line.split("\t", 1)
            elif "=" in line:
                a, b = line.split("=", 1)
            else:
                print(f"Aviso: {rel(cfg, f)}:{number}: use TAB ou '=' para separar original e tradução.", file=sys.stderr)
                continue
            if a.strip():
                g[a.strip()] = b.strip()
            else:
                print(f"Aviso: {rel(cfg, f)}:{number}: termo original vazio ignorado.", file=sys.stderr)
    f = gcfg.get("do_not_translate")
    if f and rel(cfg, f).exists():
        for line in rel(cfg, f).read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                g[line.strip()] = line.strip()
    g.update(gcfg.get("terms", {}))
    return g


def paths(cfg):
    mod = rel(cfg, cfg["mod"]["path"]).resolve()
    src = mod / "languages" / cfg["mod"].get("source_lang", "en")
    dst_lang = cfg["mod"].get("target_lang", "pt")
    if cfg["mod"].get("install", False):
        out = mod / "languages" / dst_lang
    else:
        out = rel(cfg, cfg["mod"].get("output", "saida")) / mod.name / "languages" / dst_lang
    if src.resolve() == out.resolve():
        raise ValueError("A pasta de saída é igual à origem. Escolha outro target_lang ou output.")
    return mod, src, out


def source_files(cfg, only: str | None = None) -> list[Path]:
    _, src, _ = paths(cfg)
    if not src.is_dir():
        raise ValueError(f"Pasta de origem não encontrada: {src}\n"
                         "O mod precisa ter os CSVs de idioma (veja no README como exportar o template do jogo).")
    skip = {s.lower() for s in cfg["mod"].get("skip_files", [])}
    files = sorted((p for p in src.iterdir() if p.is_file() and p.suffix.lower() == ".csv"
                    and p.name.lower() not in skip), key=lambda p: p.name.lower())
    if only:
        files = [p for p in files if p.name.lower() == only.lower()]
    if not files:
        raise ValueError(f"Nenhum CSV selecionado em {src}" + (f" para --file {only}" if only else "")
                         + ". Confira também mod.skip_files.")
    return files


def skip_id_patterns(cfg):
    return [re.compile(p) for p in cfg["mod"].get("skip_ids", [])]


def make_backend(cfg, glossary):
    e = cfg.get("engine", {})
    kind = e.get("backend", "translators")
    src = cfg["mod"].get("source_lang", "en")
    dst = e.get("target_code", cfg["mod"].get("target_lang", "pt"))
    if kind == "ollama":
        o = e.get("ollama", {})
        return OllamaBackend(model=o.get("model", "qwen2.5:7b"), url=o.get("url", "http://localhost:11434"), src=src,
                             dst=o.get("target_name", "Brazilian Portuguese" if dst.lower() in ("pt", "pt-br", "pt_br") else dst),
                             context=cfg["mod"].get("context", ""), glossary_hint=glossary,
                             temperature=o.get("temperature", 0.2), timeout=o.get("timeout", 180))
    if kind == "argos":
        return ArgosBackend(src=src, dst=dst, auto_install=e.get("argos", {}).get("auto_install", True))
    if kind == "translators":
        t = e.get("translators", {})
        lim = RateLimiter(per_minute=t.get("max_per_minute", 20), per_hour=t.get("max_per_hour", 600),
                          pause=tuple(t.get("pause", [1.5, 4.0])), long_pause=tuple(t.get("long_pause", [40, 90])),
                          long_every=t.get("long_every", 80))
        return TranslatorsBackend(providers=t.get("providers", ["bing", "alibaba", "yandex", "baidu"]),
                                  src=src, dst=dst, limiter=lim, cooldown=t.get("cooldown", 600),
                                  max_retries=t.get("max_retries", 4), max_chars=t.get("max_chars", 3000),
                                  timeout=t.get("timeout", 15))
    raise ValueError(f"backend desconhecido: {kind} (use ollama, argos ou translators)")


# ---------------------------------------------------------------- comandos
def cmd_scan(cfg, args):
    mod, src, out = paths(cfg)
    print(f"Mod:     {mod}\nOrigem:  {src}\nSaída:   {out}\n")
    total_lines = total_chars = 0
    for f in source_files(cfg, getattr(args, "file", None)):
        lf = mbloc.read_csv(f)
        rows = [e for e in lf.entries if e.key]
        chars = sum(len(e.text) for e in rows)
        mk = sum(len(mbloc.markers(e.text)) for e in rows)
        total_lines += len(rows); total_chars += chars
        print(f"  {f.name:<28} {len(rows):>6} linhas  {chars:>8} caracteres  {mk:>5} códigos")
    print(f"\nTotal: {total_lines} linhas, {total_chars} caracteres.")
    t = cfg.get("engine", {}).get("translators", {})
    if cfg.get("engine", {}).get("backend", "translators") == "translators":
        per_h = t.get("max_per_hour", 600)
        print(f"Estimativa (translators, {per_h}/hora): ~{total_lines / max(per_h, 1):.1f} h sem cache.")


def validate_source(lf: mbloc.LocFile) -> None:
    duplicates = [key for key, count in Counter(e.key for e in lf.entries if e.key is not None).items()
                  if count > 1 or not key.strip()]
    if duplicates:
        raise ValueError(f"{lf.path}: IDs vazios ou repetidos na origem: {', '.join(repr(k) for k in duplicates)}")
    for line, entry in enumerate(lf.entries, 1):
        if "\r" in entry.raw:
            raise ValueError(f"{lf.path}:{line}: quebra de linha CR isolada na origem")


def source_fingerprints(lf: mbloc.LocFile, source_lang: str, target_lang: str,
                        target_name: str | None = None) -> dict[str, str]:
    return {e.key: hashlib.sha256(json.dumps([source_lang, target_lang, target_name, e.text],
                                            ensure_ascii=False).encode("utf-8")).hexdigest()
            for e in lf.entries if e.key is not None}


def translation_languages(cfg) -> tuple[str, str, str | None]:
    mod, engine = cfg["mod"], cfg.get("engine", {})
    source = mod.get("source_lang", "en")
    target = engine.get("target_code", mod.get("target_lang", "pt"))
    target_name = None
    if engine.get("backend", "translators") == "ollama":
        default = "Brazilian Portuguese" if target.lower() in ("pt", "pt-br", "pt_br") else target
        explicit = engine.get("ollama", {}).get("target_name", default)
        if explicit != default:
            target_name = explicit
    return source, target, target_name


def revision_options(cfg) -> tuple[bool, dict[str, str]]:
    revision = cfg.get("revision", {})
    backend = cfg.get("engine", {}).get("backend", "translators")
    selected = revision.get("backends", [])
    if selected and backend not in selected:
        return False, {}
    _, target, target_name = translation_languages(cfg)
    language = (target_name or target).strip().lower().replace("_", "-")
    ptbr = revision.get("ptbr_fix", True) and language in (
        "pt", "pt-br", "brazilian portuguese", "português brasileiro", "portugues brasileiro",
        "portuguese", "português", "portugues")
    replacements_file = revision.get("replacements_file")
    replacements = revisao.load_replacements(rel(cfg, replacements_file)) if replacements_file else {}
    return ptbr, replacements


def output_policy(cfg, glossary, ptbr_fix, replacements) -> str:
    settings = {"version": 2, "glossary": glossary,
                "context": cfg["mod"].get("context", ""),
                "translate_branches": cfg["mod"].get("translate_gender_choices", True),
                "ptbr_fix": ptbr_fix,
                "replacements": {key.lower(): value for key, value in replacements.items()}}
    return hashlib.sha256(json.dumps(settings, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def history_problem(record: OutputRecord | None, source: str, text: str, policy: str) -> str | None:
    if record is None:
        return None
    original_source = record.source.removeprefix("incomplete:")
    if original_source != source:
        return "origem ou idioma alterado"
    unchanged = record.generated_hash is None or record.generated_hash == text_fingerprint(text)
    if record.source.startswith("incomplete:") and unchanged:
        return "tradução anterior incompleta"
    if record.generated_hash is not None and unchanged and record.policy is not None and record.policy != policy:
        return "glossário, contexto ou revisão alterados"
    return None


def cmd_translate(cfg, args):
    files = source_files(cfg, args.file)
    _, _, out_dir = paths(cfg)
    keep_existing = cfg["mod"].get("keep_existing", True)
    # Valida o lote inteiro antes de baixar modelos ou substituir saídas.
    # Os arquivos são relidos por vez para não manter todo o mod na memória.
    for f in files:
        validate_source(mbloc.read_csv(f))
        target = out_dir / f.name
        if keep_existing and target.exists():
            mbloc.read_csv(target)
    glossary = load_glossary(cfg)
    skip_ids = skip_id_patterns(cfg)

    ptbr_fix, replacements = revision_options(cfg)
    policy = output_policy(cfg, glossary, ptbr_fix, replacements)
    post_edit = revisao.build(ptbr_fix=ptbr_fix, replacements=replacements)
    source_lang, target_lang, target_name = translation_languages(cfg)
    backend = make_backend(cfg, glossary)

    cache = Cache(rel(cfg, cfg.get("cache", {}).get("file", "cache.sqlite")),
                  commit_every=cfg.get("cache", {}).get("commit_every", 1))
    tr = Translator(backend, cache, context=cfg["mod"].get("context", ""),
                    glossary=glossary, reuse_cache=cfg.get("cache", {}).get("reuse_cache_from", "auto") == "auto",
                    translate_branches=cfg["mod"].get("translate_gender_choices", True),
                    post_edit=post_edit, source_lang=source_lang, target_lang=target_lang)
    checkpoint_every = cfg["mod"].get("checkpoint_every", 25)
    budget = args.limit
    kept = ignored = 0
    interrupted = False
    t0 = time.monotonic()
    try:
        for f in files:
            lf = mbloc.read_csv(f)
            validate_source(lf)
            target = out_dir / f.name
            fingerprints = source_fingerprints(lf, source_lang, target_lang, target_name)
            previous = cache.output_records(target)
            existing = {}
            if keep_existing and target.exists():
                entries = mbloc.read_csv(target).entries
                counts = Counter(e.key for e in entries if e.key)
                existing = {e.key: e.text for e in entries if e.key and counts[e.key] == 1}
            rows = [e for e in lf.entries if e.key]
            print(f"\n== {f.name} ({len(rows)} linhas)")
            # Preenche o arquivo inteiro antes de começar; um checkpoint também
            # mantém as traduções válidas que estão depois da linha atual.
            valid_existing = set()
            records = {}
            result = [(e.key, e.text if e.key is not None else e.raw) for e in lf.entries]
            for i, e in enumerate(lf.entries):
                if e.key is None:
                    continue
                record = previous.get(e.key)
                same_source = record is not None and record.source.removeprefix("incomplete:") == fingerprints[e.key]
                records[e.key] = record if same_source else OutputRecord(fingerprints[e.key])
                old = existing.get(e.key)
                if old is not None and old != e.text:
                    if check_pair(e.text, old):
                        print(f"  ! {e.key}: tradução existente inválida; será refeita.")
                        records[e.key] = OutputRecord(fingerprints[e.key])
                        continue
                    problem = history_problem(record, fingerprints[e.key], old, policy)
                    if problem:
                        print(f"  ! {e.key}: {problem}; tradução será refeita.")
                        if problem == "glossário, contexto ou revisão alterados":
                            # Com --limit, conserva a saída anterior enquanto
                            # aguarda atualização, sem atualizar sua política.
                            result[i] = (e.key, old)
                        else:
                            records[e.key] = OutputRecord(fingerprints[e.key])
                    else:
                        result[i] = (e.key, old)
                        valid_existing.add(e.key)
                        # Uma edição manual pode ter corrigido uma falha.
                        # Nunca a promova a saída gerada automaticamente.
                        if record is not None:
                            records[e.key] = OutputRecord(fingerprints[e.key], record.policy, record.generated_hash)
            done = 0
            dirty = set(records)
            first_save = True

            def save_progress():
                nonlocal first_save
                mbloc.write_csv(target, result, lf.bom, source=lf)
                if dirty or first_save:
                    cache.remember_output(target, {key: records[key] for key in dirty}, replace=first_save)
                    dirty.clear()
                    first_save = False

            try:
                for i, e in enumerate(lf.entries):
                    if e.key is None:
                        continue
                    if e.key in valid_existing:
                        kept += 1
                        continue
                    if any(p.search(e.key) for p in skip_ids):
                        ignored += 1
                        continue
                    if "|" in e.text:
                        print(f"  ! {e.key}: separador extra na origem; mantendo original.")
                        ignored += 1
                        continue
                    if budget is not None and budget <= 0:
                        continue
                    failures_before = tr.stats["fail"]
                    result[i] = (e.key, tr.translate(e.text))
                    source = fingerprints[e.key]
                    if tr.stats["fail"] > failures_before:
                        # Uma escolha pode falhar enquanto o resto da frase é
                        # traduzido. Não trate essa linha parcial como concluída.
                        source = "incomplete:" + source
                    records[e.key] = OutputRecord(source, policy, text_fingerprint(result[i][1]))
                    dirty.add(e.key)
                    done += 1
                    if budget is not None:
                        budget -= 1
                    if done % checkpoint_every == 0:
                        s = tr.stats
                        print(f"  {done}/{len(rows)}  ok={s['ok']} cache={s['cache']} B={s['planB']} "
                              f"C={s['planC']} falhas={s['fail']}  [{tr.b.engine_id}]", flush=True)
                        save_progress()
            finally:
                save_progress()
            if budget is not None and budget <= 0:
                print("\nLimite atingido (--limit)."); break
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrompido. Progresso salvo na saída e no cache — rode de novo para continuar.")
    finally:
        cache.close()
    s = tr.stats
    print(f"\nFim em {time.monotonic() - t0:.0f}s: ok={s['ok']} cache={s['cache']} planoB={s['planB']} "
          f"planoC={s['planC']} falhas={s['fail']} sem_texto={s['skip']} "
          f"mantidas={kept} ignoradas={ignored}\nSaída: {out_dir}")
    return 130 if interrupted else (1 if s["fail"] else 0)


def check_pair(src_text: str, dst_text: str) -> list[str]:
    return mbloc.check_pair(src_text, dst_text)


def cmd_check(cfg, args):
    _, _, out_dir = paths(cfg)
    glossary = load_glossary(cfg)
    skip_ids = skip_id_patterns(cfg)
    ptbr_fix, replacements = revision_options(cfg)
    policy = output_policy(cfg, glossary, ptbr_fix, replacements)
    source_lang, target_lang, target_name = translation_languages(cfg)
    cache_path = rel(cfg, cfg.get("cache", {}).get("file", "cache.sqlite"))
    bad = untranslated = total = 0
    for f in source_files(cfg, getattr(args, "file", None)):
        target = out_dir / f.name
        if not target.exists():
            print(f"  [faltando] {f.name}")
            bad += 1
            continue
        source_file = mbloc.read_csv(f)
        source_entries, target_entries = source_file.entries, mbloc.read_csv(target).entries
        fingerprints = source_fingerprints(source_file, source_lang, target_lang, target_name)
        previous = Cache.read_output_records(cache_path, target)
        for label, entries in (("origem", source_entries), ("tradução", target_entries)):
            for line, entry in enumerate(entries, 1):
                if "\r" in entry.raw:
                    print(f"  {f.name}:{line}: quebra de linha CR isolada na {label}")
                    bad += 1
            for key, count in Counter(e.key for e in entries if e.key is not None).items():
                if not key.strip() or count > 1:
                    print(f"  {f.name}: ID vazio ou repetido na {label}: {key!r} ({count} ocorrência(s))")
                    bad += 1
        source_ids = [e.key for e in source_entries if e.key is not None]
        target_ids = [e.key for e in target_entries if e.key is not None]
        if Counter(source_ids) == Counter(target_ids) and source_ids != target_ids:
            print(f"  {f.name}: ordem dos IDs diferente da origem")
            bad += 1
        src = {e.key: e.text for e in source_entries if e.key}
        dst = {e.key: e.text for e in target_entries if e.key}
        for k in sorted(src.keys() - dst.keys()):
            print(f"  {f.name}: id ausente na tradução: {k}"); bad += 1
        for k in sorted(dst.keys() - src.keys()):
            print(f"  {f.name}: id extra na tradução: {k}"); bad += 1
        for k, v in src.items():
            if k not in dst:
                continue
            total += 1
            problem = history_problem(previous.get(k), fingerprints[k], dst[k], policy)
            if problem:
                print(f"  {f.name}:{k}: {problem}")
                bad += 1
            if dst[k] == v and not any(p.search(k) for p in skip_ids):
                masked, _ = mbloc.mask(v, glossary)
                text_pending = mbloc.has_letters(mbloc.TOKEN_RE.sub("", masked))
                branches_pending = (cfg["mod"].get("translate_gender_choices", True)
                                    and any(mbloc.translatable_branches(m) for m in mbloc.markers(v)))
                if text_pending or branches_pending:
                    untranslated += 1
            for p in check_pair(v, dst[k]):
                print(f"  {f.name}:{k}: {p}"); bad += 1
    print(f"\n{total} linhas verificadas, {bad} problema(s), "
          f"{untranslated} inalterada(s) com texto possivelmente pendente.")
    return 1 if bad else 0


def cmd_glossary(cfg, args):
    counts: Counter[str] = Counter()
    for f in source_files(cfg, getattr(args, "file", None)):
        if f.name.lower() not in NAME_FILES:
            continue
        for e in mbloc.read_csv(f).entries:
            if e.key and not e.key.endswith("_pl"):
                t = mbloc.strip_markers(e.text).strip()
                if t and t[0].isupper() and len(t.split()) <= 3:
                    counts[t] += 1
    out = rel(cfg, "sugestao_glossario.tsv")
    with out.open("w", encoding="utf-8") as fh:
        fh.write("# Revise: apague o que deve ser traduzido normalmente,\n"
                 "# e copie para glossario.tsv (original<TAB>tradução) ou nomes.txt (não traduzir).\n")
        for name, _ in counts.most_common():
            fh.write(f"{name}\t{name}\n")
    print(f"{len(counts)} nomes sugeridos em {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tradutor de mods de Mount & Blade: Warband")
    ap.add_argument("command", choices=["scan", "translate", "check", "glossary"])
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--limit", type=int, help="traduz no máximo N linhas novas (teste)")
    ap.add_argument("--file", help="processa só este arquivo, ex.: dialogs.csv")
    args = ap.parse_args(argv)
    if args.limit is not None and (args.limit <= 0 or args.command != "translate"):
        ap.error("--limit precisa ser positivo e só pode ser usado com translate")
    try:
        cfg = load_config(args.config)
        e = cfg.get("engine", {})
        mbloc.set_token_style(e.get("token_style",
                                    "xnx" if e.get("backend", "translators") == "argos" else "math"))
        return {"scan": cmd_scan, "translate": cmd_translate, "check": cmd_check,
                "glossary": cmd_glossary}[args.command](cfg, args) or 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"Dependência ausente: {exc}. Instale o pacote do backend configurado (veja o README).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
