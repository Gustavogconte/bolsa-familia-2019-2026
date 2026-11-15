"""Conversão dos arquivos brutos (ZIP com CSV ';' latin-1/UTF-8) para Parquet particionado.

Fluxo por tabela (cada CSV dentro do ZIP):
1. extrai o CSV em streaming, detecta a codificação e grava uma cópia temporária em UTF-8
   (contando linhas físicas, bytes nulos e caracteres irrecuperáveis);
2. lê o cabeçalho e detecta o separador;
3. o DuckDB lê o CSV com TODAS as colunas como texto (camada bronze = dado bruto, sem conversão
   de tipo) e grava Parquet zstd particionado por ano e, quando a tabela tem UF, por UF;
4. linhas malformadas não derrubam a conversão: são contadas e uma amostra vai para
   data/metadados/rejeitadas/ para análise na EDA.
"""

from __future__ import annotations

import codecs
import csv
import io
import logging
import re
import shutil
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import duckdb

from catalogo import Base
from util import (Pastas, caminho_sql, espaco_livre, fmt_bytes, ident_sql, literal_sql, padronizar_nome,
                  remover_arvore)

log = logging.getLogger("coleta")

AMOSTRA = 4 * 1024 * 1024
BLOCO = 8 * 1024 * 1024
DELIMITADORES = [";", ",", "\t", "|"]

# Limites do DuckDB para CSV maiores que a memória (ver comentário em converter_csv)
MAX_LINHA_CSV = 4 * 1024 * 1024
THREADS_PADRAO = 4
LIMIAR_DESCARGA_PARTICAO = 50_000

# Coluna com nome de pessoa (removida só com --remover-nomes): nas três bases é o NOME FAVORECIDO
RE_NOME_PESSOA = re.compile(r"^nome_(do_)?favorecido$|^nome$")


@dataclass
class OpcoesConversao:
    memoria: str | None = None  # ex.: '4GB'; None = padrão do DuckDB (80% da RAM)
    threads: int | None = None
    ufs: tuple[str, ...] = ()  # filtro opcional de UF
    remover_nomes: bool = False
    min_livre: int = 5 * 1024**3

    def assinatura(self) -> dict:
        return {"ufs": sorted(self.ufs), "remover_nomes": self.remover_nomes}


@dataclass
class InfoExtracao:
    encoding: str
    delimitador: str
    colunas: list[str]
    linhas_fisicas: int
    bytes_origem: int
    bytes_utf8: int
    substituicoes: int
    nul_removidos: int
    campos_dados: int
    avisos: list[str] = field(default_factory=list)
    precisa_padding: bool = False  # linhas com menos campos que o cabeçalho de forma sistemática


# --------------------------------------------------------------------------- ZIP

def nome_membro(info: zipfile.ZipInfo) -> str:
    """Corrige nomes de arquivo com acento gravados sem a flag UTF-8 (o zipfile decodifica como cp437)."""
    if info.flag_bits & 0x800:
        return info.filename
    bruto = info.filename.encode("cp437", errors="replace")
    for enc in ("utf-8", "cp1252"):
        try:
            return bruto.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def membros_csv(caminho_zip: Path) -> list[tuple[zipfile.ZipInfo, str]]:
    with zipfile.ZipFile(caminho_zip) as zf:
        saida = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            nome = nome_membro(info)
            if nome.lower().endswith((".csv", ".txt")):
                saida.append((info, nome))
        return saida


def nome_tabela(nome_arquivo: str) -> str:
    """'202401_NovoBolsaFamilia.csv' -> 'novo_bolsa_familia' (só usado se o ZIP trouxer mais de um CSV)."""
    nome = Path(nome_arquivo.replace("\\", "/")).name
    nome = re.sub(r"\.(csv|txt)$", "", nome, flags=re.I)
    nome = re.sub(r"^\d{4,8}[_\-]+", "", nome)
    nome = re.sub(r"[_\-]+\d{4,8}$", "", nome)
    return padronizar_nome(nome) or "tabela"


# --------------------------------------------------------------------------- codificação

def detectar_encoding(amostra: bytes) -> str:
    """'utf-8' se a amostra é UTF-8 válido com acentos; 'cp1252' se não é UTF-8; 'ascii' se não dá para saber."""
    corte = amostra
    # não deixa um caractere multibyte cortado no fim da amostra derrubar o teste
    for _ in range(3):
        try:
            texto = corte.decode("utf-8")
            return "utf-8" if not texto.isascii() else "ascii"
        except UnicodeDecodeError as e:
            if e.start >= len(corte) - 3:
                corte = corte[: e.start]
                continue
            return "cp1252"
    return "cp1252"


class Transcodificador:
    """Decodifica blocos de bytes de forma incremental, trocando para cp1252 se aparecer byte inválido em UTF-8."""

    def __init__(self, encoding_inicial: str):
        self.modo = "cp1252" if encoding_inicial == "cp1252" else "utf-8"
        self.estrito = encoding_inicial == "ascii"
        self._utf8 = codecs.getincrementaldecoder("utf-8")("strict" if self.estrito else "replace")
        self.viu_nao_ascii = False
        self.encoding_final = encoding_inicial
        self.substituicoes = 0

    def decodificar(self, dados: bytes, final: bool = False) -> str:
        if self.modo == "cp1252":
            texto = dados.decode("cp1252", errors="replace")
            self.substituicoes += texto.count("�")
            return texto
        try:
            texto = self._utf8.decode(dados, final)
        except UnicodeDecodeError:
            pendente = self._utf8.getstate()[0]
            if not self.viu_nao_ascii:
                # até aqui só havia ASCII (igual nas duas codificações): segue em cp1252 sem perda
                self.modo = "cp1252"
                self.encoding_final = "cp1252"
                texto = (pendente + dados).decode("cp1252", errors="replace")
                self.substituicoes += texto.count("�")
                return texto
            # UTF-8 com bytes inválidos no meio: substitui o que não der para ler
            self._utf8 = codecs.getincrementaldecoder("utf-8")("replace")
            self.encoding_final = "utf-8 (com bytes inválidos)"
            texto = self._utf8.decode(pendente + dados, final)
            self.substituicoes += texto.count("�")
            return texto
        if not self.estrito:
            self.substituicoes += texto.count("�")
        if not self.viu_nao_ascii and not texto.isascii():
            self.viu_nao_ascii = True
            if self.encoding_final == "ascii":
                self.encoding_final = "utf-8"
        return texto


def _cabecalho(linha: str) -> tuple[str, list[str]]:
    melhor, campos_melhor = ";", [linha]
    for d in DELIMITADORES:
        campos = next(csv.reader([linha], delimiter=d, quotechar='"'))
        if len(campos) > len(campos_melhor):
            melhor, campos_melhor = d, campos
    nomes, vistos = [], Counter()
    for i, c in enumerate(campos_melhor, 1):
        c = c.strip().strip("﻿").strip()
        if not c:
            c = f"_coluna_sem_nome_{i}"
        vistos[c.lower()] += 1
        if vistos[c.lower()] > 1:
            c = f"{c}_{vistos[c.lower()]}"
        nomes.append(c)
    return melhor, nomes


def _campos_por_linha(texto: str, delimitador: str, max_linhas: int = 5000) -> int | None:
    corte = texto.rfind("\n")
    if corte <= 0:
        return None
    leitor = csv.reader(io.StringIO(texto[:corte]), delimiter=delimitador, quotechar='"')
    contagem = Counter()
    for i, linha in enumerate(leitor):
        if i == 0:
            continue  # cabeçalho
        if i > max_linhas:
            break
        if linha:
            contagem[len(linha)] += 1
    return contagem.most_common(1)[0][0] if contagem else None


def extrair_utf8(abrir: Callable[[], io.BufferedIOBase], bytes_origem: int, destino: Path) -> InfoExtracao:
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas = 0
    nul = 0
    escrito = 0
    ultimo_char = ""
    with abrir() as src, open(destino, "wb") as out:
        amostra = src.read(AMOSTRA)
        enc = detectar_encoding(amostra)
        tc = Transcodificador(enc)
        texto = tc.decodificar(amostra)
        if texto.startswith("﻿"):
            texto = texto[1:]
        primeira = texto.split("\n", 1)[0].rstrip("\r")
        delim, colunas = _cabecalho(primeira)
        campos = _campos_por_linha(texto, delim) or len(colunas)
        avisos = []
        precisa_padding = False
        if campos > len(colunas):
            extras = [f"_coluna_extra_{i}" for i in range(1, campos - len(colunas) + 1)]
            avisos.append(f"linhas têm {campos} campos e o cabeçalho {len(colunas)}; "
                          f"criadas colunas {', '.join(extras)}")
            colunas = colunas + extras
        elif campos < len(colunas):
            sobra = colunas[campos:]
            if all(c.startswith("_coluna_sem_nome_") for c in sobra):
                colunas = colunas[:campos]  # separador sobrando no fim do cabeçalho
            else:
                precisa_padding = True
                avisos.append(f"linhas têm {campos} campos e o cabeçalho {len(colunas)}; "
                              f"colunas finais completadas com vazio")
        while True:
            if "\x00" in texto:
                nul += texto.count("\x00")
                texto = texto.replace("\x00", "")
            if texto:
                linhas += texto.count("\n")
                ultimo_char = texto[-1]
                dados = texto.encode("utf-8")
                out.write(dados)
                escrito += len(dados)
            bloco = src.read(BLOCO)
            if not bloco:
                resto = tc.decodificar(b"", final=True)
                if resto:
                    linhas += resto.count("\n")
                    ultimo_char = resto[-1]
                    dados = resto.encode("utf-8")
                    out.write(dados)
                    escrito += len(dados)
                break
            texto = tc.decodificar(bloco)
    if escrito and ultimo_char != "\n":
        linhas += 1
    return InfoExtracao(encoding=tc.encoding_final, delimitador=delim, colunas=colunas, linhas_fisicas=linhas,
                        bytes_origem=bytes_origem, bytes_utf8=escrito, substituicoes=tc.substituicoes,
                        nul_removidos=nul, campos_dados=campos, avisos=avisos, precisa_padding=precisa_padding)


# --------------------------------------------------------------------------- DuckDB

def _expr_uf(coluna: str) -> str:
    # a UF vem como sigla nas três bases; o que não for sigla vai para a partição sem_uf
    c = f"trim({ident_sql(coluna)})"
    return f"CASE WHEN regexp_matches(upper({c}), '^[A-Z]{{2}}$') THEN upper({c}) ELSE 'sem_uf' END"


def converter_csv(csv_utf8: Path, info: InfoExtracao, base: Base, tabela: str, periodo: str, membro: str,
                  pastas: Pastas, opcoes: OpcoesConversao) -> dict:
    """Converte um CSV UTF-8 em Parquet particionado e devolve as métricas da conversão."""
    t0 = time.monotonic()
    destino_tabela = pastas.bronze / base.chave / tabela
    tmp_saida = pastas.tmp / "parquet" / f"{base.chave}_{tabela}_{periodo}"
    tmp_duck = pastas.tmp / "duckdb"
    remover_arvore(tmp_saida)
    remover_arvore(tmp_duck)  # spill de uma execução anterior interrompida (pode ter dezenas de GB)
    tmp_saida.parent.mkdir(parents=True, exist_ok=True)
    tmp_duck.mkdir(parents=True, exist_ok=True)

    normalizadas = {padronizar_nome(c): c for c in info.colunas}
    coluna_uf = normalizadas.get("uf")
    col_ano = "ano" if "ano" not in normalizadas else "ano_particao"
    col_uf = "uf_sigla" if "uf_sigla" not in normalizadas else "uf_particao"
    ano_expr = literal_sql(periodo[:4])  # partição pelo ano do arquivo mensal

    excluir = []
    if opcoes.remover_nomes:
        excluir = [c for n, c in normalizadas.items() if RE_NOME_PESSOA.match(n)]
    selecao = "*" if not excluir else "* EXCLUDE (" + ", ".join(ident_sql(c) for c in excluir) + ")"

    colspec = "{" + ", ".join(f"{literal_sql(c)}: 'VARCHAR'" for c in info.colunas) + "}"
    leitura = (f"read_csv({caminho_sql(csv_utf8)}, delim={literal_sql(info.delimitador)}, quote='\"', "
               f"escape='\"', header=true, auto_detect=false, columns={colspec}, "
               # null_padding só quando o arquivo inteiro tem linhas curtas: o leitor paralelo do DuckDB não
               # aceita null_padding junto com quebras de linha dentro de aspas. Sem ele, linhas curtas isoladas
               # vão para a tabela de rejeitadas (contadas e amostradas) em vez de entrarem incompletas.
               f"null_padding={'true' if info.precisa_padding else 'false'}, "
               f"parallel={'false' if info.precisa_padding else 'true'}, "
               f"store_rejects=true, rejects_table='rejeitadas', rejects_scan='rejeitadas_scan', "
               # 4 MiB por linha (as linhas destas bases têm poucas centenas de bytes). Cada thread do leitor
               # reserva buffers desse tamanho; com 32 MiB × 12 threads o DuckDB estourava a memória.
               f"max_line_size={MAX_LINHA_CSV})")
    extras = [f"{literal_sql(periodo)} AS _periodo_arquivo", f"{literal_sql(membro)} AS _arquivo_origem",
              f"{ano_expr} AS {col_ano}"]
    particoes = [col_ano]
    if coluna_uf:
        extras.append(f"{_expr_uf(coluna_uf)} AS {col_uf}")
        particoes.append(col_uf)
    consulta = f"SELECT {selecao}, {', '.join(extras)} FROM {leitura}"
    if opcoes.ufs and coluna_uf:
        lista = ", ".join(literal_sql(u.upper()) for u in opcoes.ufs)
        consulta = f"SELECT * FROM ({consulta}) WHERE {col_uf} IN ({lista})"

    con = duckdb.connect()
    try:
        if opcoes.memoria:
            con.execute(f"SET memory_limit={literal_sql(opcoes.memoria)}")
        # Threads limitadas por padrão: o leitor de CSV e a gravação particionada guardam buffers POR THREAD
        # (e, na gravação, por partição UF). Com 12 threads um CSV grande ficava horas mandando tudo para o
        # disco (spill); com 4 threads, max_line_size de 4 MiB e descarga a cada 50 mil linhas, 2,7 GB de CSV
        # convertem em segundos com 2 GB de memória. Quem tiver mais RAM pode subir com --threads.
        con.execute(f"SET threads={int(opcoes.threads or THREADS_PADRAO)}")
        con.execute(f"SET partitioned_write_flush_threshold={LIMIAR_DESCARGA_PARTICAO}")
        con.execute("SET preserve_insertion_order=false")
        con.execute(f"SET temp_directory={caminho_sql(tmp_duck)}")
        n_linhas = con.execute(
            f"COPY ({consulta}) TO {caminho_sql(tmp_saida)} "
            f"(FORMAT parquet, COMPRESSION zstd, PARTITION_BY ({', '.join(particoes)}), OVERWRITE_OR_IGNORE true)"
        ).fetchone()[0]
        try:
            n_rejeitadas = con.execute("SELECT count(DISTINCT line) FROM rejeitadas").fetchone()[0]
        except duckdb.CatalogException:
            n_rejeitadas = 0  # a tabela só é criada quando há linha rejeitada
        amostra_rej = None
        if n_rejeitadas:
            pasta_rej = pastas.metadados / "rejeitadas"
            pasta_rej.mkdir(parents=True, exist_ok=True)
            amostra_rej = pasta_rej / f"{base.chave}__{tabela}__{periodo}.csv"
            con.execute(
                f"COPY (SELECT line AS linha, column_name AS coluna, error_type AS tipo_erro, "
                f"error_message AS mensagem, csv_line AS conteudo FROM rejeitadas ORDER BY line LIMIT 1000) "
                f"TO {caminho_sql(amostra_rej)} (HEADER, DELIMITER ';')")
    finally:
        con.close()

    # move os arquivos gerados para data/bronze/<base>/<tabela>/ano=.../[uf_sigla=.../]<periodo>.parquet
    for antigo in list(destino_tabela.glob(f"{col_ano}=*/**/{periodo}.parquet")) + \
            list(destino_tabela.glob(f"{col_ano}=*/**/{periodo}_[0-9]*.parquet")):
        antigo.unlink()
    gerados = sorted(tmp_saida.rglob("*.parquet"))
    finais: list[Path] = []
    por_pasta: dict[Path, int] = {}
    for g in gerados:
        rel = g.parent.relative_to(tmp_saida)
        pasta_final = destino_tabela / rel
        pasta_final.mkdir(parents=True, exist_ok=True)
        i = por_pasta.get(pasta_final, 0)
        por_pasta[pasta_final] = i + 1
        alvo = pasta_final / (f"{periodo}.parquet" if i == 0 else f"{periodo}_{i}.parquet")
        shutil.move(str(g), str(alvo))
        finais.append(alvo)
    remover_arvore(tmp_saida)

    bytes_parquet = sum(f.stat().st_size for f in finais)
    pref = f"{col_ano}="
    anos = sorted({p.parent.name.split("=", 1)[1] for p in finais if p.parent.name.startswith(pref)} |
                  {p.parent.parent.name.split("=", 1)[1] for p in finais if p.parent.parent.name.startswith(pref)})
    avisos = list(info.avisos)
    esperado = max(0, info.linhas_fisicas - 1)
    lidas = n_linhas + n_rejeitadas
    if not opcoes.ufs and esperado and lidas < 0.98 * esperado:
        avisos.append(f"linhas lidas+rejeitadas ({lidas}) bem abaixo das linhas físicas ({esperado}); "
                      f"pode haver campos com quebra de linha")
    if info.substituicoes:
        avisos.append(f"{info.substituicoes} caracteres irrecuperáveis substituídos por U+FFFD")
    if info.nul_removidos:
        avisos.append(f"{info.nul_removidos} bytes nulos (\\x00) removidos")
    if n_rejeitadas:
        avisos.append(f"{n_rejeitadas} linhas malformadas fora do Parquet (amostra em {amostra_rej.name})")
    return {
        "tabela": tabela,
        "membro": membro,
        "encoding": info.encoding,
        "delimitador": info.delimitador,
        "n_colunas": len(info.colunas),
        "colunas": info.colunas,
        "colunas_removidas": excluir,
        "coluna_uf": coluna_uf,
        "particoes": particoes,
        "null_padding": info.precisa_padding,
        "linhas_fisicas": info.linhas_fisicas,
        "n_linhas": int(n_linhas),
        "n_rejeitadas": int(n_rejeitadas),
        "bytes_csv": info.bytes_origem,
        "bytes_csv_utf8": info.bytes_utf8,
        "bytes_parquet": bytes_parquet,
        "n_arquivos_parquet": len(finais),
        "anos_particao": anos,
        "arquivos_parquet": [str(f.relative_to(pastas.raiz)).replace("\\", "/") for f in finais],
        "tempo_conversao_s": round(time.monotonic() - t0, 1),
        "avisos": avisos,
    }


class _MembroZip:
    """Abre um CSV de dentro do ZIP e fecha o ZIP junto (no Windows, arquivo aberto não pode ser apagado)."""

    def __init__(self, caminho: Path, info: zipfile.ZipInfo):
        self.caminho, self.info = caminho, info

    def __enter__(self):
        self.zf = zipfile.ZipFile(self.caminho)
        self.f = self.zf.open(self.info)
        return self.f

    def __exit__(self, *exc):
        self.f.close()
        self.zf.close()
        return False


def processar(caminho: Path, tipo: str, base: Base, periodo: str, pastas: Pastas, opcoes: OpcoesConversao) -> list[dict]:
    """Converte todas as tabelas de um arquivo baixado. Levanta exceção se alguma falhar."""
    tabelas = []
    if tipo == "zip":
        membros = membros_csv(caminho)
        if not membros:
            raise ValueError("ZIP sem arquivo CSV")
        usados: Counter = Counter()
        for info_zip, nome in membros:
            tabela = nome_tabela(nome) if len(membros) > 1 else base.chave
            usados[tabela] += 1
            if usados[tabela] > 1:
                tabela = f"{tabela}_{usados[tabela]}"
            tabelas.append((tabela, nome, info_zip.file_size,
                            (lambda i=info_zip: _MembroZip(caminho, i))))
    else:
        tabelas.append((base.chave, caminho.name, caminho.stat().st_size, lambda: open(caminho, "rb")))

    resultados = []
    for tabela, membro, tamanho, abrir in tabelas:
        necessario = int(tamanho * 1.6) + opcoes.min_livre
        livre = espaco_livre(pastas.tmp)
        if livre < necessario:
            raise OSError(f"espaço insuficiente para converter {membro}: precisa ~{fmt_bytes(necessario)}, "
                          f"livres {fmt_bytes(livre)}")
        tmp_csv = pastas.tmp / "csv" / f"{base.chave}_{tabela}_{periodo}.csv"
        t0 = time.monotonic()
        try:
            info = extrair_utf8(abrir, tamanho, tmp_csv)
            t_extr = round(time.monotonic() - t0, 1)
            r = converter_csv(tmp_csv, info, base, tabela, periodo, membro, pastas, opcoes)
            r["tempo_extracao_s"] = t_extr
            resultados.append(r)
            log.info("  %s %s [%s] %s linhas, %d colunas, %s CSV -> %s Parquet (%s)%s",
                     base.chave, periodo, tabela, f"{r['n_linhas']:,}".replace(",", "."), r["n_colunas"],
                     fmt_bytes(r["bytes_csv"]), fmt_bytes(r["bytes_parquet"]), info.encoding,
                     ("; " + " | ".join(r["avisos"])) if r["avisos"] else "")
        finally:
            tmp_csv.unlink(missing_ok=True)
    return resultados
