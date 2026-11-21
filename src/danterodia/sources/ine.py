"""Frecuencia de nombres y apellidos en España (datos del INE, sin conexión).

Sirve para saber **lo común que es un nombre**: si hay miles de «María García López», una
coincidencia por nombre en cualquier otra fuente vale muy poco. La correlación usa la
estimación ``personas_estimadas`` para ajustar la confianza.

Datos: ``data/ine`` (copia de ``data/spanish-names-master``), procedentes de
«Nombres y apellidos más frecuentes» del INE. Son datos agregados, no personales.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from danterodia.models import Entity, EntityType, Finding
from danterodia.sources.base import Source, SourceContext

#: Población residente aproximada usada como denominador de la estimación.
POBLACION = 48_600_000


def _sin_acentos(texto: str) -> str:
    """Quita tildes y diéresis pero **conserva la Ñ** (MUÑOZ ≠ MUNOZ en los datos del INE)."""
    out = []
    for ch in unicodedata.normalize("NFD", texto):
        if unicodedata.combining(ch) and ch != "̃":  # ̃ = tilde de la ñ
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def clave(texto: str) -> str:
    return " ".join(_sin_acentos(texto).upper().split())


@dataclass(frozen=True, slots=True)
class FrecuenciaApellido:
    primero: int
    segundo: int


@dataclass(frozen=True, slots=True)
class TablasINE:
    hombres: dict[str, int]
    mujeres: dict[str, int]
    apellidos: dict[str, FrecuenciaApellido]


def _leer(nombre: str) -> list[list[str]]:
    texto = files("danterodia.data").joinpath("ine", nombre).read_text(encoding="utf-8")
    filas = list(csv.reader(io.StringIO(texto)))
    return filas[1:]  # sin cabecera


@lru_cache(maxsize=1)
def tablas() -> TablasINE:
    hombres = {clave(f[0]): int(f[1]) for f in _leer("hombres.csv")}
    mujeres = {clave(f[0]): int(f[1]) for f in _leer("mujeres.csv")}
    apellidos: dict[str, FrecuenciaApellido] = {}
    for fichero in ("apellidos.csv", "apellidos-20.csv"):
        for f in _leer(fichero):
            apellidos.setdefault(clave(f[0]), FrecuenciaApellido(int(f[1]), int(f[2])))
    return TablasINE(hombres, mujeres, apellidos)


PARTICULAS = frozenset({"de", "del", "la", "las", "los", "y", "i", "san", "santa", "da", "dos"})


def _agrupar(partes: list[str]) -> list[str]:
    """Une las partículas con la palabra siguiente: «de la Fuente» es un único apellido."""
    grupos: list[str] = []
    pendiente: list[str] = []
    for p in partes:
        if p.lower() in PARTICULAS:
            pendiente.append(p)
            continue
        grupos.append(" ".join([*pendiente, p]))
        pendiente = []
    if pendiente:
        grupos.append(" ".join(pendiente))
    return grupos


def separar_nombre(entity: Entity) -> tuple[str, str | None, str | None]:
    """Devuelve ``(nombre, apellido1, apellido2)``.

    Usa los atributos ``nombre``/``apellido1``/``apellido2`` si existen; si no, supone el
    formato español habitual: los dos últimos grupos son los apellidos («Miguel de Cervantes
    Saavedra» → «Miguel», «de Cervantes», «Saavedra»).
    """
    attrs = entity.attributes
    if attrs.get("nombre"):
        return str(attrs["nombre"]), attrs.get("apellido1"), attrs.get("apellido2")
    partes = _agrupar(entity.value.split())
    if len(partes) >= 3:
        return " ".join(partes[:-2]), partes[-2], partes[-1]
    if len(partes) == 2:
        return partes[0], partes[1], None
    return entity.value, None, None


def _apellido(t: TablasINE, apellido: str) -> FrecuenciaApellido | None:
    """Busca el apellido tal cual y, si no está, sin partículas (DE CERVANTES → CERVANTES)."""
    k = clave(apellido)
    if k in t.apellidos:
        return t.apellidos[k]
    sin = " ".join(w for w in k.split() if w.lower() not in PARTICULAS)
    return t.apellidos.get(sin)


def _buscar(tabla: dict[str, int], nombre: str) -> int:
    """Busca el nombre compuesto entero y, si no está, el primer nombre."""
    k = clave(nombre)
    if k in tabla:
        return tabla[k]
    primero = k.split(" ", 1)[0]
    return tabla.get(primero, 0)


def estimar(nombre: str, ap1: str | None, ap2: str | None) -> dict[str, object]:
    t = tablas()
    f_h, f_m = _buscar(t.hombres, nombre), _buscar(t.mujeres, nombre)
    a1 = _apellido(t, ap1) if ap1 else None
    a2 = _apellido(t, ap2) if ap2 else None
    resultado: dict[str, object] = {
        "nombre": nombre,
        "frecuencia_nombre_hombres": f_h,
        "frecuencia_nombre_mujeres": f_m,
        "frecuencia_apellido1": a1.primero if a1 else (0 if ap1 else None),
        "frecuencia_apellido2": a2.segundo if a2 else (0 if ap2 else None),
    }
    # Estimación suponiendo independencia: personas con ese nombre × probabilidad de cada apellido.
    # Si un dato no aparece en el INE (frecuencia < 20), se usa 20 como cota superior.
    estimado = float(max(f_h + f_m, 20))
    if ap1:
        estimado *= max(a1.primero if a1 else 0, 20) / POBLACION
    if ap2:
        estimado *= max(a2.segundo if a2 else 0, 20) / POBLACION
    resultado["personas_estimadas"] = round(estimado, 2)
    return resultado


class IneFrecuencia(Source):
    name = "ine"
    description = "Frecuencia de nombre y apellidos en España (INE, sin conexión)"
    input_types = frozenset({EntityType.PERSONA})
    offline = True

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        nombre, ap1, ap2 = separar_nombre(target)
        datos = estimar(nombre, ap1, ap2)
        n = float(datos["personas_estimadas"])  # type: ignore[arg-type]
        if n >= 100:
            texto = f"Nombre muy común: unas {n:,.0f} personas en España".replace(",", ".")
        elif n >= 2:
            texto = f"Nombre poco común: unas {n:.0f} personas en España"
        else:
            texto = "Nombre muy poco común: probablemente menos de 2 personas en España"
        return [
            self.finding(
                target,
                target,
                relation="frecuencia_nombre",
                title=texto,
                description="Estimación con frecuencias del INE suponiendo independencia "
                "entre nombre y apellidos; es orientativa.",
                confidence=0.9,
                source_url="https://www.ine.es/tnombres/formGeneral.do",
                data=datos,
            )
        ]
