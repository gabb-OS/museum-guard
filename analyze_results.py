#!/usr/bin/env python3
"""
analyze_results.py

Script di analisi offline per i log CSV prodotti da MuseumGuard. Calcola:

  1. Errore di previsione ARIMA (MAE / RMSE / bias residuo)
     -> legge:  predictive-light/prediction_errors.csv

  2. Tempo di convergenza del bias (EMA) dopo uno shock
     -> legge:  predictive-light/prediction_errors.csv (stessa fonte del punto 1)

  3. Latenza end-to-end evento -> notifica Telegram (impact/theft)
     -> legge:  mashup/latency_log.csv

  4. Precision / Recall del rilevamento theft (falsi positivi/negativi)
     -> legge:  esp-sen-mock/theft_ground_truth.csv + esp-sen-mock/detections.csv

Dipendenze: pip install pandas numpy

Uso tipico (con la struttura di ./logs creata dai volumi del docker-compose
aggiornato):

    python analyze_results.py

oppure specificando percorsi custom:

    python analyze_results.py \
        --pred-errors logs/predictive-light/prediction_errors.csv \
        --latency logs/mashup/latency_log.csv \
        --ground-truth logs/esp-sen-mock/theft_ground_truth.csv \
        --detections logs/esp-sen-mock/detections.csv

Ogni sezione e' indipendente: se un file non esiste o e' vuoto, quella
sezione viene saltata con un messaggio, cosi' puoi lanciare il report anche
avendo eseguito solo alcuni dei quattro test.
"""
import argparse
import os

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 1) Errore di previsione ARIMA (MAE / RMSE / bias residuo)
# ----------------------------------------------------------------------
def analyze_prediction_error(path: str):
    if not os.path.exists(path):
        print(f"[1] SKIP: '{path}' non trovato.")
        return None

    df = pd.read_csv(path)
    if df.empty:
        print(f"[1] SKIP: '{path}' e' vuoto (nessuna riconciliazione ancora avvenuta).")
        return None

    mae = df["abs_error"].mean()
    rmse = float(np.sqrt((df["error"] ** 2).mean()))
    bias = df["error"].mean()          # bias residuo = errore medio con segno
    std_err = df["error"].std()

    print("\n=== [1] Errore di previsione ARIMA ===")
    print(f"  campioni riconciliati : {len(df)}")
    print(f"  MAE                   : {mae:.3f}")
    print(f"  RMSE                  : {rmse:.3f}")
    print(f"  Bias residuo (media)  : {bias:+.3f}")
    print(f"  Dev. std errore       : {std_err:.3f}")

    return {"n": len(df), "mae": mae, "rmse": rmse, "bias": bias, "std": std_err}


# ----------------------------------------------------------------------
# 2) Tempo di convergenza del bias (EMA) dopo uno shock
# ----------------------------------------------------------------------
def analyze_bias_convergence(path: str, shock_k: float = 2.0, tol_frac: float = 0.15, stable_n: int = 3):
    """
    Individua gli "shock" (errori anomali) e stima quanto impiega la EMA del
    bias a ristabilizzarsi vicino al proprio valore di regime.

    shock_k   : uno shock e' un |error| >= shock_k * std(error) storico
    tol_frac  : la EMA e' considerata "convergente" quando resta entro una
                banda pari a tol_frac * |ema al momento dello shock| dal
                valore di regime stimato
    stable_n  : numero di campioni consecutivi dentro banda richiesti per
                dichiarare la convergenza
    """
    if not os.path.exists(path):
        print(f"[2] SKIP: '{path}' non trovato.")
        return None

    df = pd.read_csv(path)
    if df.empty or "ema_bias" not in df.columns:
        print(f"[2] SKIP: '{path}' vuoto o senza colonna 'ema_bias'.")
        return None

    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    std_err = df["error"].std()
    threshold = shock_k * std_err if std_err and std_err > 0 else df["error"].abs().mean()

    shocks = []
    n = len(df)
    i = 0
    while i < n:
        if abs(df.loc[i, "error"]) >= threshold:
            shock_ts = df.loc[i, "timestamp"]
            shock_ema = df.loc[i, "ema_bias"]

            # Stima del valore di regime: media degli ultimi campioni della
            # finestra successiva allo shock (prima di un eventuale nuovo shock)
            tail = df.loc[i + 1: min(i + 30, n - 1), "ema_bias"]
            steady_value = tail.tail(10).mean() if len(tail) > 0 else shock_ema
            band = max(abs(shock_ema - steady_value) * tol_frac, 1e-6)

            consecutive_stable = 0
            converged_at = None
            j = i + 1
            while j < n:
                if abs(df.loc[j, "error"]) >= threshold:
                    break  # nuovo shock prima di convergere: chiudi qui
                if abs(df.loc[j, "ema_bias"] - steady_value) <= band:
                    consecutive_stable += 1
                    if consecutive_stable >= stable_n:
                        converged_at = df.loc[j - stable_n + 1, "timestamp"]
                        break
                else:
                    consecutive_stable = 0
                j += 1

            conv_time_s = (converged_at - shock_ts).total_seconds() if converged_at is not None else None
            shocks.append({
                "shock_ts": shock_ts,
                "shock_error": df.loc[i, "error"],
                "ema_at_shock": shock_ema,
                "steady_value_est": steady_value,
                "convergence_time_s": conv_time_s,
            })
            i = j + 1
        else:
            i += 1

    print("\n=== [2] Convergenza del bias (EMA) dopo shock ===")
    print(f"  soglia shock: |error| >= {threshold:.3f}  (k={shock_k}, std={std_err:.3f})")
    if not shocks:
        print("  Nessuno shock rilevato con la soglia corrente (prova a ridurre --shock-k).")
    for s in shocks:
        conv = f"{s['convergence_time_s']:.1f}s" if s["convergence_time_s"] is not None else "non convergente entro fine serie"
        print(f"  shock @ {s['shock_ts']}  error={s['shock_error']:+.2f}  "
              f"ema_iniziale={s['ema_at_shock']:+.2f} -> regime~{s['steady_value_est']:+.2f}  "
              f"tempo di convergenza: {conv}")

    return shocks


# ----------------------------------------------------------------------
# 3) Latenza end-to-end evento -> notifica Telegram
# ----------------------------------------------------------------------
def analyze_latency(path: str):
    if not os.path.exists(path):
        print(f"[3] SKIP: '{path}' non trovato.")
        return None

    df = pd.read_csv(path)
    if df.empty:
        print(f"[3] SKIP: '{path}' e' vuoto (nessun evento impact/theft ancora notificato).")
        return None

    def stats(series: pd.Series, label: str):
        series = series.dropna()
        if series.empty:
            print(f"    {label}: nessun dato")
            return None
        s = {
            "n": len(series),
            "mean": series.mean(),
            "median": series.median(),
            "p95": series.quantile(0.95),
            "max": series.max(),
        }
        print(f"    {label:38s} n={s['n']:4d}  media={s['mean']:7.1f}ms  "
              f"mediana={s['median']:7.1f}ms  p95={s['p95']:7.1f}ms  max={s['max']:7.1f}ms")
        return s

    print("\n=== [3] Latenza end-to-end evento -> notifica ===")
    result = {}
    for evt_type in df["type"].unique():
        sub = df[df["type"] == evt_type]
        print(f"  -- tipo evento: {evt_type} --")
        result[evt_type] = {
            "sensore_to_mashup": stats(sub["latency_event_to_received_ms"], "sensore -> mashup (ricezione WoT)"),
            "mashup_to_telegram": stats(sub["latency_received_to_notified_ms"], "mashup -> Telegram (invio notifica)"),
            "totale": stats(sub["latency_event_to_notified_ms"], "TOTALE evento -> notifica"),
        }
    return result


# ----------------------------------------------------------------------
# 4) Precision / Recall rilevamento theft
# ----------------------------------------------------------------------
def analyze_theft_detection(gt_path: str, det_path: str, match_tolerance_s: float = 3.0):
    if not os.path.exists(gt_path):
        print(f"[4] SKIP: '{gt_path}' non trovato.")
        return None
    if not os.path.exists(det_path):
        print(f"[4] SKIP: '{det_path}' non trovato.")
        return None

    gt = pd.read_csv(gt_path)
    det_all = pd.read_csv(det_path)
    det = det_all[det_all["type"] == "theft"].reset_index(drop=True)

    if gt.empty:
        print("[4] SKIP: nessuna iniezione di furto simulata nel log (ground truth vuoto).")
        return None

    matched_gt = set()
    matched_det = set()

    # Ogni iniezione ground-truth e' considerata "rilevata" (TP) se esiste
    # almeno una detection theft nella finestra
    # [ts_start - tolleranza, ts_end_expected + tolleranza].
    for gi, grow in gt.iterrows():
        window_start = grow["ts_start"] - match_tolerance_s
        window_end = grow["ts_end_expected"] + match_tolerance_s
        candidates = det[(det["ts"] >= window_start) & (det["ts"] <= window_end)]
        if not candidates.empty:
            matched_gt.add(gi)
            # La prima detection nella finestra e' quella "abbinata" all'iniezione;
            # eventuali altre nella stessa finestra sono ridondanti (stesso evento fisico).
            matched_det.add(candidates.index[0])

    tp = len(matched_gt)
    fn = len(gt) - tp
    fp = len(det) - len(matched_det)   # detection non riconducibili a nessuna iniezione

    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    if not np.isnan(precision) and not np.isnan(recall) and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = float("nan")

    print("\n=== [4] Precision / Recall rilevamento THEFT ===")
    print(f"  iniezioni simulate (ground truth) : {len(gt)}")
    print(f"  detection totali (theft)          : {len(det)}")
    print(f"  finestra di tolleranza            : ±{match_tolerance_s:.1f}s")
    print(f"  True Positive  (TP)               : {tp}")
    print(f"  False Negative (FN, furto mancato): {fn}")
    print(f"  False Positive (FP, falso allarme): {fp}")
    print(f"  Precision                         : {precision:.3f}")
    print(f"  Recall                             : {recall:.3f}")
    print(f"  F1-score                           : {f1:.3f}")

    return {"tp": tp, "fn": fn, "fp": fp, "precision": precision, "recall": recall, "f1": f1}


# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Analisi risultati MuseumGuard (4 metriche)")
    parser.add_argument("--pred-errors", default="logs/predictive-light/prediction_errors.csv",
                         help="CSV metriche #1/#2 (errore ARIMA + EMA)")
    parser.add_argument("--latency", default="logs/mashup/latency_log.csv",
                         help="CSV metrica #3 (latenza evento->notifica)")
    parser.add_argument("--ground-truth", default="logs/esp-sen-mock/theft_ground_truth.csv",
                         help="CSV metrica #4 (iniezioni furto simulate)")
    parser.add_argument("--detections", default="logs/esp-sen-mock/detections.csv",
                         help="CSV metrica #4 (detection confermate)")
    parser.add_argument("--shock-k", type=float, default=2.0,
                         help="soglia shock come multiplo della deviazione standard dell'errore (default 2.0)")
    parser.add_argument("--match-tolerance", type=float, default=3.0,
                         help="finestra di tolleranza in secondi per abbinare detection <-> iniezioni (default 3.0)")
    args = parser.parse_args()

    print("MuseumGuard - Report di analisi")
    print("=" * 60)

    analyze_prediction_error(args.pred_errors)
    analyze_bias_convergence(args.pred_errors, shock_k=args.shock_k)
    analyze_latency(args.latency)
    analyze_theft_detection(args.ground_truth, args.detections, match_tolerance_s=args.match_tolerance)

    print("\nFatto.")


if __name__ == "__main__":
    main()