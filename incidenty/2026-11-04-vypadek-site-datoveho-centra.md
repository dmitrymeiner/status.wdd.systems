---
nazev: Krátké omezení sítě u poskytovatele datového centra
zacatek: 2026-11-04 16:03
konec: 2026-11-04 16:13
sluzby: weby, email, panel
---

V datovém centru v Norimberku, kde běží servery s weby, poštou a ovládacím panelem, selhal u našeho poskytovatele infrastruktury Hetzner hlavní směrovač cloudové sítě ([hlášení Hetzneru](https://status.hetzner.com/incident/bf117eb3-aa51-44a2-bc9a-f458901cd266), porucha 16:03–16:10). Část internetu se v té době k serverům nedostala, část ano.

- **Naměřená nedostupnost:** weby 5 min 27 s (16:06–16:12), ovládací panel 2 min 18 s, přístup ke schránkám a odesílání e-mailů asi po minutě.
- **Servery běžely bez přerušení**, nerestartovaly se. Příchozí pošta se po celou dobu doručovala.
- **DNS** běží v jiných datových centrech, výpadek se ho netýkal.
- **Do dostupnosti podle SLA se výpadek započítává.** Měsíční garance 99,5 % zůstala dodržená.

Na naší straně nebylo co opravovat, poruchu odstranil Hetzner. Po výpadku jsme v lozích serverů ověřili, že šlo jen o síť.
