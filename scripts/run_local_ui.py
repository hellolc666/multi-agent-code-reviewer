"""Start the local review workbench on loopback only."""

from pathlib import Path

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    uvicorn.run("reviewer.local_ui:app", host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
