"""Funções auxiliares compartilhadas pelos scripts de coleta."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VERSAO_SCRIPT = "1.0.0"


@dataclass(frozen=True)
class Pastas:
    """Caminhos do projeto. Tudo é relativo à raiz do repositório."""

    raiz: Path

    @property
    def bronze(self) -> Path:
        return self.raiz / "data" / "bronze"

    @property
    def silver(self) -> Path:
        return self.raiz / "data" / "silver"

    @property
    def gold(self) -> Path:
        return self.raiz / "data" / "gold"

    @property
    def metadados(self) -> Path:
        return self.raiz / "data" / "metadados"

    @property
    def tmp(self) -> Path:
        return self.raiz / "data" / "_tmp"

    @property
    def downloads(self) -> Path:
        return self.tmp / "downloads"

    @property
    def zips(self) -> Path:
        """ZIPs guardados com --manter-zip (fora do Git)."""
        return self.raiz / "data" / "_zips"

    @property
    def docs(self) -> Path:
        return self.raiz / "docs"

    @property
    def reports(self) -> Path:
        return self.raiz / "reports"

    def criar(self) -> None:
        for p in (self.bronze, self.silver, self.gold, self.metadados, self.tmp, self.downloads):
            p.mkdir(parents=True, exist_ok=True)


def raiz_padrao() -> Path:
    return Path(__file__).resolve().parent.parent


def memoria_total() -> int | None:
    """Memória física da máquina em bytes (Windows, Linux e macOS), sem depender de pacotes extras."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = _Status()
            st.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:  # noqa: BLE001
        return None


def memoria_padrao() -> str | None:
    """Metade da RAM para o DuckDB (o padrão dele é 80%, apertado num notebook com navegador aberto)."""
    total = memoria_total()
    if not total:
        return None
    return f"{max(1, int(total * 0.5 / 1024**3))}GB"


def configurar_console() -> None:
    """Evita erro de acentuação no console do Windows."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def configurar_log(pastas: Pastas, nome: str, nivel: str = "INFO") -> logging.Logger:
    pasta_log = pastas.metadados / "logs"
    pasta_log.mkdir(parents=True, exist_ok=True)
    arquivo = pasta_log / f"{nome}_{datetime.now():%Y%m%d_%H%M%S}.log"
    log = logging.getLogger("coleta")
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, nivel.upper(), logging.INFO))
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    arq = logging.FileHandler(arquivo, encoding="utf-8")
    arq.setLevel(logging.DEBUG)
    arq.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s"))
    log.addHandler(console)
    log.addHandler(arq)
    log.debug("log em %s", arquivo)
    return log


def padronizar_nome(texto: str) -> str:
    """'Ano/Mês Referência' -> 'ano_mes_referencia' (snake_case, sem acentos)."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", texto)  # CamelCase -> Camel_Case
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return texto.strip("_")


def agora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_arquivo(caminho: Path, bloco: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        while True:
            dados = f.read(bloco)
            if not dados:
                break
            h.update(dados)
    return h.hexdigest()


def espaco_livre(caminho: Path) -> int:
    caminho = caminho if caminho.exists() else caminho.parent
    while not caminho.exists():
        caminho = caminho.parent
    return shutil.disk_usage(caminho).free


def fmt_bytes(n: float | int | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unidade in ("B", "KB", "MB", "GB", "TB"):  # unidades decimais (1 GB = 1.000.000.000 bytes)
        if abs(n) < 1000 or unidade == "TB":
            return f"{n:,.1f} {unidade}".replace(",", "X").replace(".", ",").replace("X", ".")
        n /= 1000
    return f"{n:.1f} TB"


def caminho_sql(caminho: Path | str) -> str:
    """Caminho como literal SQL do DuckDB (barras normais, aspas escapadas)."""
    return "'" + str(caminho).replace("\\", "/").replace("'", "''") + "'"


def ident_sql(nome: str) -> str:
    return '"' + nome.replace('"', '""') + '"'


def literal_sql(valor: str) -> str:
    return "'" + valor.replace("'", "''") + "'"


def remover_arvore(caminho: Path) -> None:
    if caminho.exists():
        shutil.rmtree(caminho, ignore_errors=True)


class Manifesto:
    """Registro da coleta em JSON Lines (um evento por linha; o último por chave vale).

    Fica em data/metadados/manifesto_coleta.jsonl e é a fonte de verdade para:
    - pular arquivos já convertidos (retomada);
    - gerar as tabelas de metadados pedidas no trabalho.
    """

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self.caminho.parent.mkdir(parents=True, exist_ok=True)

    def registros(self) -> list[dict]:
        if not self.caminho.exists():
            return []
        saida = []
        with open(self.caminho, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if linha:
                    try:
                        saida.append(json.loads(linha))
                    except json.JSONDecodeError:
                        continue  # linha truncada por interrupção: ignora
        return saida

    def ultimos(self) -> dict[tuple[str, str], dict]:
        ult: dict[tuple[str, str], dict] = {}
        for r in self.registros():
            ult[(r.get("base"), r.get("periodo"))] = r
        return ult

    def adicionar(self, registro: dict) -> None:
        with open(self.caminho, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
