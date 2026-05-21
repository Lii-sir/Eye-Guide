from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path


LOGGER = logging.getLogger("eyeguide.train_blind_road_seg")

DEFAULT_CONFIG = Path("training/paddleseg/pp_mobileseg_tiny_blind_road_512x512.yml")
DEFAULT_DATASET = Path("datasets/blind_road_paddleseg")
DEFAULT_OUTPUT = Path("training/paddleseg/output/blind_road_pp_mobileseg_tiny")
DEFAULT_PADDLESEG = Path(".tmp/PaddleSeg")
PADDLESEG_REPO = "https://github.com/PaddlePaddle/PaddleSeg"
PADDLESEG_BRANCH = "release/2.10"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch PP-MobileSeg-Tiny training for the blind road segmentation dataset."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Training config path.",
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=DEFAULT_DATASET,
        help="Converted PaddleSeg dataset root.",
    )
    parser.add_argument(
        "--paddleseg-root",
        type=Path,
        default=None,
        help="Existing PaddleSeg checkout. Defaults to PADDLESEG_ROOT or .tmp/PaddleSeg.",
    )
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory used for checkpoints and logs.",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "gpu", "xpu", "npu", "mlu"],
        default="gpu",
        help="Device passed to PaddleSeg tools/train.py.",
    )
    parser.add_argument("--iters", type=int, help="Override training iterations.")
    parser.add_argument("--batch-size", type=int, help="Override batch size.")
    parser.add_argument("--learning-rate", type=float, help="Override learning rate.")
    parser.add_argument(
        "--save-interval",
        type=int,
        default=500,
        help="Checkpoint save interval.",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=0,
        help="Data loader worker count.",
    )
    parser.add_argument(
        "--log-iters",
        type=int,
        default=20,
        help="Logging interval.",
    )
    parser.add_argument(
        "--keep-checkpoint-max",
        type=int,
        default=5,
        help="Maximum number of checkpoints to keep.",
    )
    parser.add_argument(
        "--resume-model",
        type=Path,
        help="Resume training from an existing checkpoint directory.",
    )
    parser.add_argument(
        "--precision",
        choices=["fp32", "fp16"],
        default="fp32",
        help="Precision passed to PaddleSeg.",
    )
    parser.add_argument(
        "--use-vdl",
        action="store_true",
        help="Enable VisualDL logging.",
    )
    parser.add_argument(
        "--do-eval",
        action="store_true",
        default=True,
        help="Evaluate on save intervals and keep best_model.",
    )
    parser.add_argument(
        "--no-eval",
        dest="do_eval",
        action="store_false",
        help="Disable periodic evaluation.",
    )
    parser.add_argument(
        "--bootstrap-paddleseg",
        action="store_true",
        help="Clone PaddleSeg release/2.10 when local checkout is missing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved training command without starting training.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level.",
    )
    return parser


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def resolve_paddleseg_root(args: argparse.Namespace) -> Path:
    if args.paddleseg_root:
        return args.paddleseg_root.resolve()
    env_root = os.environ.get("PADDLESEG_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return DEFAULT_PADDLESEG.resolve()


def maybe_bootstrap_paddleseg(root: Path, bootstrap: bool) -> None:
    if root.exists():
        return
    if not bootstrap:
        raise FileNotFoundError(
            f"PaddleSeg checkout not found at {root}. "
            "Pass --bootstrap-paddleseg or set --paddleseg-root."
        )
    root.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        PADDLESEG_BRANCH,
        PADDLESEG_REPO,
        str(root),
    ]
    LOGGER.info("Cloning PaddleSeg into %s", root)
    subprocess.run(command, check=True)


def ensure_ready(
    config: Path,
    dataset_root: Path,
    paddleseg_root: Path,
    *,
    require_paddle: bool,
) -> None:
    if not config.exists():
        raise FileNotFoundError(f"Training config not found: {config}")
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root not found: {dataset_root}")
    if not (dataset_root / "train.txt").exists():
        raise FileNotFoundError(f"Missing train.txt under {dataset_root}")
    if not (dataset_root / "val.txt").exists():
        raise FileNotFoundError(f"Missing val.txt under {dataset_root}")
    train_py = paddleseg_root / "tools" / "train.py"
    if not train_py.exists():
        raise FileNotFoundError(f"PaddleSeg train.py not found under {paddleseg_root}")
    if require_paddle and not module_exists("paddle"):
        raise RuntimeError(
            "The 'paddle' package is not installed in the current environment. "
            "Install PaddlePaddle first, then rerun this launcher."
        )


def build_command(args: argparse.Namespace, paddleseg_root: Path) -> list[str]:
    train_py = paddleseg_root / "tools" / "train.py"
    command = [
        sys.executable,
        str(train_py),
        "--config",
        str(args.config.resolve()),
        "--device",
        args.device,
        "--save_dir",
        str(args.save_dir.resolve()),
        "--save_interval",
        str(args.save_interval),
        "--num_workers",
        str(args.num_workers),
        "--log_iters",
        str(args.log_iters),
        "--keep_checkpoint_max",
        str(args.keep_checkpoint_max),
        "--precision",
        args.precision,
    ]
    if args.do_eval:
        command.append("--do_eval")
    if args.use_vdl:
        command.append("--use_vdl")
    if args.resume_model:
        command.extend(["--resume_model", str(args.resume_model.resolve())])
    if args.iters is not None:
        command.extend(["--iters", str(args.iters)])
    if args.batch_size is not None:
        command.extend(["--batch_size", str(args.batch_size)])
    if args.learning_rate is not None:
        command.extend(["--learning_rate", str(args.learning_rate)])
    return command


def render_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(message)s")

    args.config = args.config.resolve()
    args.dataset_root = args.dataset_root.resolve()
    args.save_dir = args.save_dir.resolve()
    paddleseg_root = resolve_paddleseg_root(args)

    try:
        maybe_bootstrap_paddleseg(paddleseg_root, args.bootstrap_paddleseg)
        ensure_ready(
            args.config,
            args.dataset_root,
            paddleseg_root,
            require_paddle=not args.dry_run,
        )
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        LOGGER.error("%s", exc)
        LOGGER.info("Config: %s", args.config)
        LOGGER.info("Dataset: %s", args.dataset_root)
        LOGGER.info("PaddleSeg: %s", paddleseg_root)
        return 1

    args.save_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(args, paddleseg_root)
    LOGGER.info("Training command:")
    LOGGER.info("%s", render_command(command))

    if args.dry_run:
        return 0

    env = os.environ.copy()
    env.setdefault("PADDLESEG_ROOT", str(paddleseg_root))
    existing_pythonpath = env.get("PYTHONPATH", "")
    pythonpath_parts = [str(paddleseg_root)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    process = subprocess.run(command, cwd=Path.cwd(), env=env)
    return int(process.returncode)


if __name__ == "__main__":
    sys.exit(main())
