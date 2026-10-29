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
import re
import sys
import time
from collections import Counter
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

import mbloc
from translator import (ArgosBackend, Cache, OllamaBackend, RateLimiter,
                        TranslatorsBackend, Translator)

# Arquivos com nomes próprios: bons candidatos a glossário
NAME_FILES = ("troops.csv", "parties.csv", "factions.csv", "party_templates.csv", "item_kinds.csv")


# ---------------------------------------------------------------- config
def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_dir"] = Path(path).resolve().parent
    return cfg


def rel(cfg, p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else cfg["_dir"] / p


def load_glossary(cfg) -> dict[str, str]:
    g: dict[str, str] = {}
    gcfg = cfg.get("glossary", {})
    f = gcfg.get("file")
    if f and rel(cfg, f).exists():
        for line in rel(cfg, f).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            if "\t" in line:
                a, b = line.split("\t", 1)
            elif "=" in line:
                a, b = line.split("=", 1)
            else:
                continue
            g[a.strip()] = b.strip()
    f = gcfg.get("do_not_translate")
    if f and rel(cfg, f).exists():
        for line in rel(cfg, f).read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.startswith("#"):
                g[line.strip()] = line.strip()
    g.update(gcfg.get("terms", {}))
    return g


def paths(cfg):
    mod = Path(cfg["mod"]["path"])
    src = mod / "languages" / cfg["mod"].get("source_lang", "en")
    dst_lang = cfg["mod"].get("target_lang", "pt")
    if cfg["mod"].get("install", False):
        out = mod / "languages" / dst_lang
    else:
        out = rel(cfg, cfg["mod"].get("output", "saida")) / mod.name / "languages" / dst_lang
    return mod, src, out


def source_files(cfg, only: str | None = None) -> list[Path]:
    _, src, _ = paths(cfg)
    if not src.is_dir():
        sys.exit(f"Pasta de origem não encontrada: {src}\n"
                 "O mod precisa ter languages/en/*.csv (veja o README, seção 'Mod sem pasta en').")
    skip = {s.lower() for s in cfg["mod"].get("skip_files", [])}
    files = sorted(p for p in src.glob("*.csv") if p.name.lower() not in skip)
    if only:
        files = [p for p in files if p.name.lower() == only.lower()]
    return files


def skip_id_patterns(cfg):
    return [re.compile(p) for p in cfg["mod"].get("skip_ids", [])]


def make_backend(cfg, glossary):
    e = cfg.get("engine", {})
    kind = e.get("backend", "translators")
    src = cfg["mod"].get("source_lang", "en")
    dst = e.get("target_code", "pt")
    if kind == "ollama":
        o = e.get("ollama", {})
        return OllamaBackend(model=o.get("model", "qwen2.5:7b"), url=o.get("url", "http://localhost:11434"),
                             dst=o.get("target_name", "Brazilian Portuguese"),
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
                                  max_retries=t.get("max_retries", 4), max_chars=t.get("max_chars", 3000))
    sys.exit(f"backend desconhecido: {kind} (use ollama, argos ou translators)")


# ---------------------------------------------------------------- comandos
def cmd_scan(cfg, args):
    mod, src, out = paths(cfg)
    print(f"Mod:     {mod}\nOrigem:  {src}\nSaída:   {out}\n")
    total_lines = total_chars = 0
    for f in source_files(cfg):
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


def cmd_translate(cfg, args):
    _, _, out_dir = paths(cfg)
    glossary = load_glossary(cfg)
    cache = Cache(rel(cfg, cfg.get("cache", {}).get("file", "cache.sqlite")),
                  commit_every=cfg.get("cache", {}).get("commit_every", 1))
    tr = Translator(make_backend(cfg, glossary), cache, context=cfg["mod"].get("context", ""),
                    glossary=glossary, reuse_cache=cfg.get("cache", {}).get("reuse_cache_from", "auto") == "auto",
                    translate_branches=cfg["mod"].get("translate_gender_choices", True))
    skip_ids = skip_id_patterns(cfg)
    keep_existing = cfg["mod"].get("keep_existing", True)
    budget = args.limit
    t0 = time.monotonic()
    try:
        for f in source_files(cfg, args.file):
            lf = mbloc.read_csv(f)
            target = out_dir / f.name
            existing = {}
            if keep_existing and target.exists():
                existing = {e.key: e.text for e in mbloc.read_csv(target).entries if e.key}
            rows = [e for e in lf.entries if e.key]
            print(f"\n== {f.name} ({len(rows)} linhas)")
            result: list[tuple[str | None, str]] = []
            done = 0
            for e in lf.entries:
                if e.key is None:
                    result.append((None, e.raw)); continue
                old = existing.get(e.key)
                if old is not None and old != e.text:
                    result.append((e.key, old)); continue          # já traduzida antes
                if any(p.search(e.key) for p in skip_ids) or (budget is not None and budget <= 0):
                    result.append((e.key, e.text)); continue
                result.append((e.key, tr.translate(e.text)))
                done += 1
                if budget is not None:
                    budget -= 1
                if done % 25 == 0:
                    s = tr.stats
                    print(f"  {done}/{len(rows)}  ok={s['ok']} cache={s['cache']} B={s['planB']} "
                          f"C={s['planC']} falhas={s['fail']}  [{tr.b.engine_id}]", flush=True)
                    mbloc.write_csv(target, result + [(x.key, existing.get(x.key, x.text)) if x.key else (None, x.raw)
                                                      for x in lf.entries[len(result):]], lf.bom)
            mbloc.write_csv(target, result, lf.bom)
            if budget is not None and budget <= 0:
                print("\nLimite atingido (--limit)."); break
    except KeyboardInterrupt:
        print("\nInterrompido. Tudo que já foi traduzido está no cache — rode de novo para continuar.")
    finally:
        cache.close()
    s = tr.stats
    print(f"\nFim em {time.monotonic() - t0:.0f}s: ok={s['ok']} cache={s['cache']} planoB={s['planB']} "
          f"planoC={s['planC']} falhas={s['fail']} sem_texto={s['skip']}\nSaída: {out_dir}")


def check_pair(src_text: str, dst_text: str) -> list[str]:
    probs = []
    a, b = Counter(mbloc.markers(src_text)), Counter(mbloc.markers(dst_text))
    # ramos de {s1?a:b} / {sir/madam} podem ser traduzidos: compare só os fixos
    fixed = lambda c: Counter({k: v for k, v in c.items() if not mbloc.translatable_branches(k)})
    if fixed(a) != fixed(b):
        probs.append(f"códigos diferentes: {sorted(fixed(a).elements())} -> {sorted(fixed(b).elements())}")
    if sum(1 for k in a.elements() if mbloc.translatable_branches(k)) != \
       sum(1 for k in b.elements() if mbloc.translatable_branches(k)):
        probs.append("escolha de gênero/condição sumiu ou duplicou")
    if "|" in dst_text:
        probs.append("contém |")
    if ("⟦" in dst_text or "⟧" in dst_text
            or (mbloc.TOKEN_RE.search(dst_text) and not mbloc.TOKEN_RE.search(src_text))):
        probs.append("sobrou marcador de código")
    return probs


def cmd_check(cfg, args):
    _, _, out_dir = paths(cfg)
    bad = untranslated = total = 0
    for f in source_files(cfg):
        target = out_dir / f.name
        if not target.exists():
            print(f"  [faltando] {f.name}"); continue
        src = {e.key: e.text for e in mbloc.read_csv(f).entries if e.key}
        dst = {e.key: e.text for e in mbloc.read_csv(target).entries if e.key}
        for k in src.keys() - dst.keys():
            print(f"  {f.name}: id ausente na tradução: {k}"); bad += 1
        for k, v in src.items():
            if k not in dst:
                continue
            total += 1
            if dst[k] == v and mbloc.has_letters(mbloc.MARKER_RE.sub("", v)):
                untranslated += 1
            for p in check_pair(v, dst[k]):
                print(f"  {f.name}:{k}: {p}"); bad += 1
    print(f"\n{total} linhas verificadas, {bad} problema(s), {untranslated} ainda em inglês.")
    return 1 if bad else 0


def cmd_glossary(cfg, args):
    counts: Counter[str] = Counter()
    for f in source_files(cfg):
        if f.name.lower() not in NAME_FILES:
            continue
        for e in mbloc.read_csv(f).entries:
            if e.key and not e.key.endswith("_pl"):
                t = mbloc.MARKER_RE.sub("", e.text).strip()
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
    ap.add_argument("--file", help="traduz só este arquivo, ex.: dialogs.csv")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    e = cfg.get("engine", {})
    mbloc.set_token_style(e.get("token_style",
                                "xnx" if e.get("backend", "translators") == "argos" else "math"))
    return {"scan": cmd_scan, "translate": cmd_translate, "check": cmd_check,
            "glossary": cmd_glossary}[args.command](cfg, args) or 0


if __name__ == "__main__":
    sys.exit(main())
