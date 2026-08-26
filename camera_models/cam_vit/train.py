"""Fine-tunes google/vit-base-patch16-224-in21k on the tile-grade dataset
built by prepare_dataset.py -- a local, weight-exportable counterpart to the
Roboflow-hosted tile-grade-classification-2-vit-base-patch16-224-in21k-t1
model (same base architecture, but Roboflow never exports weights for
hosted-ViT-trained models -- see development/README.md and cam_vit/README.md
for the augmentation-parity details this pipeline was built to match).

Usage:
    venv\\Scripts\\activate
    python camera_models\\cam_vit\\prepare_dataset.py   # once, or whenever the split should change
    python camera_models\\cam_vit\\train.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import yaml
from transformers import (
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
    ViTForImageClassification,
    ViTImageProcessor,
)

from dataset import TileGradeDataset, build_transforms

CAM_VIT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CAM_VIT_DIR / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def compute_metrics(eval_pred) -> dict:
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=1)
    return {"accuracy": float((preds == labels).mean())}


def main() -> None:
    config = load_config()
    dataset_dir = (CAM_VIT_DIR / config["dataset_dir"]).resolve()
    if not dataset_dir.is_dir():
        raise SystemExit(f"{dataset_dir} does not exist yet — run cam_vit/prepare_dataset.py first")

    classes = config["classes"]
    model_cfg = config["model"]
    train_cfg = config["train"]
    aug_cfg = config.get("augmentation", {})

    processor = ViTImageProcessor.from_pretrained(model_cfg["checkpoint"])
    image_size = model_cfg.get("image_size") or processor.size.get("height", 224)
    mean, std = processor.image_mean, processor.image_std

    train_transform = build_transforms(image_size, mean, std, aug_cfg, train=True)
    val_transform = build_transforms(image_size, mean, std, aug_cfg=None, train=False)

    train_dataset = TileGradeDataset(dataset_dir / "train", classes, train_transform)
    val_dataset = TileGradeDataset(dataset_dir / "val", classes, val_transform)
    print(f"train={len(train_dataset)} val={len(val_dataset)} classes={classes}")

    model = ViTForImageClassification.from_pretrained(
        model_cfg["checkpoint"],
        num_labels=len(classes),
        id2label={i: c for i, c in enumerate(classes)},
        label2id={c: i for i, c in enumerate(classes)},
    )

    output_dir = CAM_VIT_DIR / train_cfg["output_dir"]
    args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=train_cfg["epochs"],
        per_device_train_batch_size=train_cfg["batch_size"],
        per_device_eval_batch_size=train_cfg["batch_size"],
        learning_rate=float(train_cfg["learning_rate"]),
        weight_decay=train_cfg.get("weight_decay", 0.0),
        warmup_ratio=train_cfg.get("warmup_ratio", 0.0),
        logging_steps=train_cfg.get("logging_steps", 10),
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=train_cfg.get("save_total_limit", 2),
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        greater_is_better=True,
        fp16=torch.cuda.is_available(),
        report_to=[],
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=train_cfg.get("patience", 8))],
    )

    trainer.train()
    metrics = trainer.evaluate()
    print(f"\nfinal val accuracy: {metrics['eval_accuracy']:.4f}")

    best_dir = output_dir / "best"
    trainer.save_model(str(best_dir))
    processor.save_pretrained(str(best_dir))
    print(f"Saved best model + processor to {best_dir}")


if __name__ == "__main__":
    main()
