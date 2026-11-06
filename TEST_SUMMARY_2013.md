# TEST_SUMMARY 2.0.13

Self-test (ohne Fusion): `python3 -m unittest test_lazynest_2013.py` im Add-In-Ordner.

- CIRCULAR_DEPENDENCY-Erkennung (Fusion-Meldung inkl. „Zirkuläre Abhängigkeit“)
- `copy_needed`: lokal 1-Körper nein; verknüpft/Wurzel/ohne Comp/Mehrkörper ja
- `convert_blocks_part`: Wurzel blockt ohne Convert; S2-Mehrkörper nicht
- `copy_fallback_action`: copy / fallback / skip / Dedup gleicher Comp
- Policy `_part_usable`: S2 usable ohne Convert; Wurzelkörper erst mit Convert
- i18n DE/EN/ES/FR für Hinweis, Mehrkörper-Warnung, Copy-Fallback
