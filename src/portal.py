"""Acesso HTTP ao Portal da Transparência: descoberta de períodos, download e estimativa de volume."""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from catalogo import URL_PORTAL, Base
from util import VERSAO_SCRIPT, espaco_livre, fmt_bytes, sha256_arquivo

log = logging.getLogger("coleta")

# Permite apontar para outro endereço (testes, espelho). Em uso normal fica o endereço oficial.
URL_BASE = os.environ.get("COLETA_URL_PORTAL", URL_PORTAL).rstrip("/")
USER_AGENT = os.environ.get(
    "COLETA_USER_AGENT",
    f"Mozilla/5.0 (compatible; coleta-academica-portal-transparencia/{VERSAO_SCRIPT}; python-requests)",
)

PARAR = threading.Event()  # acionado no Ctrl+C para os downloads em andamento pararem com segurança
_local = threading.local()

_RE_PUSH = re.compile(r"arquivos\.push\(\s*\{(.*?)\}\s*\)", re.S)
_RE_CHAVE_VALOR = re.compile(r"[\"']?(\w+)[\"']?\s*:\s*[\"']([^\"']*)[\"']")


def sessao() -> requests.Session:
    """Uma sessão HTTP por thread (requests.Session não é segura para threads)."""
    s = getattr(_local, "sessao", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "pt-BR,pt;q=0.9"})
        _local.sessao = s
    return s


ESPERA_BASE = float(os.environ.get("COLETA_ESPERA_BASE", "5"))


def _espera(tentativa: int) -> float:
    """Espera exponencial entre tentativas: ~5 s, 15 s, 45 s, 2 min, 5 min (+ um pouco de aleatoriedade)."""
    return min(300.0, ESPERA_BASE * (3 ** (tentativa - 1))) + random.uniform(0, min(3.0, ESPERA_BASE))


def _dormir(segundos: float) -> None:
    fim = time.monotonic() + segundos
    while time.monotonic() < fim and not PARAR.is_set():
        time.sleep(min(1.0, fim - time.monotonic()))


# --------------------------------------------------------------------------- descoberta de períodos

def descobrir_periodos(base: Base, tentativas: int = 4) -> tuple[list[str] | None, str]:
    """Lê a página de download da base e extrai os períodos publicados.

    A página monta as listas de anos/meses a partir de chamadas `arquivos.push({ano, mes, dia, origem})`
    no HTML. Retorna (periodos, origem) onde origem é 'portal' ou o motivo da falha.
    """
    url = f"{URL_BASE}/download-de-dados/{base.pagina}"
    ultimo_erro = ""
    for t in range(1, tentativas + 1):
        try:
            r = sessao().get(url, timeout=(20, 60))
            if r.status_code == 200:
                periodos = set()
                for bloco in _RE_PUSH.findall(r.text):
                    campos = dict(_RE_CHAVE_VALOR.findall(bloco))
                    ano = campos.get("ano", "").strip()
                    mes = campos.get("mes", "").strip()
                    if not re.fullmatch(r"\d{4}", ano) or not re.fullmatch(r"\d{1,2}", mes):
                        continue
                    periodos.add(ano + mes.zfill(2))
                if periodos:
                    return sorted(periodos), "portal"
                ultimo_erro = "página sem a lista de arquivos (layout mudou?)"
                break
            ultimo_erro = f"HTTP {r.status_code}"
            if r.status_code in (403, 404):
                break
        except requests.RequestException as e:
            ultimo_erro = f"{type(e).__name__}: {e}"
        _dormir(_espera(t))
    return None, ultimo_erro


# --------------------------------------------------------------------------- download

@dataclass
class ResultadoDownload:
    base: str
    periodo: str
    url: str
    status: str = "erro"  # ok | indisponivel | erro | interrompido
    http_status: int | None = None
    url_final: str | None = None
    caminho: Path | None = None
    tipo: str | None = None  # zip | csv
    bytes: int = 0
    sha256: str | None = None
    tentativas: int = 0
    segundos: float = 0.0
    erro: str | None = None
    data_download: str | None = None
    retomado: bool = False
    avisos: list[str] = field(default_factory=list)


def _tipo_conteudo(caminho: Path) -> str | None:
    with open(caminho, "rb") as f:
        inicio = f.read(4096)
    if inicio.startswith(b"PK\x03\x04") or inicio.startswith(b"PK\x05\x06"):
        return "zip" if zipfile.is_zipfile(caminho) else "zip_truncado"
    amostra = inicio.lstrip(b"\xef\xbb\xbf").lower()
    if amostra.startswith(b"<!doctype") or amostra.startswith(b"<html") or b"<html" in amostra[:512]:
        return "html"
    if b";" in inicio or b"," in inicio:
        return "csv"
    return None


def baixar(base: Base, periodo: str, pasta_destino: Path, tentativas: int = 6,
           min_livre: int = 5 * 1024**3, pausa: float = 1.0) -> ResultadoDownload:
    """Baixa um arquivo com repetição, retomada (HTTP Range) e validação do ZIP."""
    from util import agora_iso

    url = base.url_download(periodo, URL_BASE)
    res = ResultadoDownload(base=base.chave, periodo=periodo, url=url)
    pasta_destino.mkdir(parents=True, exist_ok=True)
    final = pasta_destino / f"{base.chave}_{periodo}.zip"
    parcial = final.with_suffix(".zip.part")
    meta_parcial = final.with_suffix(".zip.part.validador")
    inicio_total = time.monotonic()

    # Já baixado e íntegro (ex.: execução anterior interrompida antes da conversão)
    if final.exists() and _tipo_conteudo(final) in ("zip", "csv"):
        res.status, res.caminho, res.tipo = "ok", final, _tipo_conteudo(final)
        res.bytes = final.stat().st_size
        res.sha256 = sha256_arquivo(final)
        res.data_download = agora_iso()
        res.avisos.append("arquivo já estava baixado; reaproveitado")
        return res

    for t in range(1, tentativas + 1):
        if PARAR.is_set():
            res.status, res.erro = "interrompido", "interrompido pelo usuário"
            break
        res.tentativas = t
        ja_tenho = parcial.stat().st_size if parcial.exists() else 0
        cabecalhos = {}
        if ja_tenho:
            cabecalhos["Range"] = f"bytes={ja_tenho}-"
            # If-Range: se o arquivo mudou no servidor desde o início do download, ele manda tudo de novo (200)
            # em vez de emendar pedaços de versões diferentes
            validador = meta_parcial.read_text(encoding="utf-8").strip() if meta_parcial.exists() else ""
            if validador:
                cabecalhos["If-Range"] = validador
        try:
            with sessao().get(url, stream=True, timeout=(30, 180), headers=cabecalhos, allow_redirects=True) as r:
                res.http_status = r.status_code
                res.url_final = r.url
                if r.status_code == 404:
                    res.status, res.erro = "indisponivel", "HTTP 404: arquivo não publicado"
                    break
                if r.status_code == 416 and ja_tenho:
                    # Range fora do arquivo: o .part provavelmente já está completo
                    if _tipo_conteudo(parcial) in ("zip", "csv"):
                        parcial.replace(final)
                        res.retomado = True
                        res.status = "ok"
                        break
                    parcial.unlink(missing_ok=True)
                    raise IOError("HTTP 416 com arquivo parcial inválido; recomeçando")
                if r.status_code not in (200, 206):
                    raise IOError(f"HTTP {r.status_code}")
                if r.status_code == 206:
                    m = re.match(r"bytes (\d+)-", r.headers.get("Content-Range", ""))
                    if not m or int(m.group(1)) != ja_tenho:
                        parcial.unlink(missing_ok=True)
                        raise IOError("o servidor devolveu um trecho diferente do pedido; recomeçando do zero")
                modo = "ab" if (r.status_code == 206 and ja_tenho) else "wb"
                if modo == "wb":
                    ja_tenho = 0
                    validador = r.headers.get("ETag") or r.headers.get("Last-Modified") or ""
                    if validador and not validador.startswith("W/"):
                        meta_parcial.write_text(validador, encoding="utf-8")
                    else:
                        meta_parcial.unlink(missing_ok=True)
                else:
                    res.retomado = True
                restante = int(r.headers.get("Content-Length") or 0)
                esperado = ja_tenho + restante if restante else None
                livre = espaco_livre(pasta_destino)
                if restante and livre - restante < min_livre:
                    res.status = "erro"
                    res.erro = (f"espaço em disco insuficiente: faltam {fmt_bytes(restante)}, "
                                f"livres {fmt_bytes(livre)} (reserva {fmt_bytes(min_livre)})")
                    PARAR.set()
                    break
                baixados = ja_tenho
                ultimo_log = time.monotonic()
                t0 = time.monotonic()
                with open(parcial, modo) as f:
                    for bloco in r.iter_content(chunk_size=1024 * 1024):
                        if PARAR.is_set():
                            raise InterruptedError("interrompido")
                        if bloco:
                            f.write(bloco)
                            baixados += len(bloco)
                        if time.monotonic() - ultimo_log > 30:
                            vel = (baixados - ja_tenho) / max(1e-6, time.monotonic() - t0)
                            pct = f"{100 * baixados / esperado:.0f}%" if esperado else "?"
                            log.info("  %s %s: %s (%s) a %s/s", base.chave, periodo, pct,
                                     fmt_bytes(baixados), fmt_bytes(vel))
                            ultimo_log = time.monotonic()
                if esperado and baixados != esperado:
                    raise IOError(f"download incompleto ({baixados} de {esperado} bytes)")
            tipo = _tipo_conteudo(parcial)
            if tipo == "zip_truncado":
                # mantém o .part: a próxima tentativa pede só o que falta (se já estiver completo e mesmo assim
                # inválido, o servidor responde 416 e o arquivo é descartado)
                raise IOError("ZIP incompleto ou corrompido")
            if tipo == "html":
                parcial.unlink(missing_ok=True)
                raise IOError("o servidor devolveu uma página HTML em vez do arquivo")
            if tipo is None:
                parcial.unlink(missing_ok=True)
                raise IOError("conteúdo não reconhecido (nem ZIP nem CSV)")
            parcial.replace(final)
            meta_parcial.unlink(missing_ok=True)
            res.status = "ok"
            break
        except InterruptedError:
            res.status, res.erro = "interrompido", "interrompido pelo usuário (o .part fica para retomar)"
            break
        except (requests.RequestException, IOError, OSError) as e:
            res.erro = f"{type(e).__name__}: {e}"
            if t < tentativas:
                espera = _espera(t)
                log.warning("  %s %s: falha na tentativa %d/%d (%s); nova tentativa em %.0fs",
                            base.chave, periodo, t, tentativas, res.erro, espera)
                _dormir(espera)
    res.segundos = round(time.monotonic() - inicio_total, 1)
    res.data_download = agora_iso()
    if res.status == "ok":
        res.caminho = final
        res.tipo = _tipo_conteudo(final)
        res.bytes = final.stat().st_size
        res.sha256 = sha256_arquivo(final)
        res.erro = None
    _dormir(pausa)
    return res


# --------------------------------------------------------------------------- estimativa

# Status que indicam bloqueio temporário do servidor (limite de requisições), não arquivo inexistente.
# Em 04/11/2026 o Portal passou a responder 405 em tudo depois de ~60–160 consultas seguidas.
STATUS_TEMPORARIOS = {405, 408, 425, 429, 500, 502, 503, 504}
PAUSA_ESTIMATIVA = float(os.environ.get("COLETA_PAUSA_ESTIMATIVA", "0.5"))


def tamanho_remoto(base: Base, periodo: str, tentativas: int = 6) -> dict:
    """Consulta só os cabeçalhos HTTP (sem baixar o corpo) para estimar o tamanho do arquivo.

    Repete com espera crescente quando o servidor bloqueia (405/429/5xx) ou a conexão falha; 404 é
    definitivo (arquivo não publicado).
    """
    url = base.url_download(periodo, URL_BASE)
    linha = {"base": base.chave, "periodo": periodo, "url": url, "url_final": None, "http_status": None,
             "bytes_zip": None, "ultima_modificacao": None, "tentativas": 0, "erro": None}
    for t in range(1, tentativas + 1):
        if PARAR.is_set():
            linha["erro"] = "interrompido pelo usuário"
            break
        linha["tentativas"] = t
        try:
            with sessao().get(url, stream=True, timeout=(20, 60), allow_redirects=True) as r:
                tamanho = r.headers.get("Content-Length")
                linha.update(url_final=r.url, http_status=r.status_code,
                             bytes_zip=int(tamanho) if (tamanho and r.status_code == 200) else None,
                             ultima_modificacao=r.headers.get("Last-Modified"))
                if r.status_code in STATUS_TEMPORARIOS:
                    linha["erro"] = f"HTTP {r.status_code} (servidor limitando requisições)"
                elif r.status_code != 200:
                    linha["erro"] = f"HTTP {r.status_code}"
                    break
                else:
                    linha["erro"] = None
                    _dormir(PAUSA_ESTIMATIVA)
                    break
        except requests.RequestException as e:
            linha["erro"] = f"{type(e).__name__}: {e}"
        if t < tentativas:
            espera = _espera(t)
            log.warning("  %s %s: %s; nova consulta em %.0fs", base.chave, periodo, linha["erro"], espera)
            _dormir(espera)
    return linha
