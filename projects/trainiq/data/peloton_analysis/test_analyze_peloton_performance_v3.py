import subprocess, sys

result = subprocess.run(
    [sys.executable, "analyze_peloton_performance_v3.py"],
    capture_output=True, text=True, cwd="."
)
output = result.stdout

failures = []

# Test A — HR anomala: presente, marcata, esclusa da W/HR, non altera media/mediana
if "raw_hr=4.15" not in output:
    failures.append("Test A: HR=4.15 non trovato in output (dovrebbe rimanere nel dataset)")
if "status=SUSPICIOUS_LOW" not in output:
    failures.append("Test A: HR=4.15 non classificato SUSPICIOUS_LOW")
# Verifica diretta: nella lista 'EXCLUDED FROM W/HR' deve comparire
if "EXCLUDED FROM W/HR: 2" not in output and "raw_hr=4.15 | status=SUSPICIOUS_LOW" not in output:
    failures.append("Test A: HR=4.15 non risulta escluso da W/HR")

# Test B — HR missing rimane valido per power analysis (deve comparire nel power-duration profile
# con lo stesso n totale di v2, cioè i record senza HR non vengono rimossi dal dataset di potenza)
if "HR missing:               15" not in output:
    failures.append("Test B: conteggio HR missing atteso (15) non trovato")
if "60 min | n=56 |" not in output:
    failures.append("Test B: power-duration profile 60min non ha n=56 (i record senza HR devono restare)")

# Test C — zero workout identificato, non cancellato, confrontato con vicini
if "POSSIBLE RETRY / DUPLICATE-ATTEMPT" not in output:
    failures.append("Test C: etichetta retry/duplicate-attempt assente")
if "2025-09-17 18:42 | 0 min | Lanebreak Ride" not in output:
    failures.append("Test C: record zero (Lanebreak) non presente/non conservato")

# Test D — baseline small N marcata
if "15 min | n= 1 | baseline avg=  96.0 W | median=  96.0 W | best=  96.0 W  [SMALL N]" not in output:
    failures.append("Test D: baseline PZ n=1 (15 min) non marcata [SMALL N]")

# Test E — nessun traceback, tutte le 12 sezioni presenti
if "Traceback" in output or result.returncode != 0:
    failures.append(f"Test E: traceback o exit code non zero (returncode={result.returncode})")
required_sections = [
    "FTP TIMELINE", "POWER-DURATION PROFILE", "POWER ZONE BASELINE",
    "NORMALIZED POWER ZONE PERFORMANCE", "MONTHLY NORMALIZED POWER ZONE TREND",
    "RECENT VS HISTORICAL BASELINE", "HEART RATE DATA QUALITY", "POWER / HEART RATE",
    "ZERO / FAILED REVIEW", "DATA QUALITY CLASSIFICATION", "TRAINIQ SIGNAL SUMMARY",
]
for s in required_sections:
    if s not in output:
        failures.append(f"Test E: sezione mancante: {s}")

# Verifica invarianza FTP e PZ baseline rispetto a v2/riferimento
if "171 W" not in output or "192 W" not in output or "+21 W" not in output or "+12.3%" not in output:
    failures.append("REGRESSION: FTP timeline/change diverso dal riferimento")
if "128.0 W" not in output:  # 60min PZ baseline invariata
    failures.append("REGRESSION: PZ baseline 60min diversa dal riferimento (atteso 128.0 W)")

print("=" * 60)
if failures:
    print(f"RISULTATO: {len(failures)} test falliti\n")
    for f in failures:
        print(" -", f)
    sys.exit(1)
else:
    print("RISULTATO: tutti i test A-E + verifiche di regressione PASSATI")
