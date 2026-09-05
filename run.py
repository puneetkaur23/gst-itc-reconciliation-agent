import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from agent import GSTReconciliationAgent


def main():
    agent = GSTReconciliationAgent(
        str(Path(__file__).parent / "data"),
        str(Path(__file__).parent / "output"),
    )
    result = agent.run()
    agent.save_reports()
    print("\n[OK] Reconciliation complete.")


if __name__ == "__main__":
    main()

