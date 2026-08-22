"""ARUNA AI - Market Research & Paper Trading Intelligence System.

Two markets only: CRYPTO and IDX (Indonesian equities).  Forex is not part of
this system and must not be reintroduced.

Current build stage: PHASE 10 (shadow model, drift, experiments, human
approval) - the final phase of the specification.  Every phase of the specification is now built.

``__phase__`` is kept free of imports so this module stays dependency-free; a
test asserts it matches ``aruna.core.config.CURRENT_PHASE``, because two phase
numbers that can drift apart are worse than one.
"""

__version__ = "1.0.0"
__phase__ = 10

__all__ = ["__phase__", "__version__"]
