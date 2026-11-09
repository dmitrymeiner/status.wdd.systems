---
nazev: Pomalé načítání webů
zacatek: 2026-11-20 14:05
konec:
sluzby: weby
stav: Zjišťujeme příčinu
---

Soubory začínající podtržítkem se nezveřejňují. Pro nový incident zkopírujte tento soubor
jako `incidenty/RRRR-MM-DD-kratky-nazev.md` (název souboru = kotva v historii, neměnit).

Hlavička:

- `nazev` – krátce a srozumitelně pro klienty, bez domén klientů.
- `zacatek`, `konec` – místní čas (Praha) ve tvaru `2026-11-20 14:05`. Prázdný `konec` = incident trvá,
  ukáže se nahoře na stránce. Po vyřešení doplnit `konec`.
- `sluzby` – klíče ze `sluzby.toml` oddělené čárkou: weby, email, dns, zalohy, panel.
- `stav` – nepovinné. Bez něj „Řešíme“, s vyplněným koncem „Vyřešeno“.
- `sla: ne` + `duvod_sla: …` – jen když se výpadek podle SLA 2.5 nezapočítává (např. krátký restart
  při bezpečnostní údržbě podle čl. 3.3). Důvod se zveřejní.

Text pište v množném čísle („zjišťujeme“, „opravili jsme“). Průběh přidávejte nahoru:

**14:40** Příčinu jsme našli a opravujeme ji.

**14:10** Zjišťujeme, proč se weby načítají pomalu.
