"""PortAgent - iterative PC-game -> Android porter.

The package implements the ANALYZE -> CHOOSE -> EXECUTE -> VERIFY -> LEARN
loop described in docs/ARCHITECTURE.md.  Every stage is a real component:
binary analysis is done by pure-python PE/ELF parsers, the builder emits a
genuine Gradle/NDK project, and the verifier runs a deterministic equivalence
harness against an oracle.
"""

__version__ = "0.1.0"

from .models import (
    ApiKind,
    Engine,
    GameProfile,
    StepName,
    StepReport,
    Strategy,
    Verdict,
)

__all__ = [
    "ApiKind",
    "Engine",
    "GameProfile",
    "StepName",
    "StepReport",
    "Strategy",
    "Verdict",
    "__version__",
]
