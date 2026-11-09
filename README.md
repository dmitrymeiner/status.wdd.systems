# status.wdd.systems

Veřejná stránka stavu služeb hostingu WD&D: aktuální stav, 90denní historie, měsíční dostupnost podle SLA, plánovaná údržba a incidenty. Běží na GitHub Pages, tedy mimo Hetzner, aby fungovala i při výpadku našich serverů.

## Jak to funguje

- GitHub Actions (`.github/workflows/status.yml`) načte z UptimeRobotu stav monitorů a výpadky, `build.py` postaví statický web do `dist/` a ten se nasadí na Pages. Spouští se i při každém pushi do `main` (nový incident nebo údržba je venku do dvou minut).
- Každých 5 minut workflow spouští **cron-job.org** voláním API GitHubu (`workflow_dispatch`), mimo Hetzner. Vlastní plán GitHubu je jen záloha: spouští nespolehlivě (5.–6. 11. 2026 dva běhy za 17 hodin). Token na cron-job.org smí jen spouštět Actions v tomto repozitáři.
- `data/vypadky.json` drží ukončené výpadky, aby historie nezávisela jen na UptimeRobotu (při novém monitoru nebo smazání incidentu by zmizela). Actions ho commitnou jen tehdy, když přibude nový ukončený výpadek.
- Stránka nepoužívá cookies ani nic nenačítá odjinud. Písma a vzhled jsou převzaté z `docs.wdd.systems` (`src/style.css`, změny vzhledu dělat v obou repozitářích).
- Když se data 30 minut neobnoví (plán GitHubu se zpozdí, UptimeRobot neodpovídá), stránka sama upozorní, že údaje jsou staré, a nabídne kontakt na podporu. Nepovedené načtení z UptimeRobotu nic nenasadí, zůstává poslední verze.

### Co je veřejné

Repozitář je veřejný, takže všechno v něm vidí kdokoli: názvy služeb a částí a názvy našich monitorů ze `sluzby.toml`, časy výpadků v `data/vypadky.json`, počty z kontrol záloh v `data/zalohy.json`, incidenty a údržba. **Nikdy domény klientů.** Názvy, adresy a důvody výpadků z UptimeRobotu se nikam nezapisují, ani do logu Actions (logy veřejného repozitáře jsou taky veřejné). API klíč je jen v secrets. Provozní postupy (servery, tokeny, instalace na mgmt) jsou v interní dokumentaci, ne tady.

**Repozitář musí zůstat veřejný.** V soukromém se každý běh počítá do placených minut GitHub Actions: dvě úlohy, každá se účtuje jako celá minuta, 288 běhů denně, tedy asi 580 minut denně. Tak se 5.–9. 11. 2026 spotřebovalo 90 % měsíčního limitu účtu. Historie tohoto repozitáře proto začíná 9. 11. 2026 jedním commitem se stavem z původního soukromého repozitáře.

## Výpočet dostupnosti

Podle SLA na docs.wdd.systems:

- Měsíční dostupnost = (minuty v měsíci − minuty výpadku) ÷ minuty v měsíci (SLA 2.3). Běžící měsíc se počítá do „teď“, první měsíc od data `od` ve `sluzby.toml`. Procenta se zaokrouhlují dolů (99,4999 se ukáže jako 99,49 %).
- Výpadek začíná a končí podle UptimeRobotu (SLA 2.4).
- E-mail je dole, když nefunguje kterákoli část (SMTP nebo IMAP). DNS je dole, až když neodpovídá ani jeden jmenný server; výpadek jednoho je „omezený provoz“ (SLA 2.1).
- Nezapočítává se výpadek během plánované údržby ohlášené nejméně 3 dny předem (SLA 2.5 a 3.2) a výpadek, ke kterému je incident se `sla: ne` a veřejným důvodem.
- Bezpečnostní údržba bez oznámení (`bezpecnostni: ano` v `udrzba/`, SLA 3.3): z každého výpadku během ní se nezapočítá prvních 15 min, za kalendářní měsíc nejvýš 60 min. Zbytek se započítá. Den je na stránce modrý (údržba), ne oranžový. Na stránce se taková údržba ukáže, jen když během ní něco vypadlo (kolo aktualizací bez dopadu stránku nezaplevelí). Nic jiného se automaticky nevylučuje.
- Výpadek nad 60 minut bez incidentu vypíše v Actions varování (SLA 5.6: klientům e-mail do dalšího pracovního dne).
- `python3 -m unittest discover tests` ověří výpočty (běží i v Actions před každým buildem).

## Incident

1. Zkopírovat `incidenty/_vzor.md` jako `incidenty/RRRR-MM-DD-kratky-nazev.md` (název souboru je kotva v historii, neměnit ho).
2. Vyplnit `nazev`, `zacatek`, `sluzby`, prázdný `konec`, text v množném čísle („zjišťujeme“). Commit a push: incident se ukáže nahoře na stránce.
3. Průběh doplňovat nahoru do textu, po vyřešení vyplnit `konec`.

## Plánovaná údržba

`udrzba/_vzor.md` → `udrzba/RRRR-MM-DD-kratky-nazev.md`. `oznameno` = den, kdy odešel e-mail klientům. Bez oznámení 3 dny předem build varuje a výpadek se do SLA započítá.

Bezpečnostní aktualizace s krátkými restarty (SLA 3.1 a 3.3, např. jádro nebo kritická oprava) se předem neohlašují: soubor v `udrzba/` s `bezpecnostni: ano` a bez `oznameno`, zapsat nejpozději po skončení údržby (SLA 3.4: klienty informujeme dodatečně). Konec je povinný. Kola aktualizací serverů ho zapisují sama.

## Odkud jsou data

**UptimeRobot** (plán Solo, kontrola po 60 s, měří zvenku, mimo Hetzner). Stránka používá monitory, které už v účtu běží; v `sluzby.toml` jsou zadané přesným názvem z dashboardu (log Actions u každého vypíše jeho ID, po přejmenování monitoru je lepší zapsat ID):

| Část (`sluzby.toml`) | Monitor v UptimeRobotu |
| --- | --- |
| weby/web | `wdd.cz` (HTTP, náš web na serveru wdd) |
| email/smtp | `SMTP mail.wdd.systems:465` (odesílání; příjem na portu 25 se neměří) |
| email/imap | `IMAP mail.wdd.systems` |
| dns/ns1, dns/ns2 | `NS1.wdd.systems`, `NS2.wdd.systems` |
| panel/panel | `Enhance Panel - admin.wdd.systems` |

Účet je na limitu 10 monitorů, nový tam pro stránku nepřibývá.

**mgmt** (náš správcovský server) dodává službu Zálohy: v 10:15 a 22:15 projde přes Enhance API zálohy všech webů a do `data/zalohy.json` tady připíše jen `[čas, ok, počet webů, počet problémů]`, bez domén. Výpadek služby Zálohy trvá od neúspěšné kontroly do další úspěšné. Když kontrola 30 h nepřijde, stránka ukáže „stav neznámý“, ne výpadek. Zálohy nemají 90denní pruh (`pruh = false`), jen čas a výsledek poslední kontroly: Enhance drží zálohy 7 dní, starší historie není.

Podmínky UptimeRobotu (od 26. 5. 2026) dovolují komerční použití na všech plánech. Zakazují jen přeprodávat nebo přebalovat data UptimeRobotu jako vlastní monitorovací službu, což stránka stavu vlastních služeb není.

## Nasazení (jednou)

1. Veřejný repozitář `dmitrymeiner/status.wdd.systems`, push větve `main`.
2. UptimeRobot → Integrations & API: **read-only** API klíč. GitHub → Settings → Secrets and variables → Actions → secret `UPTIMEROBOT_API_KEY`. Kdyby API v3 nefungovalo, proměnná (Variables) `UPTIMEROBOT_API` = `v2`.
3. Settings → Pages → Source: **GitHub Actions**.
4. DNS v Enhance (zóna wdd.systems): `status CNAME dmitrymeiner.github.io.`, TTL 86400.
5. GitHub → Settings (účtu) → Pages → Verified domains → `wdd.systems` (TXT `_github-pages-challenge-dmitrymeiner`). Chrání doménu před převzetím cizím repozitářem.
6. Settings → Pages → Custom domain `status.wdd.systems`, po vydání certifikátu Enforce HTTPS.
7. Actions → Stav služeb → Run workflow. V logu kroku „Načíst UptimeRobot“ je u každého monitoru jeho ID, stav a počet výpadků.
8. Zápis záloh z mgmt: fine-grained token jen na tento repozitář (Contents: Read and write), instalace podle interní dokumentace.
9. Ruleset `main` jako u docs: zákaz smazání a force-push. Povinné pull requesty ne (Actions do `main` commitují data).
10. Spouštění: fine-grained token jen na tento repozitář s **Actions: Read and write** → cron-job.org, úloha každých 5 minut: `POST https://api.github.com/repos/dmitrymeiner/status.wdd.systems/actions/workflows/status.yml/dispatches`, hlavičky `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, tělo `{"ref":"main"}`. Úspěch = HTTP 204.

## Lokálně

```
pip install markdown
python3 build.py --sample          # ukázková data, prohlédnout dist/index.html
python3 build.py --sample --sample-down email/imap,dns/ns2
UPTIMEROBOT_API_KEY=… python3 build.py --fetch   # ostrá data (zapíše data/vypadky.json)
python3 -m unittest discover tests
```
