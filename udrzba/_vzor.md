---
nazev: Aktualizace poštovního serveru
zacatek: 2026-11-26 21:00
konec: 2026-11-26 22:00
sluzby: email
oznameno: 2026-11-20
dopad: Odesílání a příjem pošty může být až 30 minut nedostupné. Zprávy odeslané na naše servery během údržby se doručí po jejím skončení.
---

Soubory začínající podtržítkem se nezveřejňují. Pro novou údržbu zkopírujte tento soubor
jako `udrzba/RRRR-MM-DD-kratky-nazev.md`.

- `zacatek`, `konec` – místní čas (Praha). Konec je povinný.
- `oznameno` – den, kdy odešel e-mail klientům. Aby se výpadek během údržby nepočítal do SLA,
  musí být nejméně 3 dny před začátkem (SLA 2.5 a 3.2). Jinak build vypíše varování a výpadek se započítá.
- Údržbu provádíme přednostně mezi 20:00 a 7:00 nebo o víkendu (SLA 3.2).
- `bezpecnostni: ano` – bezpečnostní údržba s krátkými restarty, která se předem neohlašuje (SLA 3.3, 3.4),
  např. kritická oprava jádra. `oznameno` se nevyplňuje. Z každého výpadku během ní se nezapočítá prvních
  15 minut, za měsíc nejvýše 60 minut, zbytek se započítá. Zapsat nejpozději po skončení (klienty informujeme dodatečně).
  Na stránce se ukáže, jen když během ní něco vypadlo. Kola `wdd-fleet` na mgmt ho zapisují sama.
- `dopad` – jedna věta, co klienti poznají.

Text pod hlavičkou je nepovinný (podrobnosti, co měníme).
