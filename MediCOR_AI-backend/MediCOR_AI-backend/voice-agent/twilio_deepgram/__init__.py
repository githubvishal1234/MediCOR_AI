r"""
Twilio + Deepgram Voice Agent gateway.

The replacement for the LiveKit worker in `voice-agent/agent.py`, and
deliberately a *sibling* of it rather than an edit to it: both stacks can
be run, one at a time, against the same backend and the same database
until the new one has proved itself on real calls. Nothing here imports
livekit, and nothing in agent.py imports this.

    Patient -> Twilio -> (this gateway) -> Deepgram Voice Agent
                              |
                              +-> backend_client -> FastAPI -> SQL Server

What it keeps from the old stack, unchanged:

  * `backend_client.TenantClient` is the entire business-data transport,
    reused as-is. Every tool is a thin call into a method that already
    existed and was already exercised by live calls.
  * Multi-tenancy through `POST /agent/resolve-tenant`, keyed on the
    dialled number -- which Twilio hands us outright as the `To`
    parameter, so the polling agent.py needed to discover it is gone.
  * The agent still holds NO business logic. No slot maths, no
    validation, no clinical decisions, no SQL. If a rule is needed, it
    belongs in the backend.

Run it with:

    ..\venv\Scripts\python.exe -m twilio_deepgram.run
"""
