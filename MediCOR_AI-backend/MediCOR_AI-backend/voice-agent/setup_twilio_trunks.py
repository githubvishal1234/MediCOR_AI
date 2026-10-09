r"""Provision the LiveKit SIP trunks for Twilio Elastic SIP Trunking.

Replaces the two hardcoded Vobiz scripts (`fix_sip_trunk.py`,
`fix_sip_trunk_catchall.py`). Nothing here is hardcoded: every value comes
from `.env`, so this can be re-run against a different account, a test DID
or a second LiveKit project without editing the file.

WHAT IT DOES

  1. OUTBOUND trunk  -- created if absent, updated in place if a trunk with
     the same address already exists. This is what `agent.py` passes to
     `create_sip_participant()` as `sip_trunk_id`. There is currently NO
     outbound trunk on this project, which is why outbound dialling fails.

  2. INBOUND trunk   -- the Twilio DID is ADDED to the `numbers` list. The
     existing numbers are preserved, so the old carrier keeps working
     while you test. Set LIVEKIT_INBOUND_TRUNK_ID to extend a specific
     trunk; leave it unset to create a new one.

  3. Prints the two values to paste into `.env`:  SIP_TRUNK_ID, SIP_DOMAIN.

It does NOT touch dispatch rules. The existing rule is trunk-scoped and
already dispatches the `outbound-caller` agent, so extending its trunk is
enough. Creating a NEW inbound trunk, however, means the dispatch rule
will not cover it -- the script says so explicitly when that happens.

USAGE

    ..\venv\Scripts\python.exe setup_twilio_trunks.py             # dry run
    ..\venv\Scripts\python.exe setup_twilio_trunks.py --apply
    ..\venv\Scripts\python.exe setup_twilio_trunks.py --restore

REQUIRED IN .env

    TWILIO_TERMINATION_URI       e.g. medicare-xyz.pstn.twilio.com
    TWILIO_TRUNK_AUTH_USERNAME   Termination Credential List username
    TWILIO_TRUNK_AUTH_PASSWORD   ...and password
    TWILIO_PHONE_NUMBER          the DID, E.164

OPTIONAL

    LIVEKIT_INBOUND_TRUNK_ID     extend this trunk instead of creating one
    TWILIO_SIP_TRANSPORT         auto (default) | udp | tcp | tls
                                 TLS requires Secure Trunking enabled on
                                 the Twilio trunk -- do not set it blind.
"""
import asyncio
import json
import os
import pathlib
import sys

from dotenv import load_dotenv

HERE = pathlib.Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

from livekit import api
from livekit.protocol import sip as sipproto

APPLY = "--apply" in sys.argv
RESTORE = "--restore" in sys.argv

# Keep the rollback record with the other migration artefacts when that
# folder exists -- it is gitignored wholesale. Otherwise sit next to this
# script.
_root = HERE.parents[2] / "_twilio-migration-backup"
BACKUP = (_root if _root.is_dir() else HERE) / "twilio-trunk-backup.json"

OUTBOUND_NAME = "MediCOR AI Outbound (Twilio)"
INBOUND_NAME = "MediCOR AI Inbound (Twilio)"

PLACEHOLDERS = {
    "", "REPLACE_ME", "your-subdomain.pstn.twilio.com",
    "+10000000000", "ST_xxxxxxxxxxxx",
}

TRANSPORTS = {
    "auto": sipproto.SIPTransport.SIP_TRANSPORT_AUTO,
    "udp": sipproto.SIPTransport.SIP_TRANSPORT_UDP,
    "tcp": sipproto.SIPTransport.SIP_TRANSPORT_TCP,
    "tls": sipproto.SIPTransport.SIP_TRANSPORT_TLS,
}


def need(name: str) -> str:
    """Fetch a required setting, refusing placeholders."""
    value = (os.getenv(name) or "").strip()
    if value in PLACEHOLDERS:
        print(
            f"!! {name} is not set (or is still a placeholder).\n"
            f"   Uncomment and fill it in voice-agent/.env, then re-run.",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


def e164(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        print(f"!! {raw!r} does not look like a phone number.", file=sys.stderr)
        sys.exit(2)
    return "+" + digits


def livekit_client() -> api.LiveKitAPI:
    for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if not os.getenv(key):
            print(f"!! {key} is not set in voice-agent/.env", file=sys.stderr)
            sys.exit(2)
    return api.LiveKitAPI(
        os.environ["LIVEKIT_URL"],
        os.environ["LIVEKIT_API_KEY"],
        os.environ["LIVEKIT_API_SECRET"],
    )


def save_backup(payload: dict) -> None:
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    if BACKUP.exists():
        print(f"   backup already exists, keeping it -> {BACKUP}")
        return
    BACKUP.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"   backup written -> {BACKUP}")


# ---------------------------------------------------------------- outbound

async def ensure_outbound(lk, address, username, password, number, transport):
    listed = await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
    existing = next((t for t in listed.items if t.address == address), None)

    if existing:
        print(f"   outbound trunk already exists for {address}")
        print(f"     id      : {existing.sip_trunk_id}")
        print(f"     numbers : {list(existing.numbers)}")
        if number in list(existing.numbers):
            print("     caller id already present -- nothing to change")
            return existing.sip_trunk_id, False

        if not APPLY:
            print(f"     WOULD add caller id {number}")
            return existing.sip_trunk_id, True

        info = sipproto.SIPOutboundTrunkInfo()
        info.CopyFrom(existing)
        info.numbers.append(number)
        updated = await lk.sip.update_sip_outbound_trunk(existing.sip_trunk_id, info)
        print(f"     added caller id -> numbers={list(updated.numbers)}")
        return updated.sip_trunk_id, True

    if not APPLY:
        print("   WOULD CREATE outbound trunk")
        print(f"     name      : {OUTBOUND_NAME}")
        print(f"     address   : {address}")
        print(f"     transport : {transport}")
        print(f"     numbers   : ['{number}']")
        print(f"     auth user : {username}")
        return None, True

    trunk = sipproto.SIPOutboundTrunkInfo(
        name=OUTBOUND_NAME,
        address=address,
        transport=TRANSPORTS[transport],
        numbers=[number],
        auth_username=username,
        auth_password=password,
    )
    created = await lk.sip.create_sip_outbound_trunk(
        api.CreateSIPOutboundTrunkRequest(trunk=trunk)
    )
    print(f"   CREATED outbound trunk {created.sip_trunk_id}")
    return created.sip_trunk_id, True


# ----------------------------------------------------------------- inbound

async def ensure_inbound(lk, number, trunk_id):
    listed = await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
    trunks = list(listed.items)

    target = None
    if trunk_id:
        target = next((t for t in trunks if t.sip_trunk_id == trunk_id), None)
        if target is None:
            print(f"!! LIVEKIT_INBOUND_TRUNK_ID={trunk_id} not found on this project.",
                  file=sys.stderr)
            sys.exit(3)
    elif len(trunks) == 1:
        target = trunks[0]
        print(f"   one inbound trunk found, extending it: {target.sip_trunk_id}")

    if target is not None:
        print(f"   inbound trunk : {target.sip_trunk_id} ({target.name!r})")
        print(f"     numbers now : {list(target.numbers)}")
        if number in list(target.numbers):
            print("     DID already present -- nothing to change")
            return target.sip_trunk_id, None

        if not APPLY:
            print(f"     WOULD add {number}, preserving the existing entries")
            return target.sip_trunk_id, None

        backup = {
            "inbound_trunk_id": target.sip_trunk_id,
            "numbers": list(target.numbers),
            "allowed_addresses": list(target.allowed_addresses),
        }
        save_backup(backup)

        info = sipproto.SIPInboundTrunkInfo()
        info.CopyFrom(target)
        info.numbers.append(number)
        updated = await lk.sip.update_sip_inbound_trunk(target.sip_trunk_id, info)
        print(f"     numbers now : {list(updated.numbers)}")
        return updated.sip_trunk_id, None

    # No trunk to extend -- create one, and warn about dispatch.
    if not APPLY:
        print("   WOULD CREATE inbound trunk")
        print(f"     name    : {INBOUND_NAME}")
        print(f"     numbers : ['{number}']")
        return None, "created"

    trunk = sipproto.SIPInboundTrunkInfo(name=INBOUND_NAME, numbers=[number])
    created = await lk.sip.create_sip_inbound_trunk(
        api.CreateSIPInboundTrunkRequest(trunk=trunk)
    )
    print(f"   CREATED inbound trunk {created.sip_trunk_id}")
    return created.sip_trunk_id, "created"


# ----------------------------------------------------------------- restore

async def restore(lk):
    if not BACKUP.exists():
        print(f"!! no backup at {BACKUP}", file=sys.stderr)
        sys.exit(3)
    saved = json.loads(BACKUP.read_text(encoding="utf-8"))
    trunk_id = saved["inbound_trunk_id"]

    listed = await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
    target = next((t for t in listed.items if t.sip_trunk_id == trunk_id), None)
    if target is None:
        print(f"!! inbound trunk {trunk_id} no longer exists", file=sys.stderr)
        sys.exit(3)

    print(f"restoring inbound trunk {trunk_id}")
    print(f"  numbers  {list(target.numbers)}  ->  {saved['numbers']}")
    if not APPLY:
        print("  (dry run -- pass --apply as well to actually restore)")
        return

    info = sipproto.SIPInboundTrunkInfo()
    info.CopyFrom(target)
    del info.numbers[:]
    info.numbers.extend(saved["numbers"])
    del info.allowed_addresses[:]
    info.allowed_addresses.extend(saved.get("allowed_addresses", []))
    after = await lk.sip.update_sip_inbound_trunk(trunk_id, info)
    print(f"  restored -> numbers={list(after.numbers)}")
    print("\nNote: an outbound trunk created by --apply is NOT deleted here.")
    print("Remove it from the LiveKit dashboard if you want it gone.")


# -------------------------------------------------------------------- main

async def main():
    lk = livekit_client()
    try:
        if RESTORE:
            await restore(lk)
            return

        address = need("TWILIO_TERMINATION_URI")
        username = need("TWILIO_TRUNK_AUTH_USERNAME")
        password = need("TWILIO_TRUNK_AUTH_PASSWORD")
        number = e164(need("TWILIO_PHONE_NUMBER"))
        inbound_id = (os.getenv("LIVEKIT_INBOUND_TRUNK_ID") or "").strip()

        transport = (os.getenv("TWILIO_SIP_TRANSPORT") or "auto").strip().lower()
        if transport not in TRANSPORTS:
            print(f"!! TWILIO_SIP_TRANSPORT={transport!r} -- use auto, udp, tcp or tls.",
                  file=sys.stderr)
            sys.exit(2)

        mode = "APPLY" if APPLY else "DRY RUN (nothing will be changed)"
        print("=" * 64)
        print(f"Twilio <-> LiveKit SIP provisioning  [{mode}]")
        print("=" * 64)
        print(f"LiveKit project : {os.environ['LIVEKIT_URL']}")
        print(f"Twilio host     : {address}")
        print(f"Twilio DID      : {number}")
        print(f"Transport       : {transport}")
        print()

        print("[1/2] OUTBOUND")
        out_id, _ = await ensure_outbound(
            lk, address, username, password, number, transport
        )
        print()

        print("[2/2] INBOUND")
        in_id, in_state = await ensure_inbound(lk, number, inbound_id)
        print()

        print("=" * 64)
        if APPLY:
            print("Add these two lines to voice-agent/.env (uncommented):")
            print()
            print(f"    SIP_TRUNK_ID={out_id}")
            print(f"    SIP_DOMAIN={address}")
            print()
            print("Then restart the agent worker.")
            if in_state == "created":
                print()
                print("!! A NEW inbound trunk was created. The existing SIP dispatch")
                print("   rule is scoped to the OLD trunk, so inbound calls on this")
                print("   DID will NOT reach the agent until you add the new trunk id")
                print(f"   ({in_id}) to that rule's trunk_ids.")
        else:
            print("Dry run only. Re-run with --apply to make these changes.")
        print("=" * 64)
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
