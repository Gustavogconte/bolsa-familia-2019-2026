"""Dicionário de dados: junta o dicionário oficial do Portal (docs/dicionario_oficial.csv) com o perfil
observado nos arquivos já convertidos (tipo inferido, % vazios, cardinalidade e domínio observado).

Saídas: docs/dicionario_dados.csv e docs/dicionario_dados.md.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

import duckdb

from catalogo import BASES, Base, faixa_periodo
from metadados import gravar_csv
from conversao import RE_NOME_PESSOA
from util import Manifesto, Pastas, caminho_sql, ident_sql, padronizar_nome

log = logging.getLogger("coleta")

PADROES = {
    "cpf_mascarado": r"^\*{3}\.[0-9]{3}\.[0-9]{3}-\*{2}$",
    "ano_mes": r"^(19|20)[0-9]{2}(0[1-9]|1[0-2])$",
    "aaaammdd": r"^(19|20)[0-9]{2}(0[1-9]|1[0-2])[0-3][0-9]$",
    "data_br": r"^[0-3][0-9]/[01][0-9]/[12][0-9]{3}$",
    "data_hora_br": r"^[0-3][0-9]/[01][0-9]/[12][0-9]{3} [0-2][0-9]:[0-5][0-9](:[0-5][0-9])?$",
    "data_iso": r"^[12][0-9]{3}-[01][0-9]-[0-3][0-9]",
    "decimal_virgula": r"^-?[0-9]{1,3}(\.[0-9]{3})*,[0-9]+$|^-?[0-9]+,[0-9]+$",
    "decimal_ponto": r"^-?[0-9]+\.[0-9]+$",
    "inteiro": r"^-?[0-9]+$",
}


def carregar_oficial(caminho: Path) -> list[dict]:
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


_PALAVRAS_VAZIAS = {"do", "da", "de", "dos", "das", "e", "o", "a"}
_SINONIMOS = {"unidade_gestora": "ug", "orgao_subordinado": "orgao"}


def _chave_solta(nome: str) -> str:
    """'codigo_do_orgao_superior' e 'codigo_orgao_superior' viram a mesma chave (sem preposições, com sinônimos)."""
    for longo, curto in _SINONIMOS.items():
        nome = nome.replace(longo, curto)
    return "_".join(t for t in nome.split("_") if t not in _PALAVRAS_VAZIAS)


def _indice_oficial(oficial: list[dict]) -> dict[tuple[str, str, str], dict]:
    """Índice por (base, tabela, nome padronizado); planos B: qualquer tabela da base e nome 'solto'."""
    idx = {}
    for o in oficial:
        nomes = {padronizar_nome(o["campo_oficial"])} | {a.strip() for a in (o.get("aliases") or "").split("|") if a.strip()}
        for n in nomes:
            idx.setdefault((o["base"], o["tabela"], n), o)
            idx.setdefault((o["base"], "?", n), o)
            idx.setdefault((o["base"], o["tabela"], "~" + _chave_solta(n)), o)
            idx.setdefault((o["base"], "?", "~" + _chave_solta(n)), o)
    return idx


def _num_br(x) -> str:
    if x is None:
        return "?"
    x = float(x)
    if x.is_integer():
        return f"{int(x):,}".replace(",", ".")
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _tipo(st: dict) -> tuple[str, str]:
    nn = st["n"] - st["vazios"]
    if nn <= 0:
        return "vazia", "sem valores na amostra"

    def frac(k):
        return st[k] / nn

    if frac("cpf_mascarado") >= 0.99:
        return "texto (CPF mascarado)", "formato ***.NNN.NNN-**"
    if frac("ano_mes") >= 0.99:
        return "ano-mês AAAAMM", f"{st['minimo']} a {st['maximo']}"
    if frac("aaaammdd") >= 0.99:
        return "data AAAAMMDD", f"{st['minimo']} a {st['maximo']}"
    if frac("data_br") >= 0.99:
        return "data DD/MM/AAAA", "datas em texto; converter na limpeza"
    if frac("data_hora_br") >= 0.99:
        return "data e hora DD/MM/AAAA HH:MM", "datas em texto; converter na limpeza"
    if frac("data_iso") >= 0.99:
        return "data AAAA-MM-DD", f"{st['minimo']} a {st['maximo']}"
    if (st["decimal_virgula"] + st["inteiro"]) / nn >= 0.99 and st["decimal_virgula"] > 0:
        return "decimal (vírgula decimal)", f"{_num_br(st.get('num_min'))} a {_num_br(st.get('num_max'))}"
    if frac("decimal_ponto") + frac("inteiro") >= 0.99 and st["decimal_ponto"] > 0:
        return "decimal (ponto decimal)", f"{_num_br(st.get('num_min'))} a {_num_br(st.get('num_max'))}"
    if frac("inteiro") >= 0.99:
        if st["min_len"] == st["max_len"] and st["max_len"] >= 4:
            return f"código numérico ({st['max_len']} dígitos)", f"ex.: {st['minimo']}"
        return "inteiro", f"{_num_br(st.get('num_min'))} a {_num_br(st.get('num_max'))}"
    if st["distintos"] <= 60:
        return "categórica (texto)", ""
    return "texto livre", f"comprimento {st['min_len']} a {st['max_len']}"


def pessoal(norm: str) -> bool:
    """Colunas com nome, CPF, NIS ou RGP de pessoas: o dicionário não mostra exemplos de valores."""
    return bool(RE_NOME_PESSOA.match(norm)) or norm.startswith(("cpf", "nis", "rgp")) or \
        any(x in norm for x in ("_cpf", "cpf_", "_nis", "nis_", "rgp_"))


def perfil(arquivos: list[Path], amostra: int) -> list[dict]:
    con = duckdb.connect()
    lista = "[" + ", ".join(caminho_sql(a) for a in arquivos) + "]"
    con.execute(f"CREATE TEMP TABLE amostra AS SELECT * FROM read_parquet({lista}, union_by_name=true, "
                f"hive_partitioning=false) USING SAMPLE reservoir({int(amostra)} ROWS) REPEATABLE (42)")
    colunas = [r[0] for r in con.execute("DESCRIBE amostra").fetchall()]
    saida = []
    for c in colunas:
        if c in ("_periodo_arquivo", "_arquivo_origem"):
            continue
        q = ident_sql(c)
        v = f"trim(CAST({q} AS VARCHAR))"
        filtros = ", ".join(f"count(*) FILTER (WHERE regexp_matches({v}, '{p}')) AS {k}" for k, p in PADROES.items())
        cur = con.execute(f"""
            SELECT count(*) AS n, count(*) FILTER (WHERE {q} IS NULL OR {v} = '') AS vazios,
                   approx_count_distinct({q}) AS distintos, {filtros},
                   min(length({v})) FILTER (WHERE {v} <> '') AS min_len,
                   max(length({v})) AS max_len,
                   min({v}) FILTER (WHERE {v} <> '') AS minimo, max({v}) AS maximo,
                   min(TRY_CAST(replace(replace({v}, '.', ''), ',', '.') AS DOUBLE)) AS num_min_br,
                   max(TRY_CAST(replace(replace({v}, '.', ''), ',', '.') AS DOUBLE)) AS num_max_br,
                   min(TRY_CAST({v} AS DOUBLE)) AS num_min_pt, max(TRY_CAST({v} AS DOUBLE)) AS num_max_pt
            FROM amostra""")
        st = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        st = {k: (0 if v_ is None else v_) if k not in ("minimo", "maximo") else v_ for k, v_ in st.items()}
        virgula = st["decimal_virgula"] > 0
        st["num_min"] = st["num_min_br"] if virgula else st["num_min_pt"]
        st["num_max"] = st["num_max_br"] if virgula else st["num_max_pt"]
        tipo, dominio = _tipo(st)
        top = con.execute(f"SELECT {v} AS valor, count(*) AS n FROM amostra WHERE {v} <> '' "
                          f"GROUP BY 1 ORDER BY 2 DESC LIMIT 12").fetchall()
        nn = max(1, st["n"] - st["vazios"])
        privado = pessoal(padronizar_nome(c))
        if tipo.startswith("categórica") and not privado:
            dominio = "; ".join(f"{val} (" + f"{100 * k / nn:.1f}".replace(".", ",") + "%)" for val, k in top)
        if privado:
            dominio = dominio.split("ex.:")[0].strip() if "ex.:" in dominio else (
                dominio if tipo.startswith(("texto (CPF", "código")) else "")
        saida.append({
            "coluna_original": c, "tipo_inferido": tipo, "dominio_observado": dominio,
            "pct_vazios_amostra": round(100 * st["vazios"] / max(1, st["n"]), 2),
            "n_distintos_amostra": int(st["distintos"]),
            "exemplos": "(omitido: dado pessoal)" if privado else "; ".join(str(val) for val, _ in top[:3]),
            "linhas_amostra": int(st["n"]),
        })
    con.close()
    return saida


def executar(bases: list[Base], pastas: Pastas, amostra: int = 500_000, n_periodos: int = 6) -> list[dict]:
    oficial = carregar_oficial(pastas.docs / "dicionario_oficial.csv")
    idx = _indice_oficial(oficial)
    ultimos = Manifesto(pastas.metadados / "manifesto_coleta.jsonl").ultimos()
    por_tabela: dict[tuple[str, str], list[tuple[str, list[str]]]] = defaultdict(list)
    for (b, p), r in ultimos.items():
        if r.get("status") != "ok":
            continue
        for t in r.get("tabelas") or []:
            por_tabela[(b, t["tabela"])].append((p, t.get("arquivos_parquet", [])))
    linhas = []
    cobertos: set[tuple[str, str, str]] = set()
    for base in bases:
        tabelas = sorted(t for (b, t) in por_tabela if b == base.chave)
        for tabela in tabelas:
            # Amostra os n_periodos MAIORES (em bytes de Parquet), não os mais recentes: um mês com poucas
            # linhas distorceria o domínio observado.
            def tamanho(x):
                return sum((pastas.raiz / a).stat().st_size for a in x[1] if (pastas.raiz / a).exists())

            maiores = sorted(por_tabela[(base.chave, tabela)], key=tamanho)[-n_periodos:]
            periodos = sorted(maiores, key=lambda x: faixa_periodo(x[0])[0])
            arquivos = [pastas.raiz / a for _, arqs in periodos for a in arqs if (pastas.raiz / a).exists()]
            if not arquivos:
                continue
            log.info("dicionário: perfil de %s/%s (%d arquivos, amostra de até %d linhas)",
                     base.chave, tabela, len(arquivos), amostra)
            for p in perfil(arquivos, amostra):
                norm = padronizar_nome(p["coluna_original"])
                solta = "~" + _chave_solta(norm)
                o = (idx.get((base.chave, tabela, norm)) or idx.get((base.chave, "*", norm))
                     or idx.get((base.chave, "?", norm)) or idx.get((base.chave, tabela, solta))
                     or idx.get((base.chave, "*", solta)) or idx.get((base.chave, "?", solta)))
                if o:
                    cobertos.add((o["base"], o["tabela"], o["campo_oficial"]))
                linhas.append({
                    "base": base.chave, "tabela": tabela, "coluna_original": p["coluna_original"],
                    "coluna_padronizada": norm,
                    "campo_no_dicionario_oficial": o["campo_oficial"] if o else "",
                    "descricao": o["descricao"] if o else "(não consta no dicionário oficial: verificar)",
                    "tipo_esperado": o["tipo_esperado"] if o else "",
                    "tipo_inferido": p["tipo_inferido"],
                    "dominio_esperado": o["dominio"] if o else "",
                    "dominio_observado": p["dominio_observado"],
                    "pct_vazios_amostra": p["pct_vazios_amostra"],
                    "n_distintos_amostra": p["n_distintos_amostra"],
                    "exemplos": p["exemplos"],
                    "periodos_amostrados": f"{periodos[0][0]} a {periodos[-1][0]}",
                    "fonte": o["fonte"] if o else BASES[base.chave].url_dicionario,
                })
    # campos do dicionário oficial que não apareceram nos arquivos (para conferir na EDA)
    for o in oficial:
        if o["base"] in {b.chave for b in bases} and (o["base"], o["tabela"], o["campo_oficial"]) not in cobertos:
            observadas = sorted({l["tabela"] for l in linhas if l["base"] == o["base"]})
            if not observadas:
                continue
            tabela_ref = observadas[0] if o["tabela"] == "*" else o["tabela"]
            linhas.append({"base": o["base"], "tabela": tabela_ref, "coluna_original": "",
                           "coluna_padronizada": padronizar_nome(o["campo_oficial"]),
                           "campo_no_dicionario_oficial": o["campo_oficial"], "descricao": o["descricao"],
                           "tipo_esperado": o["tipo_esperado"], "tipo_inferido": "(não encontrado nos arquivos)",
                           "dominio_esperado": o["dominio"], "fonte": o["fonte"]})
    campos = ["base", "tabela", "coluna_original", "coluna_padronizada", "campo_no_dicionario_oficial", "descricao",
              "tipo_esperado", "tipo_inferido", "dominio_esperado", "dominio_observado", "pct_vazios_amostra",
              "n_distintos_amostra", "exemplos", "periodos_amostrados", "fonte"]
    # preserva o que já existe das bases que não foram perfiladas agora
    feitas = {b.chave for b in bases}
    anteriores = []
    destino = pastas.docs / "dicionario_dados.csv"
    if destino.exists():
        with open(destino, encoding="utf-8-sig", newline="") as f:
            anteriores = [l for l in csv.DictReader(f) if l.get("base") not in feitas]
    ordem = {chave: i for i, chave in enumerate(BASES)}
    todas = sorted(anteriores + linhas, key=lambda l: (ordem.get(l["base"], 999), l["tabela"]))
    gravar_csv(destino, todas, campos)
    _markdown(pastas.docs / "dicionario_dados.md", todas)
    markdown_oficial(pastas)
    return linhas


def markdown_oficial(pastas: Pastas) -> Path:
    """docs/dicionario_oficial.md: versão legível do dicionário oficial (antes mesmo de baixar os dados)."""
    oficial = carregar_oficial(pastas.docs / "dicionario_oficial.csv")
    out = ["# Dicionário de dados oficial (Portal da Transparência)\n",
           "Campos de cada base segundo os dicionários do Portal (links em `docs/fontes.md`), com descrição em "
           "português, tipo esperado e domínio. A coluna *nome padronizado* é o snake_case sem acentos que a "
           "limpeza vai usar; como os cabeçalhos dos CSV nem sempre repetem o nome do dicionário (ex.: "
           "\"Ano/Mês Competência\" aparece como \"MÊS COMPETÊNCIA\"), ela mostra o nome mais provável no arquivo. "
           "Depois da coleta, `python src/coleta.py dicionario` confere cada campo contra os arquivos reais e gera "
           "`docs/dicionario_dados.md` com os nomes definitivos, o tipo inferido e o domínio observado.\n"]
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in oficial:
        grupos[(o["base"], o["tabela"])].append(o)
    anotadas: set[str] = set()
    for (base, tabela), ls in grupos.items():
        nome = BASES[base].nome if base in BASES else base
        sufixo = "" if tabela == "*" else f" — tabela `{tabela}`"
        out.append(f"\n## {nome}{sufixo}\n")
        if base in BASES and BASES[base].observacao and base not in anotadas:
            out.append(f"> {BASES[base].observacao}\n")
            anotadas.add(base)
        out.append("| Campo (dicionário oficial) | Nome padronizado | Descrição | Tipo esperado | Domínio |")
        out.append("|---|---|---|---|---|")
        for o in ls:
            aliases = [a for a in (o.get("aliases") or "").split("|") if a]
            padronizado = aliases[0] if aliases else padronizar_nome(o["campo_oficial"])
            out.append(f"| {_md(o['campo_oficial'])} | `{padronizado}` | {_md(o['descricao'])} | "
                       f"{_md(o['tipo_esperado'])} | {_md(o['dominio'])} |")
    destino = pastas.docs / "dicionario_oficial.md"
    destino.write_text("\n".join(out) + "\n", encoding="utf-8")
    return destino


def _md(v) -> str:
    return str(v if v is not None else "").replace("|", "/").replace("\n", " ")


def _markdown(caminho: Path, linhas: list[dict]) -> None:
    out = ["# Dicionário de dados\n",
           "Gerado por `python src/coleta.py dicionario`: descrição e tipo esperado vêm do dicionário oficial do "
           "Portal (`docs/dicionario_oficial.csv`); tipo inferido, % de vazios e domínio observado vêm de uma "
           "amostra dos arquivos em `data/bronze`. Na camada bronze tudo está como texto; a conversão de tipos "
           "é feita na limpeza.\n"]
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for l in linhas:
        grupos[(l["base"], l["tabela"])].append(l)
    for (base, tabela), ls in grupos.items():
        nome = BASES[base].nome if base in BASES else base
        out.append(f"\n## {nome} — `{tabela}`\n")
        out.append("| Coluna no arquivo | Nome padronizado | Descrição | Tipo esperado | Tipo inferido | "
                   "Domínio | % vazios |")
        out.append("|---|---|---|---|---|---|---:|")
        for l in ls:
            dominio = l.get("dominio_observado") or l.get("dominio_esperado") or ""
            out.append(f"| {_md(l['coluna_original']) or '—'} | `{_md(l['coluna_padronizada'])}` | "
                       f"{_md(l['descricao'])} | {_md(l['tipo_esperado'])} | {_md(l['tipo_inferido'])} | "
                       f"{_md(dominio)[:300]} | {_md(l.get('pct_vazios_amostra'))} |")
    caminho.write_text("\n".join(out) + "\n", encoding="utf-8")
