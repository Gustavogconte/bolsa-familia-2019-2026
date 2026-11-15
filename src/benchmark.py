"""Comparação CSV × Parquet: tamanho em disco e tempo de leitura (pandas e DuckDB).

Para cada base escolhe um período já convertido (o mais recente cujo CSV cabe no limite de tamanho),
extrai o CSV original de dentro do ZIP e mede:
- pandas: ler o arquivo inteiro como DataFrame (CSV latin-1 × Parquet);
- DuckDB: contar linhas e agrupar por uma coluna (CSV × Parquet).
O ZIP é reaproveitado de data/_zips/ (opção --manter-zip da coleta) ou baixado de novo só para isso.
"""

from __future__ import annotations

import logging
import shutil
import time
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

import portal
from catalogo import Base
from conversao import _MembroZip, membros_csv
from util import Manifesto, Pastas, caminho_sql, fmt_bytes, ident_sql, literal_sql, padronizar_nome

log = logging.getLogger("coleta")


def _tempo(func) -> tuple[float, object]:
    t0 = time.perf_counter()
    r = func()
    return round(time.perf_counter() - t0, 3), r


def _ler_csv_duckdb(csv: Path, t: dict, encoding: str) -> str:
    """Mesmas opções de leitura usadas na conversão (ver conversao.converter_csv)."""
    colspec = "{" + ", ".join(f"{literal_sql(c)}: 'VARCHAR'" for c in t["colunas"]) + "}"
    pad = bool(t.get("null_padding"))
    return (f"read_csv({caminho_sql(csv)}, delim={literal_sql(t['delimitador'])}, quote='\"', escape='\"', "
            f"header=true, auto_detect=false, columns={colspec}, null_padding={'true' if pad else 'false'}, "
            f"parallel={'false' if pad else 'true'}, store_rejects=true, encoding={literal_sql(encoding)}, "
            f"max_line_size=33554432)")


def executar(bases: list[Base], pastas: Pastas, limite_mb: int = 400) -> list[dict]:
    ultimos = Manifesto(pastas.metadados / "manifesto_coleta.jsonl").ultimos()
    limite = limite_mb * 1024 * 1024
    pasta_tmp = pastas.tmp / "benchmark"
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    resultados = []
    for base in bases:
        regs = [r for (b, _), r in ultimos.items() if b == base.chave and r.get("status") == "ok" and r.get("tabelas")]
        if not regs:
            log.info("benchmark: %s ainda não tem período convertido; pulando", base.chave)
            continue

        def maior(r):
            return max(int(t.get("bytes_csv") or 0) for t in r["tabelas"])

        # O período medido é o MAIOR que cabe no limite (não o mais recente); se nenhum cabe, mede o menor.
        cabem = [r for r in regs if maior(r) <= limite]
        reg = max(cabem, key=maior) if cabem else min(regs, key=maior)
        periodo = reg["periodo"]
        guardado = pastas.zips / f"{base.chave}_{periodo}.zip"
        baixado_agora = False
        if guardado.exists():
            caminho, tipo = guardado, "zip" if zipfile.is_zipfile(guardado) else "csv"
        else:
            log.info("benchmark: baixando de novo %s %s só para medir a leitura do CSV", base.chave, periodo)
            res = portal.baixar(base, periodo, pasta_tmp)
            if res.status != "ok":
                log.warning("benchmark: não consegui baixar %s %s (%s)", base.chave, periodo, res.erro)
                continue
            caminho, tipo, baixado_agora = res.caminho, res.tipo, True
        membros = {nome: info for info, nome in membros_csv(caminho)} if tipo == "zip" else {}
        for t in reg["tabelas"]:
            try:
                linha = _medir(base, periodo, reg, t, caminho, tipo, membros, pastas, pasta_tmp, limite)
            except Exception as e:  # noqa: BLE001
                log.warning("benchmark %s %s [%s]: não deu para medir (%s: %s)", base.chave, periodo,
                            t.get("tabela"), type(e).__name__, str(e).splitlines()[0][:200])
                continue
            if linha:
                resultados.append(linha)
        if baixado_agora:
            Path(caminho).unlink(missing_ok=True)
    return resultados


def _medir(base, periodo, reg, t, caminho, tipo, membros, pastas, pasta_tmp, limite) -> dict | None:
    arquivos_pq = [pastas.raiz / a for a in t.get("arquivos_parquet", []) if (pastas.raiz / a).exists()]
    if not arquivos_pq:
        return None
    csv_tmp = pasta_tmp / f"{base.chave}_{t['tabela']}_{periodo}.csv"
    if tipo == "zip":
        info = membros.get(t["membro"])
        if info is None:
            return None
        with _MembroZip(caminho, info) as src, open(csv_tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
    else:
        shutil.copyfile(caminho, csv_tmp)
    cp1252 = str(t.get("encoding", "")).startswith("cp1252")
    enc_py, enc_duck = ("cp1252", "latin-1") if cp1252 else ("utf-8", "utf-8")
    b_csv, b_pq = csv_tmp.stat().st_size, sum(p.stat().st_size for p in arquivos_pq)
    linha = {"base": base.chave, "tabela": t["tabela"], "periodo": periodo, "n_linhas": t.get("n_linhas"),
             "bytes_zip": reg.get("bytes"), "bytes_csv": b_csv, "bytes_parquet": b_pq,
             "reducao_pct": round(100 * (1 - b_pq / b_csv), 1) if b_csv else None, "observacao": ""}
    if b_csv <= limite:
        linha["pandas_csv_s"], _ = _tempo(lambda: pd.read_csv(
            csv_tmp, sep=t["delimitador"], encoding=enc_py, encoding_errors="replace", dtype=str,
            keep_default_na=False, on_bad_lines="skip", engine="c"))
        linha["pandas_parquet_s"], _ = _tempo(
            lambda: pq.read_table([str(p) for p in arquivos_pq], partitioning=None).to_pandas())
        if linha["pandas_parquet_s"]:
            linha["pandas_parquet_x_mais_rapido"] = round(linha["pandas_csv_s"] / linha["pandas_parquet_s"], 1)
    else:
        linha["observacao"] = f"CSV de {fmt_bytes(b_csv)} acima do limite: pandas não medido"
    por_nome = {padronizar_nome(c): c for c in t["colunas"]}
    col = por_nome.get("uf") or t["colunas"][0]
    linha["coluna_agrupada"] = col
    con = duckdb.connect()
    leitura_csv = _ler_csv_duckdb(csv_tmp, t, enc_duck)
    leitura_pq = "read_parquet([" + ", ".join(caminho_sql(p) for p in arquivos_pq) + "])"
    linha["duckdb_parquet_contar_s"], _ = _tempo(lambda: con.execute(f"SELECT count(*) FROM {leitura_pq}").fetchall())
    linha["duckdb_parquet_agrupar_s"], _ = _tempo(lambda: con.execute(
        f"SELECT {ident_sql(col)}, count(*) FROM {leitura_pq} GROUP BY 1").fetchall())
    try:
        linha["duckdb_csv_contar_s"], _ = _tempo(lambda: con.execute(f"SELECT count(*) FROM {leitura_csv}").fetchall())
        linha["duckdb_csv_agrupar_s"], _ = _tempo(lambda: con.execute(
            f"SELECT {ident_sql(col)}, count(*) FROM {leitura_csv} GROUP BY 1").fetchall())
    except duckdb.Error as e:
        # Alguns arquivos antigos têm bytes que o leitor latin-1 do DuckDB recusa (a conversão só funciona
        # porque regrava o CSV em UTF-8 antes); fica a medida de tamanho e a do pandas, com a observação.
        linha["observacao"] = (linha["observacao"] + "; " if linha["observacao"] else "") + \
            f"DuckDB não leu o CSV bruto ({str(e).splitlines()[0][:80]})"
    con.close()
    csv_tmp.unlink(missing_ok=True)
    log.info("benchmark %s %s [%s]: CSV %s × Parquet %s; pandas %ss × %ss; DuckDB agrupar %ss × %ss",
             base.chave, periodo, t["tabela"], fmt_bytes(b_csv), fmt_bytes(b_pq),
             linha.get("pandas_csv_s", "-"), linha.get("pandas_parquet_s", "-"),
             linha.get("duckdb_csv_agrupar_s", "-"), linha["duckdb_parquet_agrupar_s"])
    return linha


CAMPOS = ["base", "tabela", "periodo", "n_linhas", "bytes_zip", "bytes_csv", "bytes_parquet", "reducao_pct",
          "pandas_csv_s", "pandas_parquet_s", "pandas_parquet_x_mais_rapido", "duckdb_csv_contar_s",
          "duckdb_parquet_contar_s", "duckdb_csv_agrupar_s", "duckdb_parquet_agrupar_s", "coluna_agrupada",
          "observacao"]
