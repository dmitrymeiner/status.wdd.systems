---
nazev: Bezpečnostní aktualizace serverů
zacatek: 2026-11-06 16:37
konec: 2026-11-06 21:50
sluzby: weby, email, dns, panel
bezpecnostni: ano
dopad: Při restartu serveru byly weby a ovládací panel asi minutu nedostupné. Výpadek e-mailu ani DNS monitoring nezaznamenal.
---

Nainstalovali jsme kritickou bezpečnostní opravu jádra operačního systému a novou verzi ovládacího panelu. Oprava řešila vážnou zranitelnost, proto jsme ji nasadili hned a nečekali na noční hodiny. Kvůli jádru bylo potřeba servery postupně restartovat.

- **16:41** restart serveru s weby a ovládacím panelem, nedostupnost asi 1 minutu
- **16:44–16:51** restart poštovního serveru a obou DNS serverů po jednom, bez zaznamenaného výpadku
- **21:36–21:47** restart ostatních serverů, služeb pro klienty se netýkal
