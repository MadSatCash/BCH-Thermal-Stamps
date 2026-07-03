"""BCH wallet operations backed by the bitcash library.

Each stamp owns its own freshly generated BCH key. The private key (WIF) is what
the recipient scans to sweep the funds, and it is also what lets the creator
recover unclaimed funds later. Key generation, WIF and CashAddr all work offline;
only the balance/sweep helpers talk to the BCH network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bitcash import Key
from bitcash.network import NetworkAPI


@dataclass
class StampKey:
    wif: str
    address: str  # CashAddr, e.g. bitcoincash:qz...

    @property
    def address_short(self) -> str:
        return _short_address(self.address)


def new_key() -> StampKey:
    """Generate a brand new random BCH key (secure RNG, offline)."""
    key = Key()
    return StampKey(wif=key.to_wif(), address=key.address)


def key_from_wif(wif: str) -> StampKey:
    """Rebuild a key from its WIF. Raises if the WIF is invalid."""
    key = Key(wif)
    return StampKey(wif=key.to_wif(), address=key.address)


def is_valid_wif(wif: str) -> bool:
    try:
        Key(wif)
        return True
    except Exception:
        return False


@dataclass
class Movement:
    """One on-chain movement of the stamp's own address."""

    txid: str
    direction: str  # "in" = received funds, "out" = funds left the address
    amount_sats: int  # received (in) or net leaving the address (out)
    block: int | None  # block height, if confirmed
    time: int | None  # unix timestamp of the block, if the API gives it


@dataclass
class AddressHistory:
    balance_sats: int
    movements: list[Movement] = field(default_factory=list)

    @property
    def tx_count(self) -> int:
        return len(self.movements)

    @property
    def ever_used(self) -> bool:
        # Any movement at all means the stamp was funded at some point, even if
        # the funds already came and went (claimed by the recipient or swept).
        return bool(self.movements)

    @property
    def total_received_sats(self) -> int:
        return sum(m.amount_sats for m in self.movements if m.direction == "in")

    @property
    def total_sent_sats(self) -> int:
        return sum(m.amount_sats for m in self.movements if m.direction == "out")


def _address_body(address: str | None) -> str:
    # Compare addresses ignoring the "bitcoincash:" / "bchtest:" prefix and case.
    return address.split(":")[-1].lower() if address else ""


def _block_time(txid: str) -> int | None:
    # bitcash's Transaction object only carries the block height, not a wall-clock
    # time, but the verbose raw transaction does (blocktime). Best-effort: a flaky
    # endpoint just means we fall back to showing the block height instead.
    try:
        raw = NetworkAPI.get_raw_transaction(txid)
        if isinstance(raw, dict):
            stamp = raw.get("blocktime") or raw.get("time")
            return int(stamp) if stamp else None
    except Exception:
        pass
    return None


def get_address_history(wif: str) -> AddressHistory:
    """Balance plus the full movement history of the stamp's address. Requires
    internet.

    The history is what tells "funded then claimed" apart from "never funded": a
    stamp whose funds arrived and were swept shows a zero balance just like a
    brand-new one, so the balance alone can't reveal that it was used - but the
    transactions can, along with how much moved and when.
    """
    key = Key(wif)
    address = _address_body(key.address)
    balance = int(key.get_balance("satoshi"))
    try:
        txids = key.get_transactions()
    except Exception:
        # If the history lookup fails (e.g. flaky API), fall back to balance-only.
        return AddressHistory(balance_sats=balance)

    movements: list[Movement] = []
    for txid in txids:
        try:
            tx = NetworkAPI.get_transaction(txid)
        except Exception:
            continue
        received = sum(o.amount for o in tx.outputs if _address_body(o.address) == address)
        sent = sum(i.amount for i in tx.inputs if _address_body(i.address) == address)
        if received >= sent:
            direction, amount = "in", received
        else:
            direction, amount = "out", sent - received  # net that left the address
        movements.append(Movement(txid, direction, amount, tx.block, _block_time(txid)))

    # Chronological order; within the same block show the incoming (funding)
    # before the outgoing (claim).
    movements.sort(key=lambda m: (m.time or 0, m.block or 0, 0 if m.direction == "in" else 1))
    return AddressHistory(balance_sats=balance, movements=movements)


def sweep_to(wif: str, destination: str) -> str:
    """Send ALL funds from the stamp key to `destination`. Returns the txid.

    Requires internet. Used both to recover unclaimed stamps and, in general, to
    move every satoshi the key controls (minus the network fee) to one address.
    """
    key = Key(wif)
    return key.send([], leftover=destination, combine=True)


def _short_address(address: str) -> str:
    body = address.split(":", 1)[-1]
    if len(body) <= 22:
        return body
    return f"{body[:12]}...{body[-6:]}"
