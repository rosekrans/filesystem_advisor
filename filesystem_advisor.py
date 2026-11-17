#!/usr/bin/env python3
"""
Filesystem Cleanup Advisor
--------------------------
Uses a local Ollama model (default: phi3:3.8b) to analyze filesystem data
and provide careful cleanup advice.

Core idea:
  You feed data → model analyzes it → everything is saved to history.
  Interactive chatting is optional (only if you pass --chat).

Features:
- Gathers cache/directory size summaries and package information
- Can also accept external data files (--data)
- Saves every run (prompt + model response + snapshot) into history
- Can continue from previous sessions so context is remembered
- NEVER deletes anything — only advises

Requirements:
    pip install requests

Typical usage (non-interactive):
    python filesystem_advisor.py                     # new scan + analysis
    python filesystem_advisor.py --continue          # new scan, keep previous context
    python filesystem_advisor.py --data mydata.json  # feed external data
    python filesystem_advisor.py --chat              # enter interactive mode after analysis
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "phi3:3.8b"
OLLAMA_URL = "http://localhost:11434/api/chat"
HISTORY_DIR = Path("fs_advisor_history")
MAX_DEPTH = 2
TOP_N_LARGE = 15

DEFAULT_SCAN_PATHS = [
    "~/.cache",
    "~/.local/share",
    "/tmp",
    "~/Library/Caches",          # macOS
    "~/.npm",
    "~/.cache/pip",
    "~/.cache/huggingface",
    "~/.cache/torch",
    "~/.cache/yarn",
    "~/.gradle/caches",
    "~/.m2/repository",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def human_size(num_bytes: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def get_dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError):
        pass
    return total


def scan_path(path: Path, max_depth: int = MAX_DEPTH) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir() if path.exists() else False,
        "size_bytes": 0,
        "size_human": "0 B",
        "top_items": [],
        "error": None,
    }

    if not path.exists():
        result["error"] = "does not exist"
        return result

    try:
        if path.is_file():
            size = path.stat().st_size
            result["size_bytes"] = size
            result["size_human"] = human_size(size)
            return result

        size = get_dir_size(path)
        result["size_bytes"] = size
        result["size_human"] = human_size(size)

        items = []
        for child in path.iterdir():
            try:
                if child.is_file():
                    sz = child.stat().st_size
                    items.append({"name": child.name, "type": "file", "size": sz})
                elif child.is_dir():
                    sz = get_dir_size(child)
                    items.append({"name": child.name, "type": "dir", "size": sz})
            except (OSError, PermissionError):
                continue

        items.sort(key=lambda x: x["size"], reverse=True)
        result["top_items"] = [
            {
                "name": i["name"],
                "type": i["type"],
                "size_human": human_size(i["size"]),
                "size_bytes": i["size"],
            }
            for i in items[:TOP_N_LARGE]
        ]

    except Exception as e:
        result["error"] = str(e)

    return result


def gather_filesystem_data(scan_paths: list[str] | None = None) -> dict[str, Any]:
    paths = scan_paths or DEFAULT_SCAN_PATHS
    expanded = [Path(os.path.expanduser(p)).resolve() for p in paths]

    data: dict[str, Any] = {
        "timestamp": dt.datetime.now().isoformat(),
        "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
        "scanned_paths": [],
        "total_scanned_bytes": 0,
        "packages": {},
    }

    for p in expanded:
        summary = scan_path(p)
        data["scanned_paths"].append(summary)
        data["total_scanned_bytes"] += summary.get("size_bytes", 0)

    data["total_scanned_human"] = human_size(data["total_scanned_bytes"])
    data["packages"]["pip"] = get_pip_list()
    data["packages"]["notes"] = "Only basic pip list is collected."

    return data


def get_pip_list() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return []


def load_external_data(path: Path) -> dict[str, Any]:
    """Load data from a JSON or plain-text file."""
    text = path.read_text(encoding="utf-8")

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            if "scanned_paths" not in data:
                data = {
                    "timestamp": dt.datetime.now().isoformat(),
                    "hostname": "external",
                    "scanned_paths": [],
                    "total_scanned_bytes": 0,
                    "total_scanned_human": "unknown",
                    "packages": {},
                    "raw_text": text,
                    "external_file": str(path),
                }
            return data
        except json.JSONDecodeError:
            pass

    # Treat as plain text report
    return {
        "timestamp": dt.datetime.now().isoformat(),
        "hostname": "external",
        "scanned_paths": [],
        "total_scanned_bytes": 0,
        "total_scanned_human": "unknown",
        "packages": {},
        "raw_text": text,
        "external_file": str(path),
    }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a careful, experienced system administrator and cleanup advisor.
Your job is to analyze filesystem and package data and give practical, safe advice on what can be cleaned up.

Rules you must follow:
1. Prioritize safety. Never recommend deleting anything that could break the system or important user data without clear warnings.
2. Distinguish between:
   - Safe / high-confidence cleanups (old caches, temporary build artifacts, obvious orphans)
   - Items that need human review
   - Items that should generally be left alone
3. When possible, estimate potential space savings.
4. Suggest concrete commands the user can run, but always recommend reviewing them first.
5. Be concise but thorough. Use bullet points and clear sections.
6. If the data is incomplete, say so and ask for more specific information rather than guessing.

Respond in a structured way:
- Executive summary (1-3 sentences)
- High-priority safe cleanups
- Items needing review
- Suggested next actions / commands
- Any risks or caveats
"""


def build_user_message(fs_data: dict[str, Any], extra_question: str | None = None) -> str:
    if "raw_text" in fs_data:
        lines = [
            f"External data file: {fs_data.get('external_file', 'unknown')}",
            f"Timestamp: {fs_data['timestamp']}",
            "",
            "=== Provided Data ===",
            fs_data["raw_text"],
            "",
            "=== Request ===",
        ]
        if extra_question:
            lines.append(extra_question)
        else:
            lines.append(
                "Please analyze the above data and give cleanup advice. "
                "Focus on caches, temporary files, and obviously unused items. "
                "Prioritize safety and explain your reasoning briefly."
            )
        return "\n".join(lines)

    summary_lines = [
        f"Scan timestamp: {fs_data['timestamp']}",
        f"Hostname: {fs_data['hostname']}",
        f"Total size of scanned locations: {fs_data.get('total_scanned_human', 'unknown')}",
        "",
        "=== Scanned Paths ===",
    ]

    for entry in fs_data.get("scanned_paths", []):
        summary_lines.append(f"\nPath: {entry['path']}")
        if entry.get("error"):
            summary_lines.append(f"  Error: {entry['error']}")
            continue
        summary_lines.append(f"  Size: {entry.get('size_human', '?')}")
        if entry.get("top_items"):
            summary_lines.append("  Largest items:")
            for item in entry["top_items"][:8]:
                summary_lines.append(
                    f"    - {item['name']} ({item['type']}) : {item['size_human']}"
                )

    pip_pkgs = fs_data.get("packages", {}).get("pip", [])
    summary_lines.append("\n=== Installed pip packages (sample) ===")
    if pip_pkgs:
        for pkg in pip_pkgs[:40]:
            summary_lines.append(f"  {pkg.get('name', '?')}=={pkg.get('version', '?')}")
        if len(pip_pkgs) > 40:
            summary_lines.append(f"  ... and {len(pip_pkgs) - 40} more packages")
    else:
        summary_lines.append("  (could not retrieve pip list)")

    summary_lines.append("\n=== Request ===")
    if extra_question:
        summary_lines.append(extra_question)
    else:
        summary_lines.append(
            "Please analyze the above data and give cleanup advice. "
            "Focus on caches, temporary files, and obviously unused items. "
            "Prioritize safety and explain your reasoning briefly."
        )

    return "\n".join(summary_lines)


# ---------------------------------------------------------------------------
# Ollama + history
# ---------------------------------------------------------------------------

def call_ollama(
    messages: list[dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 8192,
        },
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]
    except requests.exceptions.ConnectionError:
        print("\n[ERROR] Could not connect to Ollama at", OLLAMA_URL)
        print("Make sure Ollama is running and the model is pulled:")
        print(f"    ollama pull {model}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Ollama request failed: {e}")
        sys.exit(1)


def save_session(
    session_id: str,
    messages: list[dict[str, str]],
    fs_data: dict[str, Any] | None = None,
    model: str = DEFAULT_MODEL,
) -> Path:
    HISTORY_DIR.mkdir(exist_ok=True)
    path = HISTORY_DIR / f"{session_id}.json"

    record = {
        "session_id": session_id,
        "model": model,
        "created": dt.datetime.now().isoformat(),
        "updated": dt.datetime.now().isoformat(),
        "messages": messages,
        "filesystem_snapshot": fs_data,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    return path


def load_session(session_id: str) -> dict[str, Any] | None:
    path = HISTORY_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_sessions() -> list[str]:
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.stem for f in files]


def new_session_id() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filesystem Cleanup Advisor – feed data, get advice, keep history"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--continue", dest="continue_last", action="store_true",
                        help="Keep previous conversation context while adding new data")
    parser.add_argument("--session", type=str, help="Continue a specific session ID")
    parser.add_argument("--list-sessions", action="store_true", help="List saved sessions")
    parser.add_argument("--scan", nargs="*", help="Override default scan paths")
    parser.add_argument("--data", type=str, help="Load data from external file (JSON or text)")
    parser.add_argument("--chat", action="store_true",
                        help="Enter interactive chat mode after the analysis")
    args = parser.parse_args()

    if args.list_sessions:
        sessions = list_sessions()
        if not sessions:
            print("No saved sessions found.")
        else:
            print("Saved sessions (newest first):")
            for s in sessions:
                print(f"  {s}")
        return

    # Session handling
    session_id: str
    messages: list[dict[str, str]] = []

    if args.session:
        loaded = load_session(args.session)
        if not loaded:
            print(f"Session '{args.session}' not found.")
            sys.exit(1)
        session_id = args.session
        messages = loaded.get("messages", [])
        print(f"Loaded session: {session_id}")
    elif args.continue_last:
        sessions = list_sessions()
        if sessions:
            session_id = sessions[0]
            loaded = load_session(session_id)
            messages = loaded.get("messages", [])
            print(f"Continuing session: {session_id} (previous context kept)")
        else:
            session_id = new_session_id()
            print(f"No previous sessions – starting new: {session_id}")
    else:
        session_id = new_session_id()
        print(f"Starting new session: {session_id}")

    # Obtain data
    if args.data:
        data_path = Path(args.data).expanduser()
        if not data_path.exists():
            print(f"[ERROR] Data file not found: {data_path}")
            sys.exit(1)
        print(f"Loading external data from: {data_path}")
        fs_data = load_external_data(data_path)
    else:
        print("Gathering filesystem information...")
        fs_data = gather_filesystem_data(args.scan)
        print(f"Scanned total: {fs_data.get('total_scanned_human', 'unknown')}")

    # Build prompt and call model
    user_content = build_user_message(fs_data)

    if not messages:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    else:
        messages.append({"role": "user", "content": user_content})

    print("\nSending data to model... (this may take a moment)")
    reply = call_ollama(messages, model=args.model)
    messages.append({"role": "assistant", "content": reply})

    print("\n" + "=" * 60)
    print("MODEL ADVICE")
    print("=" * 60)
    print(reply)
    print("=" * 60)

    save_path = save_session(session_id, messages, fs_data, model=args.model)
    print(f"\nSession saved to: {save_path}")

    # Interactive mode is OPTIONAL
    if not args.chat:
        print("\nDone. (Use --chat if you want to ask follow-up questions)")
        return

    print("\nInteractive mode. Type questions or 'quit' / 'exit' to stop.")
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if user_input.lower() in {"quit", "exit", "q"}:
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        print("Thinking...")
        reply = call_ollama(messages, model=args.model)
        messages.append({"role": "assistant", "content": reply})

        print("\n" + "-" * 40)
        print(reply)
        print("-" * 40)

        save_session(session_id, messages, fs_data, model=args.model)

    print(f"\nFinal session saved under ID: {session_id}")


if __name__ == "__main__":
    main()
