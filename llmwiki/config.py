"""Configuration loader for LLM Wiki."""

from pathlib import Path
import yaml


# Project root is one level up from this file's parent (llmwiki/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    """Load config.yaml and resolve paths to absolute."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    # Read API key from file
    key_file = PROJECT_ROOT / cfg["api"]["key_file"]
    cfg["api"]["key"] = key_file.read_text().strip()

    # Resolve directory paths to absolute
    cfg["paths"]["wiki_dir"] = str(PROJECT_ROOT / cfg["paths"]["wiki_dir"])
    cfg["paths"]["raw_dir"] = str(PROJECT_ROOT / cfg["paths"]["raw_dir"])
    cfg["paths"]["prompts_dir"] = str(PROJECT_ROOT / cfg["paths"]["prompts_dir"])

    return cfg
