"""Tabelas de metadados da coleta (geradas a partir do manifesto) e resumo em Markdown para o relatório."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from catalogo import BASES, faixa_periodo
from util import Manifesto, Pastas, agora_iso, fmt_bytes

CAMPOS_ARQUIVOS = [
    "base", "periodo", "tabela", "status", "data_download", "url", "url_final", "http_status",
    "tentativas", "retomado", "bytes_zip", "sha256_zip", "tempo_download_s", "membro", "encoding",
    "delimitador", "n_colunas", "linhas_fisicas", "n_linhas", "n_rejeitadas", "bytes_csv", "bytes_csv_utf8",
    "bytes_parquet", "n_arquivos_parquet", "particoes", "anos_particao", "coluna_uf",
    "colunas_removidas", "filtro_ufs", "tempo_extracao_s", "tempo_conversao_s", "avisos", "erro", "colunas",
]


def _lista(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " | ".join(str(x) for x in v)
    return str(v)


def linhas_arquivos(manifesto: Manifesto) -> list[dict]:
    """Uma linha por base × período × tabela (último registro de cada base × período)."""
    saida = []
    for (base, periodo), r in sorted(manifesto.ultimos().items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        comum = {
            "base": base, "periodo": periodo,
            "status": r.get("status"), "data_download": r.get("data_download"), "url": r.get("url"),
            "url_final": r.get("url_final"), "http_status": r.get("http_status"), "tentativas": r.get("tentativas"),
            "retomado": r.get("retomado"), "bytes_zip": r.get("bytes"), "sha256_zip": r.get("sha256"),
            "tempo_download_s": r.get("segundos"), "filtro_ufs": _lista((r.get("opcoes") or {}).get("ufs")),
            "erro": r.get("erro"),
        }
        tabelas = r.get("tabelas") or []
        if not tabelas:
            saida.append({**comum, "tabela": ""})
            continue
        for t in tabelas:
            linha = dict(comum)
            for k in ("tabela", "membro", "encoding", "delimitador", "n_colunas", "linhas_fisicas", "n_linhas",
                      "n_rejeitadas", "bytes_csv", "bytes_csv_utf8", "bytes_parquet", "n_arquivos_parquet",
                      "coluna_uf", "tempo_extracao_s", "tempo_conversao_s"):
                linha[k] = t.get(k)
            linha["particoes"] = _lista(t.get("particoes"))
            linha["anos_particao"] = _lista(t.get("anos_particao"))
            linha["colunas_removidas"] = _lista(t.get("colunas_removidas"))
            linha["avisos"] = _lista(t.get("avisos"))
            linha["colunas"] = _lista(t.get("colunas"))
            saida.append(linha)
    return saida


def _soma(linhas: list[dict], campo: str) -> int:
    return sum(int(l.get(campo) or 0) for l in linhas)


def resumo_bases(linhas: list[dict]) -> list[dict]:
    por_base: dict[str, list[dict]] = defaultdict(list)
    for l in linhas:
        por_base[l["base"]].append(l)
    saida = []
    for chave in [b for b in BASES if b in por_base] + [b for b in por_base if b not in BASES]:
        ls = por_base[chave]
        ok = [l for l in ls if l["status"] == "ok"]
        periodos_ok = sorted({l["periodo"] for l in ok})
        # bytes_zip se repete em cada tabela do mesmo período: conta uma vez por período
        zip_por_periodo = {l["periodo"]: int(l.get("bytes_zip") or 0) for l in ok}
        b_zip = sum(zip_por_periodo.values())
        b_csv, b_pq = _soma(ok, "bytes_csv"), _soma(ok, "bytes_parquet")
        saida.append({
            "base": chave,
            "nome": BASES[chave].nome if chave in BASES else chave,
            "periodos_ok": len(periodos_ok),
            "periodos_indisponiveis": len({l["periodo"] for l in ls if l["status"] == "indisponivel"}),
            "periodos_com_erro": len({l["periodo"] for l in ls if l["status"] in ("erro", "interrompido")}),
            "primeiro_periodo": periodos_ok[0] if periodos_ok else "",
            "ultimo_periodo": periodos_ok[-1] if periodos_ok else "",
            "tabelas": len({l["tabela"] for l in ok if l.get("tabela")}),
            "n_linhas": _soma(ok, "n_linhas"),
            "n_rejeitadas": _soma(ok, "n_rejeitadas"),
            "bytes_zip": b_zip,
            "bytes_csv": b_csv,
            "bytes_parquet": b_pq,
            "reducao_csv_para_parquet_pct": round(100 * (1 - b_pq / b_csv), 1) if b_csv else None,
            "csv_vezes_maior_que_parquet": round(b_csv / b_pq, 2) if b_pq else None,
            "reducao_zip_para_parquet_pct": round(100 * (1 - b_pq / b_zip), 1) if b_zip else None,
            "tentativas_extras": sum(max(0, int(l.get("tentativas") or 1) - 1) for l in
                                     {l["periodo"]: l for l in ls}.values()),
        })
    return saida


def layouts(linhas: list[dict]) -> list[dict]:
    """Assinaturas de colunas por base/tabela: mostra quando o layout do arquivo mudou entre períodos."""
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for l in linhas:
        if l["status"] == "ok" and l.get("tabela"):
            grupos[(l["base"], l["tabela"])].append(l)
    saida = []
    for (base, tabela), ls in sorted(grupos.items()):
        ls = sorted(ls, key=lambda l: faixa_periodo(l["periodo"])[0] + l["periodo"])
        versoes: list[dict] = []
        for l in ls:
            cols = l.get("colunas") or ""
            if versoes and versoes[-1]["colunas"] == cols:
                versoes[-1]["ultimo_periodo"] = l["periodo"]
                versoes[-1]["n_periodos"] += 1
                continue
            anterior = set(versoes[-1]["colunas"].split(" | ")) if versoes else set()
            atual = set(cols.split(" | "))
            versoes.append({
                "base": base, "tabela": tabela, "versao_layout": len(versoes) + 1,
                "primeiro_periodo": l["periodo"], "ultimo_periodo": l["periodo"], "n_periodos": 1,
                "n_colunas": l.get("n_colunas"),
                "colunas_novas": " | ".join(sorted(atual - anterior)) if versoes else "",
                "colunas_retiradas": " | ".join(sorted(anterior - atual)) if versoes else "",
                "encoding": l.get("encoding"), "colunas": cols,
            })
        saida.extend(versoes)
    return saida


def gravar_csv(caminho: Path, linhas: list[dict], campos: list[str] | None = None) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    campos = campos or (list(linhas[0].keys()) if linhas else ["vazio"])
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for l in linhas:
            w.writerow({k: _lista(v) if isinstance(v, (list, tuple)) else v for k, v in l.items()})


def ler_csv(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _num(n) -> str:
    return f"{int(n):,}".replace(",", ".") if n not in (None, "") else "-"


def atualizar(pastas: Pastas) -> dict:
    """Regrava todas as tabelas de metadados e o resumo em Markdown. Devolve os totais."""
    manifesto = Manifesto(pastas.metadados / "manifesto_coleta.jsonl")
    linhas = linhas_arquivos(manifesto)
    gravar_csv(pastas.metadados / "coleta_arquivos.csv", linhas, CAMPOS_ARQUIVOS)
    resumo = resumo_bases(linhas)
    gravar_csv(pastas.metadados / "coleta_resumo_bases.csv", resumo)
    lay = layouts(linhas)
    gravar_csv(pastas.metadados / "layouts_por_periodo.csv", lay)
    totais = {
        "arquivos_baixados": sum(r["periodos_ok"] for r in resumo),
        "tabelas_parquet": len({(l["base"], l["periodo"], l["tabela"]) for l in linhas if l["status"] == "ok"}),
        "n_linhas": sum(r["n_linhas"] for r in resumo),
        "n_rejeitadas": sum(r["n_rejeitadas"] for r in resumo),
        "bytes_zip": sum(r["bytes_zip"] for r in resumo),
        "bytes_csv": sum(r["bytes_csv"] for r in resumo),
        "bytes_parquet": sum(r["bytes_parquet"] for r in resumo),
    }
    _markdown(pastas, resumo, lay, linhas, totais)
    return totais


def _markdown(pastas: Pastas, resumo: list[dict], lay: list[dict], linhas: list[dict], totais: dict) -> None:
    disp = {l["base"]: l for l in ler_csv(pastas.metadados / "periodos_disponiveis.csv")}
    bench = ler_csv(pastas.metadados / "benchmark_formatos.csv")
    out = ["# Resumo da coleta\n", f"Gerado em {agora_iso()} a partir de `data/metadados/manifesto_coleta.jsonl`.\n"]
    red = (100 * (1 - totais["bytes_parquet"] / totais["bytes_csv"])) if totais["bytes_csv"] else None
    out.append("## Volume total\n")
    out.append("| Métrica | Valor |\n|---|---|")
    out.append(f"| Arquivos baixados (base × período) | {_num(totais['arquivos_baixados'])} |")
    out.append(f"| Tabelas convertidas (base × período × tabela) | {_num(totais['tabelas_parquet'])} |")
    out.append(f"| Linhas em Parquet | {_num(totais['n_linhas'])} |")
    out.append(f"| Linhas malformadas (fora do Parquet) | {_num(totais['n_rejeitadas'])} |")
    out.append(f"| Tamanho baixado (ZIP) | {fmt_bytes(totais['bytes_zip'])} |")
    out.append(f"| Tamanho dos CSV descompactados | {fmt_bytes(totais['bytes_csv'])} |")
    out.append(f"| Tamanho em Parquet (zstd) | {fmt_bytes(totais['bytes_parquet'])} |")
    if red is not None:
        vezes = totais["bytes_csv"] / max(1, totais["bytes_parquet"])
        out.append(f"| Redução CSV → Parquet | {red:.1f}% ({vezes:.1f}× menor) |".replace(".", ","))
    out.append("")
    out.append("## Por base\n")
    out.append("| Base | Períodos | Primeiro | Último | Linhas | ZIP | CSV | Parquet | Redução CSV→Parquet | Indisp. | Erros |")
    out.append("|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in resumo:
        reducao = (f"{r['reducao_csv_para_parquet_pct']:.1f}%".replace(".", ",")
                   if r["reducao_csv_para_parquet_pct"] is not None else "-")
        out.append(f"| {r['nome']} | {r['periodos_ok']} | {r['primeiro_periodo']} | {r['ultimo_periodo']} | "
                   f"{_num(r['n_linhas'])} | {fmt_bytes(r['bytes_zip'])} | {fmt_bytes(r['bytes_csv'])} | "
                   f"{fmt_bytes(r['bytes_parquet'])} | {reducao} | {r['periodos_indisponiveis']} | "
                   f"{r['periodos_com_erro']} |")
    out.append("")
    if disp:
        out.append("## Lacunas nas séries publicadas\n")
        out.append("Meses que faltam entre o primeiro e o último arquivo listado no Portal "
                   "(`data/metadados/periodos_disponiveis.csv`).\n")
        tem = False
        for base, d in disp.items():
            if d.get("lacunas"):
                tem = True
                out.append(f"- **{BASES[base].nome if base in BASES else base}**: {d['lacunas']}")
        if not tem:
            out.append("- Nenhuma lacuna nas bases selecionadas.")
        out.append("")
    mudancas = [l for l in lay if l["versao_layout"] > 1]
    out.append("## Mudanças de layout entre períodos\n")
    if mudancas:
        out.append("| Base | Tabela | Versão | A partir de | Colunas novas | Colunas retiradas |")
        out.append("|---|---|---:|---|---|---|")
        for l in mudancas:
            out.append(f"| {l['base']} | {l['tabela']} | {l['versao_layout']} | {l['primeiro_periodo']} | "
                       f"{l['colunas_novas'] or '-'} | {l['colunas_retiradas'] or '-'} |")
    else:
        out.append("Nenhuma mudança de colunas detectada nos arquivos convertidos.")
    out.append("")
    problemas = [l for l in linhas if l["status"] in ("erro", "interrompido", "indisponivel")]
    out.append("## Arquivos indisponíveis ou com erro\n")
    if problemas:
        out.append("| Base | Período | Status | Detalhe |\n|---|---|---|---|")
        for l in problemas:
            out.append(f"| {l['base']} | {l['periodo']} | {l['status']} | {(l.get('erro') or '').replace('|', '/')} |")
    else:
        out.append("Nenhum.")
    out.append("")
    avisos = [l for l in linhas if l["status"] == "ok" and l.get("avisos")]
    if avisos:
        out.append("## Avisos de qualidade na leitura\n")
        out.append("| Base | Período | Tabela | Aviso |\n|---|---|---|---|")
        for l in avisos[:200]:
            out.append(f"| {l['base']} | {l['periodo']} | {l['tabela']} | {l['avisos'].replace('|', '/')} |")
        if len(avisos) > 200:
            out.append(f"\n… mais {len(avisos) - 200} linhas em `data/metadados/coleta_arquivos.csv`.")
        out.append("")
    if bench:
        out.append("## CSV × Parquet: tamanho e tempo de leitura\n")
        out.append("| Base | Tabela | Período | CSV | Parquet | pandas CSV (s) | pandas Parquet (s) | "
                   "DuckDB CSV (s) | DuckDB Parquet (s) |")
        out.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
        for b in bench:
            out.append(f"| {b['base']} | {b['tabela']} | {b['periodo']} | {fmt_bytes(float(b['bytes_csv'] or 0))} | "
                       f"{fmt_bytes(float(b['bytes_parquet'] or 0))} | {b.get('pandas_csv_s') or '-'} | "
                       f"{b.get('pandas_parquet_s') or '-'} | {b.get('duckdb_csv_agrupar_s') or '-'} | "
                       f"{b.get('duckdb_parquet_agrupar_s') or '-'} |")
        out.append("")
    pastas.reports.mkdir(parents=True, exist_ok=True)
    (pastas.reports / "coleta_resumo.md").write_text("\n".join(out) + "\n", encoding="utf-8")
