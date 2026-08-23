"""Router strategi adaptif (bagian 17).

Pertanyaan yang dijawab paket ini bukan "strategi apa yang paling bagus"
melainkan "strategi apa yang paling cocok untuk kondisi pasar SEKARANG, dan
apa buktinya" (bagian 17.57).

**Router tidak mengeksekusi apa pun** (bagian 17.1). Ia mengamati, menilai,
memilih, dan menolak memilih. Seluruh eksekusi tetap di tangan operator, dan
itu dijaga penjaga AST di `tests/test_router_analis_saja.py` - bukan oleh
janji di docstring ini.
"""

from __future__ import annotations

__all__: list[str] = []
