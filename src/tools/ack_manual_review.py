"""CLI to acknowledge manual review flags."""

from __future__ import annotations

import argparse

from src.config.settings import load_settings
from src.ledger import EventLedger, EventType, StateManager


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Acknowledge manual review and clear the flag in the ledger."
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--reason",
        default="manual_ack",
        help="Reason for acknowledging manual review",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    ledger = EventLedger(settings.storage.ledger_path)
    state_manager = StateManager(
        initial_equity=100.0,
        risk_config=settings.risk,
        news_config=settings.news,
    )
    state_manager.rebuild(ledger.load_all())
    was_flagged = state_manager.state.requires_manual_review

    ledger.append(
        EventType.MANUAL_REVIEW_ACKNOWLEDGED,
        {"reason": args.reason, "previously_flagged": was_flagged},
        {"source": "manual_review_ack"},
    )

    if was_flagged:
        print("Manual review acknowledged. Restart the bot to resume trading.")
    else:
        print("Manual review already clear. Recorded acknowledgement event.")


if __name__ == "__main__":
    main()
