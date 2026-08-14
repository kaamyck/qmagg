# Qullamaggie Setup Screener — S&P 500 + NASDAQ

Codzienny skan calego uniwersum (S&P 500 + NASDAQ, cena >$5, obrot >$10M/dzien)
pod setupy Qullamaggie: momentum burst -> ciasna konsolidacja -> breakout.

## Instalacja (5 minut, raz)

1. Utworz **publiczne** repo na GitHub (np. `qmag-screener`) i wgraj te pliki
   (zachowaj strukture katalogow, w tym `.github/workflows/scan.yml`).
2. W repo: **Settings -> Actions -> General -> Workflow permissions**
   -> zaznacz **Read and write permissions** -> Save.
3. Zakladka **Actions** -> "Daily Qullamaggie Scan" -> **Run workflow**
   (pierwszy raz recznie; potem odpala sie sam pn-pt o 22:30 UTC).
4. Po ~10-15 min w `docs/results.json` pojawia sie wynik. Adres do wklejenia
   w dashboardzie:

   https://raw.githubusercontent.com/TWOJ_LOGIN/qmag-screener/main/docs/results.json

## Kryteria setupu

- ruch >=30% w oknie 30/60/90 sesji (momentum burst)
- ADR20 >= 3.5% (score premiuje wyzsze)
- cena nad EMA10 / EMA20 / SMA50, EMA10 > EMA20, obie rosnace
- baza 3-15 dni: zaciesniajacy sie zakres (ADR5/ADR20), wyzsze dolki
- wysychajacy wolumen (vol5/vol20)
- cena max 8% pod pivotem (max high z 25 sesji)

Statusy: **SETUP** (gotowy do obserwacji pod breakout), **BREAKOUT**
(przebicie pivota dzisiaj), **BUDUJE** (konsoliduje, jeszcze nie ciasno).

Score 0-100: momentum 25 + ADR 15 + trend 20 + zaciesnienie 20 + wolumen 10 + pivot 10.

Narzedzie edukacyjne — nie stanowi rekomendacji inwestycyjnej.
