"""Coleta da segunda fonte: população do IBGE, tabela de municípios SIAFI ↔ IBGE e malhas para mapas.

Por que cada uma:
- IBGE/SIDRA 6579 (estimativas anuais) e 4709 (Censo 2022): denominador para taxas por 100 mil
  habitantes por município e UF. O IBGE não publicou estimativa municipal para 2022 nem para 2023; o que
  fazer com esses anos é decisão da etapa de limpeza, não da coleta.
- Tabela SIAFI ↔ IBGE: as três bases identificam o município pelo código SIAFI (4 dígitos), enquanto o
  IBGE e as malhas usam o código IBGE (7 dígitos).
- IBGE Localidades: região, UF e regiões geográficas de cada município (variáveis derivadas).
- Malhas do IBGE (2025): polígonos de UF e municípios para os mapas coropléticos.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from portal import _dormir, _espera, sessao
from util import Pastas, agora_iso, sha256_arquivo

log = logging.getLogger("coleta")

URL_SIDRA = os.environ.get("COLETA_URL_SIDRA", "https://apisidra.ibge.gov.br/values").rstrip("/")
URL_SIAFI = os.environ.get(
    "COLETA_URL_SIAFI", "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv")
URL_LOCALIDADES = os.environ.get(
    "COLETA_URL_LOCALIDADES", "https://servicodados.ibge.gov.br/api/v1/localidades/municipios")
URL_MALHAS = os.environ.get(
    "COLETA_URL_MALHAS",
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2025/Brasil",
).rstrip("/")
MALHAS = ["BR_UF_2025.zip", "BR_Municipios_2025.zip"]


def _get(url: str, tentativas: int = 5, **kw) -> requests.Response:
    ultimo = None
    timeout = kw.pop("timeout", (20, 180))
    for t in range(1, tentativas + 1):
        try:
            r = sessao().get(url, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            ultimo = IOError(f"HTTP {r.status_code}")
            if r.status_code in (400, 404):
                break
        except requests.RequestException as e:
            ultimo = e
        if t < tentativas:
            log.warning("  falha em %s (%s); tentando de novo", url, ultimo)
            _dormir(_espera(t))
    raise IOError(f"{type(ultimo).__name__}: {ultimo}")


def _sidra(consulta: str) -> list[dict]:
    """Consulta a API do SIDRA e devolve as linhas com os rótulos do próprio SIDRA como nomes de coluna."""
    r = _get(f"{URL_SIDRA}/{consulta.strip('/')}", headers={"Accept": "application/json"})
    dados = r.json()
    if not dados:
        return []
    cabecalho = {k: v for k, v in dados[0].items() if v}
    return [{cabecalho[k]: (str(v) if v is not None else None) for k, v in linha.items() if k in cabecalho}
            for linha in dados[1:]]


def _salvar(linhas: list[dict], destino: Path) -> int:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not linhas:
        return 0
    colunas = list(dict.fromkeys(k for l in linhas for k in l))
    tabela = pa.table({c: pa.array([l.get(c) for l in linhas], type=pa.string()) for c in colunas})
    pq.write_table(tabela, destino, compression="zstd")
    return len(linhas)


def caminho_no_projeto(caminho):
    # caminho relativo à pasta do projeto, para o CSV não guardar o caminho (e o usuário) desta máquina
    raiz_projeto = Path(__file__).resolve().parents[1]
    try:
        return caminho.resolve().relative_to(raiz_projeto).as_posix()
    except ValueError:
        return caminho.as_posix()


def _meta(fonte: str, descricao: str, url: str, destino: Path | None, linhas: int | None, colunas: int | None,
          status: str = "ok", erro: str | None = None) -> dict:
    existe = destino is not None and destino.exists()
    return {"fonte": fonte, "descricao": descricao, "url": url, "data_download": agora_iso(), "status": status,
            "arquivo": caminho_no_projeto(destino) if destino else "", "linhas": linhas, "colunas": colunas,
            "bytes": destino.stat().st_size if existe else None,
            "sha256": sha256_arquivo(destino) if existe else None, "erro": erro}


def populacao(pastas: Pastas) -> list[dict]:
    raiz = pastas.bronze / "referencias"
    metas = []
    # anos disponíveis na tabela 6579 (consulta leve no nível Brasil)
    url = f"{URL_SIDRA}/t/6579/n1/all/v/9324/p/all"
    try:
        anos = sorted({l.get("Ano (Código)") or l.get("Ano") for l in _sidra("t/6579/n1/all/v/9324/p/all")})
        anos = [a for a in anos if a]
        log.info("  IBGE 6579: anos com estimativa municipal: %s", ", ".join(anos))
    except Exception as e:  # noqa: BLE001
        metas.append(_meta("IBGE/SIDRA 6579", "anos disponíveis", url, None, None, None, "erro", str(e)))
        anos = []
    for ano in anos:
        destino = raiz / "ibge_estimativas_populacao_municipios" / f"ano={ano}" / "populacao.parquet"
        url = f"{URL_SIDRA}/t/6579/n6/all/v/9324/p/{ano}"
        try:
            linhas = _sidra(f"t/6579/n6/all/v/9324/p/{ano}")
            n = _salvar(linhas, destino)
            metas.append(_meta("IBGE/SIDRA 6579", f"população estimada por município, {ano}", url, destino, n,
                               len(linhas[0]) if linhas else 0))
            log.info("  IBGE 6579 %s: %d municípios", ano, n)
        except Exception as e:  # noqa: BLE001
            metas.append(_meta("IBGE/SIDRA 6579", f"população estimada por município, {ano}", url, None, None,
                               None, "erro", str(e)))
            log.error("  IBGE 6579 %s: %s", ano, e)
        time.sleep(1)
    consultas = [
        ("IBGE/SIDRA 6579", "população estimada por UF, todos os anos", "t/6579/n3/all/v/9324/p/all",
         raiz / "ibge_estimativas_populacao_uf" / "populacao_uf.parquet"),
        ("IBGE/SIDRA 4709", "população residente por município, Censo 2022", "t/4709/n6/all/v/93/p/2022",
         raiz / "ibge_censo2022_populacao_municipios" / "populacao.parquet"),
        ("IBGE/SIDRA 4709", "população residente por UF, Censo 2022", "t/4709/n3/all/v/93/p/2022",
         raiz / "ibge_censo2022_populacao_uf" / "populacao_uf.parquet"),
    ]
    for fonte, desc, consulta, destino in consultas:
        url = f"{URL_SIDRA}/{consulta}"
        try:
            linhas = _sidra(consulta)
            n = _salvar(linhas, destino)
            metas.append(_meta(fonte, desc, url, destino, n, len(linhas[0]) if linhas else 0))
            log.info("  %s: %s (%d linhas)", fonte, desc, n)
        except Exception as e:  # noqa: BLE001
            metas.append(_meta(fonte, desc, url, None, None, None, "erro", str(e)))
            log.error("  %s %s: %s", fonte, desc, e)
    return metas


def municipios_siafi(pastas: Pastas) -> list[dict]:
    pasta = pastas.bronze / "referencias" / "municipios_siafi_ibge"
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        r = _get(URL_SIAFI)
        bruto = pasta / "municipios.csv"
        bruto.write_bytes(r.content)
        linhas = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
        destino = pasta / "municipios.parquet"
        n = _salvar(linhas, destino)  # tudo como texto: preserva zeros à esquerda do código SIAFI
        log.info("  tabela SIAFI ↔ IBGE: %d municípios", n)
        return [_meta("kelvins/municipios-brasileiros (MIT)", "códigos IBGE e SIAFI dos municípios", URL_SIAFI,
                      destino, n, len(linhas[0]) if linhas else 0)]
    except Exception as e:  # noqa: BLE001
        log.error("  tabela SIAFI ↔ IBGE: %s", e)
        return [_meta("kelvins/municipios-brasileiros (MIT)", "códigos IBGE e SIAFI dos municípios", URL_SIAFI,
                      None, None, None, "erro", str(e))]


def _achatar_municipio(m: dict) -> dict:
    micro = m.get("microrregiao") or {}
    meso = micro.get("mesorregiao") or {}
    imediata = m.get("regiao-imediata") or {}
    intermediaria = imediata.get("regiao-intermediaria") or {}
    uf = (meso.get("UF") or intermediaria.get("UF") or {})
    regiao = uf.get("regiao") or {}
    return {
        "id_municipio": m.get("id"), "nome": m.get("nome"),
        "id_microrregiao": micro.get("id"), "microrregiao": micro.get("nome"),
        "id_mesorregiao": meso.get("id"), "mesorregiao": meso.get("nome"),
        "id_regiao_imediata": imediata.get("id"), "regiao_imediata": imediata.get("nome"),
        "id_regiao_intermediaria": intermediaria.get("id"), "regiao_intermediaria": intermediaria.get("nome"),
        "id_uf": uf.get("id"), "sigla_uf": uf.get("sigla"), "uf": uf.get("nome"),
        "id_regiao": regiao.get("id"), "sigla_regiao": regiao.get("sigla"), "regiao": regiao.get("nome"),
    }


def localidades(pastas: Pastas) -> list[dict]:
    destino = pastas.bronze / "referencias" / "ibge_localidades_municipios" / "municipios.parquet"
    try:
        r = _get(URL_LOCALIDADES)
        dados = r.json()
        (destino.parent / "municipios.json").parent.mkdir(parents=True, exist_ok=True)
        (destino.parent / "municipios.json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        linhas = [{k: (str(v) if v is not None else None) for k, v in _achatar_municipio(m).items()} for m in dados]
        n = _salvar(linhas, destino)
        log.info("  IBGE Localidades: %d municípios", n)
        return [_meta("IBGE Localidades", "municípios com UF, região e regiões geográficas", URL_LOCALIDADES,
                      destino, n, len(linhas[0]) if linhas else 0)]
    except Exception as e:  # noqa: BLE001
        log.warning("  IBGE Localidades indisponível (%s). UF e região também saem do próprio código IBGE: "
                    "2 primeiros dígitos = UF, 1º dígito = região.", e)
        return [_meta("IBGE Localidades", "municípios com UF, região e regiões geográficas", URL_LOCALIDADES,
                      None, None, None, "erro", str(e))]


def malhas(pastas: Pastas) -> list[dict]:
    pasta = pastas.bronze / "referencias" / "malhas_ibge_2025"
    pasta.mkdir(parents=True, exist_ok=True)
    metas = []
    for nome in MALHAS:
        url = f"{URL_MALHAS}/{nome}"
        destino = pasta / nome
        if destino.exists() and destino.stat().st_size > 0:
            metas.append(_meta("IBGE Malhas 2025", f"malha {nome} (já baixada)", url, destino, None, None))
            continue
        try:
            with _get(url, stream=True) as r:
                parcial = destino.with_suffix(".part")
                with open(parcial, "wb") as f:
                    for bloco in r.iter_content(1024 * 1024):
                        f.write(bloco)
                parcial.replace(destino)
            metas.append(_meta("IBGE Malhas 2025", f"malha {nome} (shapefile zipado)", url, destino, None, None))
            log.info("  malha %s baixada", nome)
        except Exception as e:  # noqa: BLE001
            metas.append(_meta("IBGE Malhas 2025", f"malha {nome}", url, None, None, None, "erro", str(e)))
            log.error("  malha %s: %s", nome, e)
    return metas


def coletar(pastas: Pastas, sem_malhas: bool = False) -> list[dict]:
    metas = []
    log.info("Segunda fonte: população (IBGE/SIDRA)")
    metas += populacao(pastas)
    log.info("Segunda fonte: tabela de municípios SIAFI ↔ IBGE")
    metas += municipios_siafi(pastas)
    log.info("Segunda fonte: IBGE Localidades")
    metas += localidades(pastas)
    if not sem_malhas:
        log.info("Segunda fonte: malhas municipais do IBGE")
        metas += malhas(pastas)
    return metas
