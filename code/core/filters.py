"""Global filter model shared by the dashboard, the agent and the API.

A :class:`FilterState` is immutable and hashable, so KPI functions can be
memoised on it. Filters are applied to admissions first; every other table
(services, medications, surgeries) is scoped through the admission ids, so a
single set of filters cascades consistently across all views.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd

from core.data import HospitalData
from core.fmt import es_date


@dataclass(frozen=True)
class FilterState:
    """Active slice of the data. Empty tuples mean "no restriction"."""

    date_from: date
    date_to: date
    services: tuple[str, ...] = field(default_factory=tuple)
    routes: tuple[str, ...] = field(default_factory=tuple)      # vía de ingreso
    classes: tuple[str, ...] = field(default_factory=tuple)     # ambulatorio / hospitalario
    regimes: tuple[str, ...] = field(default_factory=tuple)
    sexes: tuple[str, ...] = field(default_factory=tuple)
    age_groups: tuple[str, ...] = field(default_factory=tuple)
    zones: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def default(cls, data: HospitalData) -> "FilterState":
        return cls(date_from=data.min_date.date(), date_to=data.reference_date.date())

    def with_dates(self, start: date, end: date) -> "FilterState":
        return replace(self, date_from=start, date_to=end)

    def describe(self) -> str:
        """Short Spanish summary used in headers and in the agent context."""
        parts = [f"{es_date(self.date_from)} – {es_date(self.date_to)}"]
        labels = {
            "services": "Servicio", "routes": "Vía", "classes": "Clase", "regimes": "Régimen",
            "sexes": "Sexo", "age_groups": "Edad", "zones": "Zona",
        }
        for attr, label in labels.items():
            values = getattr(self, attr)
            if values:
                parts.append(f"{label}: {', '.join(values)}")
        return " · ".join(parts)

    @property
    def active_count(self) -> int:
        """Number of dimension filters in use (dates excluded)."""
        return sum(bool(getattr(self, a)) for a in
                   ("services", "routes", "classes", "regimes", "sexes", "age_groups", "zones"))


def filter_admissions(data: HospitalData, fs: FilterState, *, ignore_dates: bool = False) -> pd.DataFrame:
    """Admissions matching every active filter."""
    a = data.admissions
    mask = pd.Series(True, index=a.index)
    if not ignore_dates:
        mask &= a["fecha"].between(pd.Timestamp(fs.date_from), pd.Timestamp(fs.date_to))
    for col, values in (
        ("servicio", fs.services), ("via_ingreso", fs.routes), ("clase_ingreso", fs.classes),
        ("regimen", fs.regimes), ("sexo", fs.sexes), ("grupo_etario", fs.age_groups), ("zona", fs.zones),
    ):
        if values:
            mask &= a[col].isin(values)
    return a[mask]


def scope_lines(lines: pd.DataFrame, admissions: pd.DataFrame, fs: FilterState) -> pd.DataFrame:
    """Service/medication lines belonging to the filtered admissions and dates."""
    in_scope = lines["oid_ingreso"].isin(admissions["oid_ingreso"])
    in_dates = lines["fecha_prestacion"].between(
        pd.Timestamp(fs.date_from), pd.Timestamp(fs.date_to) + pd.Timedelta(days=1)
    )
    return lines[in_scope & in_dates]
