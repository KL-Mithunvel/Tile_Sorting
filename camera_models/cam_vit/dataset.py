"""Torch Dataset + augmentation pipeline for the tile-grade ViT classifier.

Augmentation mirrors the Roboflow-hosted tile-grade-classification v2
dataset version -- the better-performing config Roboflow trained
tile-grade-classification-2-vit-base-patch16-224-in21k-t1 on (84.2% held-out
accuracy vs v3's 63.2% after v3 added Adaptive Equalization contrast
preprocessing on top of the same augmentation). Pulled from the live
Roboflow project (workspace aida-hutc5) via versions_get and
trainings_describe_recipe, 2026-08-25 session -- not just the summary in
development/README.md:
  - brightness +/-10%, exposure +/-5%: Roboflow exposes these as two
    independent sliders on the same underlying operation (linear pixel-value
    scaling); approximated here as two independent torchvision ColorJitter
    brightness passes rather than one combined jitter, so each stays
    separately tunable via config.yaml and the two-knob structure is
    preserved even though the arithmetic isn't Roboflow's exact
    implementation.
  - "ninety" rotation: Roboflow's step allows 0/90/180/270 degrees only, not
    continuous-angle rotation -- RandomNinetyRotation below matches that
    exactly.
  - horizontal + vertical flip, each independent p=0.5 -- exact match.
  - deliberately NO contrast/CLAHE step -- that's what Roboflow's v3 added
    and it measurably hurt accuracy, concentrated in grade 5 recall.
"""

from __future__ import annotations

import random
from pathlib import Path

import torch
from PIL import Image
from torch.utils.data import Dataset
from torchvision import transforms
from torchvision.transforms import functional as TF

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class RandomNinetyRotation:
    """Randomly rotate by 0, 90, 180, or 270 degrees (Roboflow's "ninety" step)."""

    def __call__(self, img: Image.Image) -> Image.Image:
        degrees = random.choice([0, 90, 180, 270])
        # expand=True: for 90/270 a non-square image's width/height must
        # swap. Without it, TF.rotate keeps the original canvas and crops
        # the rotated content into it instead -- the following Resize step
        # normalizes size regardless, so expand=True is always safe here.
        return TF.rotate(img, degrees, expand=True) if degrees else img


def build_transforms(image_size: int, mean, std, aug_cfg: dict | None, train: bool) -> transforms.Compose:
    ops = []
    if train and aug_cfg:
        if aug_cfg.get("ninety_rotation", False):
            ops.append(RandomNinetyRotation())
        if aug_cfg.get("hflip_prob", 0.0):
            ops.append(transforms.RandomHorizontalFlip(p=aug_cfg["hflip_prob"]))
        if aug_cfg.get("vflip_prob", 0.0):
            ops.append(transforms.RandomVerticalFlip(p=aug_cfg["vflip_prob"]))
        brightness = aug_cfg.get("brightness_percent", 0) / 100.0
        if brightness:
            ops.append(transforms.ColorJitter(brightness=brightness))
        exposure = aug_cfg.get("exposure_percent", 0) / 100.0
        if exposure:
            ops.append(transforms.ColorJitter(brightness=exposure))
    ops.append(transforms.Resize((image_size, image_size)))
    ops.append(transforms.ToTensor())
    ops.append(transforms.Normalize(mean=mean, std=std))
    return transforms.Compose(ops)


class TileGradeDataset(Dataset):
    def __init__(self, split_dir: Path, classes: list[str], transform: transforms.Compose):
        self.transform = transform
        self.classes = classes
        self.class_to_idx = {c: i for i, c in enumerate(classes)}
        self.samples: list[tuple[Path, int]] = []
        for cls in classes:
            cls_dir = split_dir / cls
            if not cls_dir.is_dir():
                continue
            for path in sorted(cls_dir.iterdir()):
                if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    self.samples.append((path, self.class_to_idx[cls]))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> dict:
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        pixel_values = self.transform(img)
        return {"pixel_values": pixel_values, "labels": torch.tensor(label, dtype=torch.long)}
