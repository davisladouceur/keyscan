"""
Generate CNC key cutter instructions in multiple formats.

Most professional key machines (Silca Futura Pro, HPC Blitz, Ilco Orion)
accept the same fundamental data: a blank code + bitting string.
"""


def generate_cnc_instruction(blank_code: str, bitting: list[int]) -> dict:
    """
    Produce CNC instructions in all supported formats.

    Args:
        blank_code: Key blank identifier (e.g. 'KW1').
        bitting:    List of integer bitting codes (e.g. [3, 5, 2, 6, 4]).

    Returns:
        {
            "standard":       "KW1,35264",
            "verbose":        {...},
            "machine_serial": "CMD:CUT BLANK=KW1 BITTING=3-5-2-6-4",
            "display":        "KW1 — 3 5 2 6 4",
        }
    """
    bitting_str = "".join(str(b) for b in bitting)
    bitting_dashes = "-".join(str(b) for b in bitting)

    return {
        "standard": f"{blank_code},{bitting_str}",
        "verbose": {
            "blank": blank_code,
            "cuts": bitting,
            "cut_count": len(bitting),
            "direction": "standard",
            "shoulder": "A",
        },
        "machine_serial": f"CMD:CUT BLANK={blank_code} BITTING={bitting_dashes}",
        "display": f"{blank_code} — {' '.join(str(b) for b in bitting)}",
    }
