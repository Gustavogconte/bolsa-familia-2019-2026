"""Limpeza e tratamento (Etapa 3): bronze -> silver (linha a linha) e gold (município x mês).

Cada mês do recorte é lido uma vez da bronze, limpo e gravado em
data/silver/pagamentos/ano=AAAA/mes=MM/pagamentos.parquet. No mesmo passo saem as partes da gold e as
contagens do log de limpeza. No fim, o script junta tudo em data/gold e em reports/log_limpeza.csv e .md.

Uso, na raiz do projeto:

    python src/limpeza.py              # processa os meses que faltam e monta a gold e o log
    python src/limpeza.py --refazer    # processa todos os meses de novo
"""

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eda import fases, fonte, meses_trocados, periodos_do_recorte  # noqa: E402
from util import caminho_sql, remover_arvore  # noqa: E402

raiz = Path(__file__).resolve().parents[1]
ref = raiz / "data" / "bronze" / "referencias"
silver = raiz / "data" / "silver"
gold = raiz / "data" / "gold"
pasta_tmp = raiz / "data" / "_tmp" / "duckdb_limpeza"


def conectar():
    remover_arvore(pasta_tmp)  # sobra de uma execução interrompida
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    con.execute("SET memory_limit = '6GB'")  # deixa folga para o sistema na máquina de 16 GB
    con.execute(f"SET temp_directory = {caminho_sql(pasta_tmp)}")
    return con


def gravar(con, sql, destino):
    # grava num .tmp e só depois troca o nome, para não deixar arquivo pela metade
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".parquet.tmp")
    con.execute(f"COPY ({sql}) TO {caminho_sql(tmp)} (FORMAT parquet, COMPRESSION zstd)")
    tmp.replace(destino)


def carregar_municipios(con):
    # código SIAFI (usado pelo Portal) -> código IBGE, nome, UF e região do IBGE
    tabela = caminho_sql(ref / "municipios_siafi_ibge" / "municipios.csv")
    localidades = caminho_sql(ref / "ibge_localidades_municipios" / "municipios.parquet")
    con.execute(f"""
        CREATE OR REPLACE TABLE municipios AS
        SELECT lpad(CAST(m.siafi_id AS VARCHAR), 4, '0') AS cod_siafi,
               CAST(m.codigo_ibge AS INTEGER) AS cod_municipio,
               l.nome AS municipio, l.sigla_uf AS uf, l.regiao
        FROM read_csv({tabela}) m
        LEFT JOIN read_parquet({localidades}) l ON CAST(l.id_municipio AS INTEGER) = CAST(m.codigo_ibge AS INTEGER)""")


def piso_do_mes(periodo):
    # piso por família no Auxílio Brasil; fora desse período não se aplica
    if "202112" <= periodo <= "202207":
        return 400
    if "202208" <= periodo <= "202302":
        return 600
    return 0


def arquivos_do_mes(periodo):
    pasta = silver / "pagamentos" / f"ano={periodo[:4]}" / f"mes={periodo[4:]}"
    return [pasta / "pagamentos.parquet",
            silver / "_contagens" / f"{periodo}.parquet",
            gold / "_partes" / f"municipio_{periodo}.parquet",
            gold / "_partes" / f"brasil_{periodo}.parquet"]


def processar_mes(con, periodo, bases):
    n = {}

    # 1) uma view por base; o programa mais novo vem primeiro (em nov/2021, Auxílio Brasil antes do Bolsa Família)
    n["linhas_bronze"] = 0
    n["rotulos_trocados"] = 0
    n["meses_alterados_pela_troca"] = 0
    for base in bases:
        trocar = meses_trocados(con, base, periodo)
        con.execute(f"CREATE OR REPLACE TEMP VIEW v_{base} AS {fonte(base, periodo, trocar)}")
        total = con.execute(f"SELECT count(*) FROM v_{base}").fetchone()[0]
        n["linhas_bronze"] += total
        if trocar:
            n["rotulos_trocados"] += total
            n["meses_alterados_pela_troca"] += con.execute(
                f"SELECT count(*) FROM v_{base} WHERE m_comp <> m_ref").fetchone()[0]

    # 2) junta as bases do mês. Do Bolsa Família de nov/2021 só ficam as linhas de família e mês de
    #    referência que não estão no Auxílio Brasil. O valor fica de fora da chave: em 4,9 mil famílias ele
    #    muda de um arquivo para o outro, quase sempre de R$ 1 a R$ 35 a mais no do Bolsa Família
    primeira = bases[0]
    juntas = f"SELECT '{primeira}' AS base, * FROM v_{primeira}"
    for base in bases[1:]:
        juntas += (f" UNION ALL SELECT '{base}' AS base, b.* FROM v_{base} b"
                   f" ANTI JOIN v_{primeira} a ON a.nis = b.nis AND a.mes_referencia = b.mes_referencia")
    linhas_juntas = con.execute(f"SELECT count(*) FROM ({juntas})").fetchone()[0]
    n["repetidas_entre_bases"] = n["linhas_bronze"] - linhas_juntas

    # 3) duplicado exato: a mesma linha inteira mais de uma vez. Linhas sem NIS ficam todas,
    #    porque não dá para saber se duas delas são da mesma família
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE dedup AS
        SELECT DISTINCT * FROM ({juntas}) WHERE nis IS NOT NULL
        UNION ALL
        SELECT * FROM ({juntas}) WHERE nis IS NULL""")
    n["duplicadas_exatas"] = linhas_juntas - con.execute("SELECT count(*) FROM dedup").fetchone()[0]

    # 4) mesma família e mesmo mês de referência mais de uma vez: fica, mas com marca
    con.execute("""
        CREATE OR REPLACE TEMP TABLE repetidas AS
        SELECT nis, mes_referencia FROM dedup WHERE nis IS NOT NULL GROUP BY ALL HAVING count(*) > 1""")

    # 5) tipos, nomes em snake_case, município do IBGE e marcas
    fase = "CASE d.base"
    for base in fases:
        fase += f" WHEN '{base}' THEN '{fases[base][0]}'"
    fase += " END"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE limpo AS
        SELECT {fase} AS fase,
               CAST(strptime(d.mes_competencia, '%Y%m') AS DATE) AS mes_pagamento,
               CAST(strptime(d.mes_referencia, '%Y%m') AS DATE) AS mes_referencia,
               CAST(d.m_comp - d.m_ref AS SMALLINT) AS meses_atraso,
               m.uf, m.regiao, m.cod_municipio, m.municipio,
               TRY_CAST(d.nis AS BIGINT) AS nis,
               CAST(d.valor AS DECIMAL(10, 2)) AS valor,
               d.m_ref < d.m_comp AS retroativo,
               d.cpf IS NULL OR trim(d.cpf) = '' AS cpf_ausente,
               r.nis IS NOT NULL AS chave_repetida,
               d.valor < 20 OR d.valor >= 3000 AS valor_extremo,
               d.nome_municipio AS nome_portal
        FROM dedup d
        LEFT JOIN municipios m ON m.cod_siafi = d.cod_siafi
        LEFT JOIN repetidas r ON r.nis = d.nis AND r.mes_referencia = d.mes_referencia""")

    destinos = arquivos_do_mes(periodo)
    gravar(con, "SELECT * EXCLUDE (nome_portal) FROM limpo", destinos[0])

    # 6) contagens para o log de limpeza
    piso = piso_do_mes(periodo)
    linha = con.execute(f"""
        SELECT count(*), count(*) FILTER (nis IS NULL), count(*) FILTER (cpf_ausente),
               count(*) FILTER (chave_repetida), count(*) FILTER (retroativo), count(*) FILTER (valor_extremo),
               count(*) FILTER (cod_municipio IS NULL),
               count(*) FILTER (upper(strip_accents(municipio)) <> upper(strip_accents(nome_portal))),
               count(*) FILTER (valor IS NULL),
               count(*) FILTER (fase = 'Auxílio Brasil' AND meses_atraso = 0 AND valor < {piso})
        FROM limpo""").fetchone()
    nomes = ["linhas_silver", "nis_ausente", "cpf_ausente", "chave_repetida", "retroativo", "valor_extremo",
             "sem_codigo_ibge", "nome_diferente_do_ibge", "valor_ilegivel", "abaixo_do_piso"]
    for i in range(len(nomes)):
        n[nomes[i]] = linha[i]
    n["nis_em_dois_municipios"] = con.execute("""
        SELECT count(*) FROM (SELECT nis FROM limpo WHERE nis IS NOT NULL
                              GROUP BY nis HAVING count(DISTINCT cod_municipio) > 1)""").fetchone()[0]

    colunas = [f"'{periodo}' AS periodo"]
    for chave in n:
        colunas.append(f"{n[chave]} AS {chave}")
    gravar(con, "SELECT " + ", ".join(colunas), destinos[1])

    # 7) partes da gold: município x mês e Brasil x mês (família = NIS distinto)
    medidas = """count(*) AS linhas, count(DISTINCT nis) AS familias,
                 count(DISTINCT nis) FILTER (meses_atraso = 0) AS familias_mes_corrente,
                 sum(valor) AS valor_total, sum(valor) FILTER (meses_atraso = 0) AS valor_mes_corrente,
                 sum(valor) FILTER (retroativo) AS valor_retroativo"""
    gravar(con, f"""SELECT '{periodo}' AS periodo, cod_municipio, municipio, uf, regiao, {medidas}
                    FROM limpo GROUP BY ALL""", destinos[2])
    gravar(con, f"SELECT '{periodo}' AS periodo, {medidas} FROM limpo", destinos[3])
    return n


def montar_gold(con):
    # população por município e ano. O IBGE não tem estimativa municipal para 2022 e 2023, e o Censo 2022
    # (contagem) fica ~5% abaixo das estimativas: 2022 e 2023 são interpolados entre 2021 e 2024
    estimativas = caminho_sql(ref / "ibge_estimativas_populacao_municipios" / "*" / "*.parquet")
    con.execute(f"""
        CREATE OR REPLACE TABLE populacao AS
        WITH est AS (
            SELECT TRY_CAST("Município (Código)" AS INTEGER) AS cod_municipio, TRY_CAST("Ano" AS INTEGER) AS ano,
                   TRY_CAST("Valor" AS BIGINT) AS populacao
            FROM read_parquet({estimativas}, hive_partitioning = false)),
        p21 AS (SELECT cod_municipio, populacao FROM est WHERE ano = 2021),
        p24 AS (SELECT cod_municipio, populacao FROM est WHERE ano = 2024)
        SELECT cod_municipio, ano, populacao, 'estimativa IBGE' AS fonte
        FROM est WHERE ano >= 2019 AND populacao IS NOT NULL
        UNION ALL
        SELECT cod_municipio, 2022, CAST(round(p21.populacao + (p24.populacao - p21.populacao) / 3) AS BIGINT),
               'interpolada entre 2021 e 2024'
        FROM p21 JOIN p24 USING (cod_municipio)
        UNION ALL
        SELECT cod_municipio, 2023, CAST(round(p21.populacao + 2 * (p24.populacao - p21.populacao) / 3) AS BIGINT),
               'interpolada entre 2021 e 2024'
        FROM p21 JOIN p24 USING (cod_municipio)""")
    gravar(con, "SELECT * FROM populacao ORDER BY ano, cod_municipio", gold / "populacao_municipio_ano.parquet")

    # programa em vigor no mês (nov/2021 já é Auxílio Brasil)
    fase = "CASE WHEN periodo < '202111' THEN 'Bolsa Família' WHEN periodo < '202303' THEN 'Auxílio Brasil' " \
           "ELSE 'Novo Bolsa Família' END"
    partes = gold / "_partes"
    gravar(con, f"""
        SELECT CAST(strptime(g.periodo, '%Y%m') AS DATE) AS mes, CAST(substr(g.periodo, 1, 4) AS INTEGER) AS ano,
               {fase} AS fase, g.* EXCLUDE (periodo), p.populacao,
               1e5 * g.familias / p.populacao AS familias_100mil_hab,
               g.valor_mes_corrente / g.familias_mes_corrente AS valor_por_familia
        FROM read_parquet({caminho_sql(partes / "municipio_*.parquet")}) g
        LEFT JOIN populacao p ON p.cod_municipio = g.cod_municipio AND p.ano = CAST(substr(g.periodo, 1, 4) AS INTEGER)
        ORDER BY mes, cod_municipio""", gold / "municipio_mes.parquet")
    gravar(con, f"""
        SELECT CAST(strptime(periodo, '%Y%m') AS DATE) AS mes, CAST(substr(periodo, 1, 4) AS INTEGER) AS ano,
               {fase} AS fase, * EXCLUDE (periodo),
               valor_mes_corrente / familias_mes_corrente AS valor_por_familia
        FROM read_parquet({caminho_sql(partes / "brasil_*.parquet")}) ORDER BY mes""", gold / "brasil_mes.parquet")
    sem_pop = con.execute(f"""SELECT count(*) FROM read_parquet({caminho_sql(gold / "municipio_mes.parquet")})
                              WHERE populacao IS NULL""").fetchone()[0]
    return sem_pop


def br(numero):
    return format(int(numero), ",").replace(",", ".")


def montar_log(con, sem_pop):
    contagens = caminho_sql(silver / "_contagens" / "*.parquet")
    total = con.execute(f"SELECT * EXCLUDE (periodo) FROM read_parquet({contagens})").fetchdf().sum()
    n_pop = con.execute("SELECT count(*) FROM populacao WHERE ano IN (2022, 2023)").fetchone()[0]

    # problema, decisão, justificativa, linhas afetadas
    log = [
        ["Colunas com nomes do CSV (maiúsculas e acentos) e todas gravadas como texto na bronze",
         "Renomear para snake_case e converter os tipos: meses para data, NIS para inteiro, valor para decimal, "
         "marcas para booleano",
         "Permite calcular direto sobre a base, e na amostra da EDA a memória caiu 88%",
         br(total["linhas_silver"])],
        ["Arquivo de nov/2021 do Bolsa Família com os rótulos de competência e referência trocados",
         "Trocar as duas colunas nesse arquivo: a competência é a coluna que vale o mês do arquivo",
         "É assim nos outros 93 arquivos, e o dicionário do Auxílio Brasil descreve essa mesma troca",
         br(total["rotulos_trocados"]) + " (em " + br(total["meses_alterados_pela_troca"]) + " os meses mudam)"],
        ["Nov/2021 publicado nas duas bases (o arquivo do Bolsa Família repete os pagamentos do Auxílio Brasil)",
         "Manter o arquivo do Auxílio Brasil e, do Bolsa Família, só as linhas de família e mês de referência "
         "que não estão nele (mesmo NIS e mês de referência)",
         "É o mesmo pagamento publicado duas vezes, então somar dobraria o mês. O valor ficou fora da chave porque "
         "em 4,9 mil famílias ele muda de um arquivo para o outro, quase sempre de R$ 1 a R$ 35 a mais no do Bolsa "
         "Família",
         br(total["repetidas_entre_bases"]) + " removidas"],
        ["Duplicados exatos (a mesma linha inteira mais de uma vez)",
         "Remover, mantendo uma",
         "Mesma parcela registrada duas vezes, e nenhum campo diferencia as cópias",
         br(total["duplicadas_exatas"]) + " removidas"],
        ["NIS ausente",
         "Manter a linha com NIS nulo, fora da contagem de famílias",
         "Não dá para atribuir a família, mas o valor foi pago e entra nos totais",
         br(total["nis_ausente"])],
        ["CPF ausente (o CPF vem mascarado, ***.123.456-**)",
         "Tirar a coluna de CPF da silver e manter a marca cpf_ausente",
         "O CPF mascarado não é chave nem variável de análise, e tirar a coluna diminui os dados pessoais guardados (LGPD)",
         br(total["cpf_ausente"])],
        ["Mesmo NIS e mesmo mês de referência mais de uma vez no mês",
         "Manter e marcar chave_repetida",
         "Pode ser complemento legítimo, e as famílias são contadas por NIS distinto",
         br(total["chave_repetida"])],
        ["Pagamento retroativo (referência anterior ao mês pago)",
         "Manter, marcar retroativo e guardar meses_atraso",
         "É pagamento real, e a gold separa o valor do mês do valor retroativo",
         br(total["retroativo"])],
        ["Valores extremos da parcela",
         "Manter e marcar valor_extremo quando o valor é menor que R$ 20 ou de R$ 3.000 para cima",
         "Regra explícita, porque são raros, plausíveis (ajustes e acumulados) e não mudam a mediana",
         br(total["valor_extremo"])],
        ["Município identificado pelo código SIAFI, não pelo código IBGE",
         "Trocar pelo código IBGE e trazer nome, UF e região do IBGE (tabela SIAFI ↔ IBGE)",
         "O código IBGE liga a base à população e à malha do mapa",
         br(total["sem_codigo_ibge"]) + " sem correspondência"],
        ["Nome do município diferente do IBGE (grafia ou mudança de nome, ex.: Embu → Embu das Artes)",
         "Usar o nome do IBGE",
         "Mantém cada município com um nome só em toda a série",
         br(total["nome_diferente_do_ibge"])],
        ["Valor da parcela ilegível",
         "Viraria nulo",
         "Nenhuma linha fora do formato 1.234,56",
         br(total["valor_ilegivel"])],
        ["Mesmo NIS pago em dois municípios no mesmo mês",
         "Manter, e na gold a família conta nos dois municípios",
         "Caso raro, registrado assim pelo Portal",
         br(total["nis_em_dois_municipios"]) + " NIS"],
        ["Parcela do Auxílio Brasil abaixo do piso legal (R$ 400 de dez/2021 a jul/2022 e R$ 600 depois)",
         "Manter e declarar a limitação no relatório",
         "Parte da transferência (benefício extraordinário) parece não estar nesta base",
         br(total["abaixo_do_piso"])],
        ["População municipal de 2022 e 2023 sem estimativa do IBGE",
         "Interpolar em linha reta entre as estimativas de 2021 e 2024, e o Censo 2022 fica só como conferência",
         "O Censo é contagem e fica ~5% abaixo das estimativas: criaria um salto falso na taxa",
         br(n_pop) + " município × ano"],
        ["Município × mês da gold sem população",
         "Nenhum tratamento foi necessário",
         "Todos os códigos IBGE da base estão nas estimativas",
         br(sem_pop)],
    ]

    cabecalho = ["problema", "decisão", "justificativa", "linhas afetadas"]
    with open(raiz / "reports" / "log_limpeza.csv", "w", encoding="utf-8", newline="") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(cabecalho)
        escritor.writerows(log)

    texto = ["# Log de limpeza (Etapa 3)", "",
             "Gerado por `src/limpeza.py`. Bronze: " + br(total["linhas_bronze"]) + " linhas e silver: "
             + br(total["linhas_silver"]) + " linhas.", "",
             "| " + " | ".join(cabecalho) + " |", "|---|---|---|---:|"]
    for item in log:
        texto.append("| " + " | ".join(item) + " |")
    (raiz / "reports" / "log_limpeza.md").write_text("\n".join(texto) + "\n", encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Limpeza: bronze -> silver e gold")
    parser.add_argument("--refazer", action="store_true", help="processa de novo os meses já feitos")
    parser.add_argument("--mes", help="processa só este mês (AAAAMM), usado pelo próprio script")
    args = parser.parse_args()

    arquivos = periodos_do_recorte()
    if args.mes:
        inicio = time.time()
        bases_do_mes = [b for b, f, p in arquivos if p == args.mes]
        bases = []
        for base in reversed(list(fases)):  # o programa mais novo primeiro
            if base in bases_do_mes:
                bases.append(base)
        con = conectar()
        carregar_municipios(con)
        n = processar_mes(con, args.mes, bases)
        con.close()
        print(f"{args.mes}: {n['linhas_bronze']} linhas na bronze, {n['linhas_silver']} na silver "
              f"({round(time.time() - inicio)}s)", flush=True)
        return

    meses = sorted(set(p for b, f, p in arquivos))
    for periodo in meses:
        feito = all(a.exists() for a in arquivos_do_mes(periodo))
        if feito and not args.refazer:
            continue
        # cada mês roda num processo separado: o Windows só devolve a memória quando o processo termina,
        # e com um processo só cada mês ficava mais lento que o anterior
        subprocess.run([sys.executable, __file__, "--mes", periodo], check=True)

    con = conectar()
    sem_pop = montar_gold(con)
    montar_log(con, sem_pop)
    con.close()
    remover_arvore(pasta_tmp)
    print("Silver em", silver / "pagamentos")
    print("Gold em", gold)
    print("Log em", raiz / "reports" / "log_limpeza.md")


if __name__ == "__main__":
    main()
