"""Resumos da camada bronze para a análise exploratória (Etapa 2) do recorte do projeto.

Recorte: as três fases do programa federal de transferência de renda, jan/2019 a último mês publicado:
Bolsa Família (bolsa_familia_pagamentos), Auxílio Brasil (auxilio_brasil) e Novo Bolsa Família
(novo_bolsa_familia). São ~1,65 bilhão de linhas: o notebook não consegue ler isso direto. Este script percorre
um arquivo mensal por vez com DuckDB e grava tabelas pequenas em data/_eda/<tabela>/<base>_<periodo>.parquet:

- perfil:         por mês e coluna — linhas, nulos, vazios, fora do formato esperado, distintos (aprox.)
- mensal:         por mês — linhas, famílias (NIS distintos), valor, duplicados, quantis do valor, inconsistências
- municipio_mes:  por mês × UF × município — linhas, famílias, valor, retroativos, registros inválidos
- valor_faixas:   por mês × faixa de R$ 10 — linhas e valor (histograma do valor da parcela)
- defasagem:      por mês × (mês pago − mês de referência) — linhas e valor (pagamentos retroativos)
- transicoes:     por par de meses consecutivos — famílias que ficaram, entraram e saíram

Retomada: um resumo que já existe é pulado, a não ser que o arquivo da bronze tenha mudado desde que ele foi feito
(número e tamanho dos arquivos, guardados em data/_eda/_fontes.json) ou que o resumo esteja ilegível. Meses com
menos de 27 UFs na bronze (conversão incompleta) não são resumidos.

Competência e referência: em cada arquivo, a competência (mês da folha) é a coluna que vale o mês do arquivo em
todas as linhas. No arquivo de nov/2021 do Bolsa Família os rótulos vêm trocados ("MÊS REFERÊNCIA" é que vale o
mês do arquivo); o script detecta isso pelos dados e troca as duas colunas.

Uso, na raiz do projeto:

    python src/eda.py                                   # todos os resumos (completa o que falta)
    python src/eda.py --so mensal                       # só uma tabela
    python src/eda.py --so mensal --bases novo_bolsa_familia --refazer   # recalcula um recorte
    python src/eda.py --periodos 202111 --refazer       # recalcula um mês
"""

import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))

from util import caminho_sql, literal_sql, remover_arvore  # noqa: E402

raiz = Path(__file__).resolve().parents[1]
bronze = raiz / "data" / "bronze"
saida = raiz / "data" / "_eda"
arquivo_registro = saida / "_fontes.json"
pasta_tmp = raiz / "data" / "_tmp" / "duckdb_eda"

# base -> (fase, primeiro mês da fase dentro do recorte, último mês da fase)
fases = {
    "bolsa_familia_pagamentos": ("Bolsa Família", "201901", "202111"),
    "auxilio_brasil": ("Auxílio Brasil", "202111", "202302"),
    "novo_bolsa_familia": ("Novo Bolsa Família", "202303", "999912"),
}
col_comp = "MÊS COMPETÊNCIA"
col_ref = "MÊS REFERÊNCIA"
# nome no CSV -> nome usado na EDA
colunas = {
    col_comp: "mes_competencia",
    col_ref: "mes_referencia",
    "UF": "uf",
    "CÓDIGO MUNICÍPIO SIAFI": "cod_siafi",
    "NOME MUNICÍPIO": "nome_municipio",
    "CPF FAVORECIDO": "cpf",
    "NIS FAVORECIDO": "nis",
    "VALOR PARCELA": "valor_txt",
}
ufs = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
       "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]
lista_ufs = ", ".join(literal_sql(u) for u in ufs)
# formatos descritos no dicionário oficial
regex_mes = r"\d{4}(0[1-9]|1[0-2])"
regex_valor = r"-?\d{1,3}(\.?\d{3})*,\d{2}"
todas_tabelas = ["perfil", "mensal", "municipio_mes", "valor_faixas", "defasagem", "transicoes"]
# arquivo mensal na bronze: AAAAMM.parquet, e AAAAMM_N.parquet quando a conversão divide a partição
nome_arquivo_mensal = re.compile(r"(\d{6})(?:_\d+)?")

log = logging.getLogger("eda")


def casa(coluna, regex):
    return f"regexp_full_match({coluna}, {literal_sql(regex)})"


def regra_formato(coluna):
    # regra de formato de cada coluna, como o dicionário oficial descreve (nulo e vazio contam à parte)
    if coluna == "mes_competencia" or coluna == "mes_referencia":
        return casa(coluna, regex_mes)
    if coluna == "uf":
        return f"{coluna} IN ({lista_ufs})"
    if coluna == "cod_siafi":
        return casa(coluna, r"\d{4}")
    if coluna == "nome_municipio":
        return f"length(trim({coluna})) > 0"
    if coluna == "cpf":
        return casa(coluna, r"\*\*\*\.\d{3}\.\d{3}-\*\*")
    if coluna == "nis":
        return casa(coluna, r"\d{11}")
    return casa(coluna, regex_valor)  # valor_txt


# fontes na bronze

def arquivos_fonte(base, periodo):
    pasta = bronze / base / base
    return (sorted(pasta.glob(f"ano=*/uf_sigla=*/{periodo}.parquet"))
            + sorted(pasta.glob(f"ano=*/uf_sigla=*/{periodo}_[0-9]*.parquet")))


def impressao(base, periodo):
    # identifica a versão da fonte de um mês: se mudar, os resumos daquele mês são refeitos
    arqs = arquivos_fonte(base, periodo)
    return {"arquivos": len(arqs), "bytes": sum(a.stat().st_size for a in arqs)}


def periodo_e_base(item):
    return item[2], item[0]


def periodos_do_recorte():
    # (base, fase, mês) de cada arquivo mensal completo do recorte, em ordem cronológica
    lista = []
    for base in fases:
        fase, inicio, fim = fases[base]
        periodos = set()
        for arq in (bronze / base / base).glob("ano=*/uf_sigla=*/*.parquet"):
            nome = nome_arquivo_mensal.fullmatch(arq.stem)
            if not nome:
                raise SystemExit(f"Arquivo inesperado na bronze: {arq}\n(esperado AAAAMM.parquet ou "
                                 f"AAAAMM_N.parquet; mova ou apague o arquivo e rode de novo)")
            periodos.add(nome.group(1))
        for p in sorted(periodos):
            if p < inicio or p > fim:
                continue
            ufs_do_mes = set()
            for arq in arquivos_fonte(base, p):
                if arq.parent.name.removeprefix("uf_sigla=") in ufs:
                    ufs_do_mes.add(arq.parent.name)
            if len(ufs_do_mes) < len(ufs):
                log.warning("%s %s: só %d de %d UFs na bronze (conversão incompleta?); mês fora dos resumos",
                            base, p, len(ufs_do_mes), len(ufs))
                continue
            lista.append((base, fase, p))
    return sorted(lista, key=periodo_e_base)


def ler_parquet(base, periodo):
    lista = ", ".join(caminho_sql(a) for a in arquivos_fonte(base, periodo))
    return f"read_parquet([{lista}], union_by_name = true)"


def meses_trocados(con, base, periodo):
    # rótulos trocados: no arquivo, a coluna chamada "MÊS REFERÊNCIA" é que vale o mês do arquivo
    p = literal_sql(periodo)
    comp_dif, ref_dif, linhas = con.execute(f"""
        SELECT count(*) FILTER ("{col_comp}" IS DISTINCT FROM {p}), count(*) FILTER ("{col_ref}" IS DISTINCT FROM {p}),
               count(*)
        FROM {ler_parquet(base, periodo)}""").fetchone()
    if comp_dif and not ref_dif:
        log.warning("%s %s: rótulos trocados (\"%s\" vale o mês do arquivo em todas as linhas e \"%s\" difere em "
                    "%d); as duas colunas serão trocadas", base, periodo, col_ref, col_comp, comp_dif)
        return True
    if comp_dif:
        log.warning("%s %s: %d de %d linhas com competência diferente do mês do arquivo", base, periodo, comp_dif,
                    linhas)
    return False


def mes_em_numero(col):
    # ano * 12 + mês, para fazer conta de diferença entre meses
    return f'TRY_CAST(substr("{col}", 1, 4) AS INTEGER) * 12 + TRY_CAST(substr("{col}", 5, 2) AS INTEGER)'


def fonte(base, periodo, trocar_meses=False):
    # select com as colunas padronizadas e o valor convertido, para um arquivo mensal (todas as UFs)
    origem = dict(colunas)
    comp = col_comp
    ref = col_ref
    if trocar_meses:
        origem[col_comp] = "mes_referencia"
        origem[col_ref] = "mes_competencia"
        comp = col_ref
        ref = col_comp
    partes = []
    for nome in origem:
        partes.append(f'"{nome}" AS {origem[nome]}')
    valor = 'trim("VALOR PARCELA")'
    # texto fora do formato vira NULL (ilegível), e não um número 10 ou 100 vezes maior
    return (f"SELECT {', '.join(partes)}, "
            f"CASE WHEN {casa(valor, regex_valor)} THEN "
            f"CAST(replace(replace({valor}, '.', ''), ',', '.') AS DECIMAL(14, 2)) END AS valor, "
            f"{mes_em_numero(comp)} AS m_comp, {mes_em_numero(ref)} AS m_ref "
            f"FROM {ler_parquet(base, periodo)}")


# resumos por mês

def gravar(con, sql, destino):
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".parquet.tmp")
    con.execute(f"COPY ({sql}) TO {caminho_sql(tmp)} (FORMAT parquet, COMPRESSION zstd)")
    for tentativa in range(5):  # no Windows, antivírus ou um leitor aberto podem travar o destino por instantes
        try:
            tmp.replace(destino)
            return
        except PermissionError:
            if tentativa == 4:
                raise
            time.sleep(1 + tentativa)


def resumo_perfil(base, fase, periodo):
    partes = []
    for c in colunas.values():
        regra = regra_formato(c)
        partes.append(
            f"SELECT '{c}' AS coluna, count(*) AS linhas, count(*) FILTER ({c} IS NULL) AS nulos, "
            f"count(*) FILTER ({c} IS NOT NULL AND trim({c}) = '') AS vazios, "
            f"count(*) FILTER ({c} IS NOT NULL AND trim({c}) <> '' AND NOT coalesce({regra}, false)) AS fora_formato, "
            f"approx_count_distinct({c}) AS distintos_aprox, max(length({c})) AS maior_tamanho "
            f"FROM v")
    return (f"SELECT '{base}' AS base, '{fase}' AS fase, '{periodo}' AS periodo, * FROM ("
            + " UNION ALL ".join(partes) + ")")


def resumo_mensal(base, fase, periodo):
    # linhas sem NIS não identificam a família: ficam fora de famílias, chaves repetidas e duplicados exatos
    # (o perfil conta quantas são)
    return f"""
    WITH chaves AS (
        SELECT count(*) AS chaves_repetidas, coalesce(sum(n), 0) AS linhas_em_chave_repetida FROM (
            SELECT nis, mes_referencia, count(*) AS n FROM v WHERE nis IS NOT NULL
            GROUP BY ALL HAVING count(*) > 1)),
    exatas AS (
        SELECT count(*) AS linhas_distintas FROM (
            SELECT DISTINCT * EXCLUDE (valor, m_comp, m_ref) FROM v WHERE nis IS NOT NULL)),
    com_nis AS (SELECT count(*) AS linhas FROM v WHERE nis IS NOT NULL),
    multi AS (SELECT count(*) AS nis_em_mais_de_um_municipio FROM (
            SELECT nis FROM v WHERE nis IS NOT NULL GROUP BY nis HAVING count(DISTINCT cod_siafi) > 1)),
    base AS (
        SELECT count(*) AS linhas,
               count(DISTINCT nis) AS familias,
               count(DISTINCT nis) FILTER (m_ref = m_comp) AS familias_mes_corrente,
               sum(valor) AS valor_total,
               sum(valor) FILTER (m_ref = m_comp) AS valor_mes_corrente,
               count(*) FILTER (m_ref < m_comp) AS linhas_retroativas,
               count(*) FILTER (mes_competencia IS DISTINCT FROM '{periodo}') AS linhas_competencia_diferente_do_arquivo,
               count(*) FILTER (m_ref > m_comp) AS linhas_referencia_futura,
               count(*) FILTER (valor IS NULL) AS linhas_valor_ilegivel,
               count(*) FILTER (valor = 0) AS linhas_valor_zero,
               count(*) FILTER (valor < 0) AS linhas_valor_negativo,
               min(valor) AS valor_min, max(valor) AS valor_max,
               avg(valor) AS valor_media, stddev_samp(valor) AS valor_desvio,
               quantile_cont(valor, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]) AS valor_quantis,
               avg(valor) FILTER (m_ref = m_comp) AS valor_media_mes_corrente,
               quantile_cont(valor, 0.5) FILTER (m_ref = m_comp) AS valor_mediana_mes_corrente
        FROM v)
    SELECT '{base}' AS base, '{fase}' AS fase, '{periodo}' AS periodo, base.*, exatas.linhas_distintas,
           com_nis.linhas - exatas.linhas_distintas AS linhas_duplicadas_exatas, chaves.chaves_repetidas,
           chaves.linhas_em_chave_repetida,
           multi.nis_em_mais_de_um_municipio
    FROM base, exatas, com_nis, chaves, multi"""


def resumo_municipio_mes(base, fase, periodo):
    return f"""
    SELECT '{base}' AS base, '{fase}' AS fase, '{periodo}' AS periodo, uf, cod_siafi, nome_municipio,
           count(*) AS linhas, count(DISTINCT nis) AS familias,
           count(DISTINCT nis) FILTER (m_ref = m_comp) AS familias_mes_corrente,
           sum(valor) AS valor_total, sum(valor) FILTER (m_ref = m_comp) AS valor_mes_corrente,
           count(*) FILTER (m_ref < m_comp) AS linhas_retroativas,
           count(*) FILTER (valor IS NULL) AS linhas_valor_ilegivel,
           count(*) FILTER (valor <= 0) AS linhas_valor_nao_positivo,
           count(*) FILTER (cpf IS NULL OR trim(cpf) = '') AS linhas_cpf_ausente,
           count(*) FILTER (nis IS NULL OR NOT {regra_formato("nis")}) AS linhas_nis_invalido
    FROM v GROUP BY uf, cod_siafi, nome_municipio"""


def resumo_valor_faixas(base, fase, periodo):
    return f"""
    SELECT '{base}' AS base, '{fase}' AS fase, '{periodo}' AS periodo,
           (m_ref = m_comp) AS mes_corrente, floor(valor / 10) * 10 AS faixa_inicio,
           count(*) AS linhas, sum(valor) AS valor
    FROM v WHERE valor IS NOT NULL GROUP BY ALL"""


def resumo_defasagem(base, fase, periodo):
    return f"""
    SELECT '{base}' AS base, '{fase}' AS fase, '{periodo}' AS periodo, m_comp - m_ref AS meses_de_atraso,
           count(*) AS linhas, count(DISTINCT nis) AS familias, sum(valor) AS valor
    FROM v GROUP BY ALL"""


resumos = {
    "perfil": resumo_perfil,
    "mensal": resumo_mensal,
    "municipio_mes": resumo_municipio_mes,
    "valor_faixas": resumo_valor_faixas,
    "defasagem": resumo_defasagem,
}


# retomada

def ler_registro():
    try:
        return json.loads(arquivo_registro.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def salvar_registro(registro):
    arquivo_registro.parent.mkdir(parents=True, exist_ok=True)
    tmp = arquivo_registro.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registro, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    tmp.replace(arquivo_registro)


def legivel(con, caminho):
    if not caminho.exists():
        return False
    try:
        con.execute(f"SELECT count(*) FROM parquet_metadata({caminho_sql(caminho)})").fetchone()
        return True
    except duckdb.Error:
        log.warning("resumo ilegível, será refeito: %s", caminho)
        return False


def em_dia(con, registro, tabela, chave, fonte_atual, destino):
    # o resumo existe, abre e foi feito a partir da fonte atual?
    if not legivel(con, destino):
        return False
    if tabela not in registro:
        registro[tabela] = {}
    feito_com = registro[tabela].get(chave)
    if feito_com is None:  # resumo anterior ao registro de fontes: passa a valer a fonte atual
        registro[tabela][chave] = fonte_atual
        return True
    return feito_com == fonte_atual


# transições

def mes_seguinte(periodo):
    ano = int(periodo[:4])
    mes = int(periodo[4:])
    return f"{ano + mes // 12}{mes % 12 + 1:02d}"


def nis_do_mes(arquivos, periodo):
    # NIS distintos pagos no mês (todas as bases do mês: nov/2021 tem Bolsa Família e Auxílio Brasil)
    partes = []
    for b, f, p in arquivos:
        if p == periodo:
            partes.append(f"SELECT TRY_CAST(nis AS BIGINT) AS nis, '{b}' AS base FROM ({fonte(b, p)})")
    return " UNION ALL ".join(partes)


def resumo_transicoes(con, arquivos, registro, refazer):
    # entradas e saídas de famílias entre meses seguidos do calendário. Cada mês é lido uma vez só: os NIS do mês
    # ficam numa tabela temporária, que é comparada com a do mês anterior e depois vira o "anterior" do mês seguinte.
    # refazer traz os meses cujos pares devem ser recalculados mesmo em dia
    meses = sorted(set(p for b, f, p in arquivos))
    bases = {}
    fontes = {}
    for m in meses:
        do_mes = []
        fontes[m] = {}
        for b, f, p in arquivos:
            if p == m:
                do_mes.append(b)
                fontes[m][b] = impressao(b, p)
        bases[m] = "+".join(sorted(do_mes))

    esperados = []
    for i in range(len(meses) - 1):
        a = meses[i]
        b = meses[i + 1]
        if mes_seguinte(a) == b:
            esperados.append((a, b))
        else:
            log.warning("transições: falta mês entre %s e %s na bronze; esse intervalo fica sem par", a, b)

    pasta = saida / "transicoes"
    nomes_esperados = []
    for a, b in esperados:
        nomes_esperados.append(f"{a}_{b}")
    sobras = []
    for arq in pasta.glob("*.parquet"):
        if arq.stem not in nomes_esperados:
            sobras.append(arq.stem)
    if len(sobras) > 0:
        log.warning("transições: pares que não fazem parte da série atual (apague-os, ou o notebook vai lê-los): %s",
                    ", ".join(sorted(sobras)))

    pares = []
    for a, b in esperados:
        chave = f"{a}_{b}"
        fonte_par = {"anterior": fontes[a], "atual": fontes[b]}
        if a in refazer or b in refazer or not em_dia(con, registro, "transicoes", chave, fonte_par,
                                                       pasta / f"{chave}.parquet"):
            pares.append((a, b))
    precisa = set()
    for a, b in pares:
        precisa.add(a)
        precisa.add(b)

    anterior = None
    for m in meses:
        if m not in precisa:
            anterior = None
            continue
        inicio = time.perf_counter()
        con.execute(f"""CREATE OR REPLACE TEMP TABLE atual AS
                        SELECT nis, count(DISTINCT base) AS n_bases FROM ({nis_do_mes(arquivos, m)})
                        WHERE nis IS NOT NULL GROUP BY nis""")
        if anterior is not None and (anterior, m) in pares:
            gravar(con, f"""
                SELECT '{anterior}' AS periodo_anterior, '{m}' AS periodo,
                       '{bases[anterior]}' AS bases_anterior, '{bases[m]}' AS bases_atual,
                       (SELECT count(*) FROM ant) AS familias_anterior,
                       (SELECT count(*) FROM atual) AS familias_atual,
                       (SELECT count(*) FROM atual WHERE n_bases > 1) AS familias_em_duas_bases,
                       (SELECT count(*) FROM ant SEMI JOIN atual USING (nis)) AS permaneceram,
                       (SELECT count(*) FROM atual ANTI JOIN ant USING (nis)) AS entraram,
                       (SELECT count(*) FROM ant ANTI JOIN atual USING (nis)) AS sairam""",
                  pasta / f"{anterior}_{m}.parquet")
            if "transicoes" not in registro:
                registro["transicoes"] = {}
            registro["transicoes"][f"{anterior}_{m}"] = {"anterior": fontes[anterior], "atual": fontes[m]}
            salvar_registro(registro)
            log.info("transição %s → %s em %.0fs", anterior, m, time.perf_counter() - inicio)
        con.execute("DROP TABLE IF EXISTS ant")
        con.execute("ALTER TABLE atual RENAME TO ant")
        anterior = m


# execução

def conectar(memoria, threads):
    remover_arvore(pasta_tmp)  # spill de uma execução interrompida
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    livre_gb = shutil.disk_usage(pasta_tmp).free / 1e9
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    con.execute(f"SET memory_limit = {literal_sql(memoria)}")
    con.execute(f"SET temp_directory = {caminho_sql(pasta_tmp)}")
    con.execute(f"SET max_temp_directory_size = '{max(1, int(livre_gb) - 5)}GB'")  # deixa 5 GB livres no disco
    con.execute("SET preserve_insertion_order = false")
    if threads:
        con.execute(f"SET threads = {int(threads)}")
    return con


def executar(tabelas, refazer=False, memoria="8GB", threads=None, bases=None, periodos=None):
    arquivos = periodos_do_recorte()
    if len(arquivos) == 0:
        raise SystemExit(f"Nenhum arquivo do recorte em {bronze}: rode a coleta antes (README).")
    log.info("recorte: %d arquivos mensais (%s a %s)", len(arquivos), arquivos[0][2], arquivos[-1][2])
    escolhidos = []
    for b, f, p in arquivos:
        if (bases is None or b in bases) and (periodos is None or p in periodos):
            escolhidos.append((b, f, p))
    registro = ler_registro()
    con = conectar(memoria, threads)
    por_mes = []
    for t in tabelas:
        if t in resumos:
            por_mes.append(t)

    for i, (base, fase, periodo) in enumerate(escolhidos, 1):
        chave = f"{base}_{periodo}"
        fonte_atual = impressao(base, periodo)
        pendentes = []
        for t in por_mes:
            if refazer or not em_dia(con, registro, t, chave, fonte_atual, saida / t / f"{chave}.parquet"):
                pendentes.append(t)
        if len(pendentes) == 0:
            continue
        inicio = time.perf_counter()
        trocar = meses_trocados(con, base, periodo)
        con.execute(f"CREATE OR REPLACE TEMP VIEW v AS {fonte(base, periodo, trocar)}")
        for t in pendentes:
            gravar(con, resumos[t](base, fase, periodo), saida / t / f"{chave}.parquet")
            if t not in registro:
                registro[t] = {}
            registro[t][chave] = fonte_atual
            salvar_registro(registro)
        log.info("[%d/%d] %s %s: %s em %.0fs", i, len(escolhidos), base, periodo, ", ".join(pendentes),
                 time.perf_counter() - inicio)

    if "transicoes" in tabelas:
        refazer_meses = set()
        if refazer:
            for b, f, p in escolhidos:
                refazer_meses.add(p)
        resumo_transicoes(con, arquivos, registro, refazer_meses)
    salvar_registro(registro)
    con.close()
    remover_arvore(pasta_tmp)
    log.info("resumos em %s", saida)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # mensagens de erro do argparse
    parser = argparse.ArgumentParser(description="Resumos da bronze para a análise exploratória")
    parser.add_argument("--so", nargs="+", choices=todas_tabelas, default=todas_tabelas, help="só estas tabelas")
    parser.add_argument("--bases", nargs="+", choices=list(fases), help="só estas bases")
    parser.add_argument("--periodos", nargs="+", help="só estes meses (AAAAMM)")
    parser.add_argument("--refazer", action="store_true", help="recalcula mesmo o que está em dia")
    parser.add_argument("--memoria", default="8GB", help="limite de memória do DuckDB (padrão 8GB)")
    parser.add_argument("--threads", type=int, default=None)
    args = parser.parse_args()
    for p in args.periodos or []:
        if not re.fullmatch(regex_mes, p):
            parser.error(f"período inválido: {p} (use AAAAMM)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    executar(args.so, args.refazer, args.memoria, args.threads, args.bases, args.periodos)


if __name__ == "__main__":
    main()
