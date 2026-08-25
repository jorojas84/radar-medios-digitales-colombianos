"""Comprueba si direcciones IP pertenecen a rangos públicos de Power BI."""

import argparse
import ipaddress
import json
from pathlib import Path


IPS_PREDETERMINADAS = ("20.97.34.140", "40.119.11.75")


def buscar_coincidencias(ruta: Path, ips: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Devuelve los rangos Power BI que contienen cada dirección solicitada."""
    with ruta.open(encoding="utf-8") as archivo:
        datos = json.load(archivo)

    resultados = {ip: [] for ip in ips}
    direcciones = {ip: ipaddress.ip_address(ip) for ip in ips}

    for elemento in datos["values"]:
        if not elemento["name"].startswith("PowerBI"):
            continue
        for prefijo in elemento["properties"]["addressPrefixes"]:
            red = ipaddress.ip_network(prefijo)
            for texto, direccion in direcciones.items():
                if direccion in red:
                    resultados[texto].append((elemento["name"], prefijo))

    return resultados


def main() -> int:
    """Lee argumentos, ejecuta la búsqueda e imprime un resumen."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archivo",
        type=Path,
        help="Ruta a ServiceTags_Public.json",
    )
    parser.add_argument("ips", nargs="*", default=list(IPS_PREDETERMINADAS))
    argumentos = parser.parse_args()

    for ip, coincidencias in buscar_coincidencias(
        argumentos.archivo,
        argumentos.ips,
    ).items():
        print(f"\n{ip}:")
        if not coincidencias:
            print("  NO MATCH")
            continue
        for etiqueta, prefijo in coincidencias:
            print(f"  {etiqueta} -> {prefijo}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
