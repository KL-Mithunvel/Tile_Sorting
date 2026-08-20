"""Evaluates the tile-grade-classification Roboflow model(s), calling the
hosted serverless inference API from this machine (no Docker/self-hosted
server - Roboflow doesn't offer raw weight export for a model trained
through their hosted ViT training, only through their own inference server
or hosted API; see repo owner conversation, 2026-08-21).

Two modes:
- Held-out test set (default): a fixed 19-image set the model was not
  trained on - the honest generalization estimate.
- --full-dataset: every local image under data/roboflow_dataset/ (376,
  ground truth from folder name), most of which the model WAS trained
  on - this measures overall dataset fit, not generalization. Useful for
  spotting systematic errors, not for judging how well the model will do
  on new tiles - see the held-out numbers for that.

Writes per-version metrics/predictions/confusion-matrix plot, plus a
version-comparison chart, under development/output/roboflow_models/.

Usage:
    venv\\Scripts\\activate
    python development\\evaluate_grade_model.py
    python development\\evaluate_grade_model.py --full-dataset
"""

from __future__ import annotations

import argparse
import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import requests
from dotenv import load_dotenv
import os

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "development" / "output" / "roboflow_models" / "tile-grade-classification"
DATASET_DIR = REPO_ROOT / "data" / "roboflow_dataset"

PROJECT = "tile-grade-classification"
VERSIONS = [2, 3]
CLASSES = ["3A", "3B", "4", "5"]
OWNER = "Ev9BmkEz9pfms5s8nZsoRuLE3s22"

# Fixed held-out test set (Roboflow's split assignment is sticky across
# version regeneration - confirmed by the repo owner's session, 2026-08-21 -
# so the same 19 images are a valid held-out set for every version below).
TEST_SET = [
    ("8nyZgQAfU5GTglGswBqa", "3A"),
    ("7aLRfJTy7nHKysajOst0", "3B"),
    ("8VBEEmTwGD7iQGXNfeK1", "4"),
    ("9JygOH8KT0Rjxj3wgStp", "3B"),
    ("8g0NdPBlOeMI0oppEsIp", "3B"),
    ("8vyU7tUWQDjrhwjL2BLI", "3A"),
    ("8BAWfQqT0POZ13fltu54", "4"),
    ("8tEJIRQPMeMPUuHS6KPR", "3A"),
    ("8LQcYQHMIStyG48gBuA2", "5"),
    ("8ea0mUfF7gz1cp2Cvx3f", "3B"),
    ("88IL80RKAHf3QzjNowDj", "5"),
    ("9YPHzoZ6hdQWrgKKde5n", "3A"),
    ("97m0lgyFKzSiNmGKy8u3", "3A"),
    ("89JgKJzUXsWCKYt1UKXR", "4"),
    ("7REG1GcmkfEiVpXm7Q6v", "5"),
    ("7xxyHRoO35mVqAH4S92U", "3B"),
    ("8qSAFoUC7wO0I8d8l8hL", "4"),
    ("7xeKrmoSjLyGox41aStU", "5"),
    ("9XWaVW1pKRBfbz4lOpvL", "3A"),
]


def infer_by_url(api_key: str, version: int, image_id: str, retries: int = 3) -> dict:
    url = f"https://serverless.roboflow.com/{PROJECT}/{version}"
    image_url = f"https://source.roboflow.com/{OWNER}/{image_id}/original.jpg"
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, params={"api_key": api_key, "image": image_url}, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            print(f"    retry {attempt + 1}/{retries} after error: {exc}")
    raise last_exc


def infer_by_file(api_key: str, version: int, image_path: Path, retries: int = 3) -> dict:
    url = f"https://serverless.roboflow.com/{PROJECT}/{version}"
    encoded = base64.b64encode(image_path.read_bytes())
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.post(
                url,
                params={"api_key": api_key},
                data=encoded,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            print(f"    retry {attempt + 1}/{retries} after error on {image_path.name}: {exc}")
    raise last_exc


def load_full_dataset() -> list[tuple[Path, str]]:
    samples = []
    for grade_dir in sorted(DATASET_DIR.iterdir()):
        if not grade_dir.is_dir():
            continue
        for path in sorted(grade_dir.iterdir()):
            if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                samples.append((path, grade_dir.name))
    return samples


def summarize(rows: list[dict]) -> dict:
    correct = sum(r["true"] == r["pred"] for r in rows)
    matrix = np.zeros((len(CLASSES), len(CLASSES)), dtype=int)
    for r in rows:
        matrix[CLASSES.index(r["true"]), CLASSES.index(r["pred"])] += 1

    per_class = {}
    for i, cls in enumerate(CLASSES):
        tp = matrix[i, i]
        support = matrix[i, :].sum()
        predicted = matrix[:, i].sum()
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[cls] = {"precision": precision, "recall": recall, "f1": f1, "support": int(support)}

    return {
        "accuracy": correct / len(rows),
        "num_correct": correct,
        "num_total": len(rows),
        "rows": rows,
        "confusion_matrix": matrix.tolist(),
        "per_class": per_class,
    }


def evaluate_held_out(api_key: str, version: int) -> dict:
    rows = []
    for image_id, true_label in TEST_SET:
        pred = infer_by_url(api_key, version, image_id)
        top = pred["predictions"][0]["class"]
        confidence = pred["predictions"][0]["confidence"]
        rows.append({"image_id": image_id, "true": true_label, "pred": top, "confidence": confidence})
    return summarize(rows)


def evaluate_full_dataset(api_key: str, version: int, max_workers: int = 6) -> dict:
    samples = load_full_dataset()
    rows = [None] * len(samples)

    def _run(i: int, path: Path, true_label: str) -> None:
        pred = infer_by_file(api_key, version, path)
        top = pred["predictions"][0]["class"]
        confidence = pred["predictions"][0]["confidence"]
        rows[i] = {"image": str(path.relative_to(DATASET_DIR)), "true": true_label, "pred": top, "confidence": confidence}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run, i, path, label): i for i, (path, label) in enumerate(samples)}
        done = 0
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 50 == 0 or done == len(samples):
                print(f"  ...{done}/{len(samples)} processed")

    return summarize(rows)


def plot_confusion_matrix(matrix: list, version: int, accuracy: float, out_path: Path, label: str = "test split") -> None:
    matrix = np.array(matrix)
    fig, ax = plt.subplots(figsize=(5, 4.5))
    im = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(CLASSES)))
    ax.set_yticks(range(len(CLASSES)))
    ax.set_xticklabels(CLASSES)
    ax.set_yticklabels(CLASSES)
    ax.set_xlabel("Predicted grade")
    ax.set_ylabel("True grade")
    ax.set_title(f"tile-grade-classification v{version}\naccuracy={accuracy:.1%} ({label}, n={matrix.sum()})")
    for i in range(len(CLASSES)):
        for j in range(len(CLASSES)):
            color = "white" if matrix[i, j] > matrix.max() / 2 else "black"
            ax.text(j, i, str(matrix[i, j]), ha="center", va="center", color=color)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_comparison(results: list, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 4))
    versions = [r["version"] for r in results]
    accuracies = [r["accuracy"] * 100 for r in results]
    bars = ax.bar([f"v{v}" for v in versions], accuracies, color="#4C72B0")
    ax.set_ylabel("Test-split accuracy (%)")
    ax.set_ylim(0, 100)
    ax.set_title("tile-grade-classification: version comparison")
    for bar, acc in zip(bars, accuracies):
        ax.text(bar.get_x() + bar.get_width() / 2, acc + 1, f"{acc:.1f}%", ha="center")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full-dataset", action="store_true",
        help="Evaluate against all local images in data/roboflow_dataset/ instead of the 19-image held-out test set. "
             "Most of these images were used in training, so this measures dataset fit, not generalization.",
    )
    parser.add_argument("--versions", type=int, nargs="+", default=VERSIONS)
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ["ROBOFLOW_API_KEY"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    label = "full dataset" if args.full_dataset else "test split"
    prefix = "full_dataset_" if args.full_dataset else ""
    for version in args.versions:
        print(f"Evaluating version {version} ({label})...")
        if args.full_dataset:
            result = evaluate_full_dataset(api_key, version)
        else:
            result = evaluate_held_out(api_key, version)
        results.append({**result, "version": version})

        version_dir = OUTPUT_DIR / f"v{version}"
        version_dir.mkdir(parents=True, exist_ok=True)
        with open(version_dir / f"{prefix}predictions.json", "w") as f:
            json.dump(result["rows"], f, indent=2)
        with open(version_dir / f"{prefix}metrics.json", "w") as f:
            json.dump(
                {
                    "model_id": f"aida-hutc5/{PROJECT}/{version}",
                    "model_url": f"https://app.roboflow.com/aida-hutc5/{PROJECT}/{version}",
                    "eval_set": label,
                    "accuracy": result["accuracy"],
                    "num_correct": result["num_correct"],
                    "num_total": result["num_total"],
                    "per_class": result["per_class"],
                },
                f,
                indent=2,
            )
        plot_confusion_matrix(
            result["confusion_matrix"], version, result["accuracy"],
            version_dir / f"{prefix}confusion_matrix.png", label=label,
        )
        print(f"  accuracy: {result['num_correct']}/{result['num_total']} = {result['accuracy']:.1%}")

    plot_comparison(results, OUTPUT_DIR / f"{prefix}version_comparison.png")
    print(f"\nAll results written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
