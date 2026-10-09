/**
 * What your AI receptionist does on a call.
 *
 * Written for a clinic manager, not an engineer. Three rules hold it:
 *
 * 1. **No vendor or technology is named.** Not the telephony carrier, the
 *    speech recogniser, the model or the voice. Those are implementation
 *    details a clinic neither chooses nor needs, and naming them here
 *    would date the page the moment any of them changed.
 *
 * 2. **The six stages are an explanation, not telemetry.** They describe
 *    what happens on every call, whether or not one is happening now. The
 *    dashboard WebSocket carries nine event types and not one of them
 *    reports the agent's per-turn state — there is no `agent.listening`,
 *    no tool-call event, and `/agent-settings` returns only a voice
 *    profile and a greeting. Animating "Listening → Processing → Taking
 *    action" from nothing would be a fabricated status display, which is
 *    the exact defect this redesign removed from the patient profile.
 *
 * 3. **The one live claim is derived from data that exists.**
 *    `useRealtime().liveCalls` is real, already populated and reconciled
 *    against `GET /calls` every ten seconds. So the indicator says one of
 *    two observable things: no calls in progress, or this many. It does
 *    not claim the line is "online", because nothing here can see that.
 *
 * If per-turn state is ever wanted, the backend change is written up in
 * FRONTEND_BACKEND_DEPENDENCIES.md §1 — and each stage below already
 * takes an `active` prop that nothing currently sets.
 */

import { Link } from "react-router-dom";
import {
  PhoneIncoming, Ear, CalendarSearch, Wand2, Database, MessageCircle,
} from "lucide-react";
import { Card, Chip, LiveDot } from "./ui";
import { useRealtime } from "../lib/realtime";

const STAGES = [
  {
    icon: PhoneIncoming,
    title: "Patient calls",
    detail: "The line is answered on the first ring, at any hour.",
  },
  {
    icon: Ear,
    title: "Understands the request",
    detail: "The caller explains what they need in their own words — no menus, no keypad.",
  },
  {
    icon: CalendarSearch,
    title: "Checks patient & availability",
    detail: "Looks the caller up on your roster and reads your doctors' real open slots.",
  },
  {
    icon: Wand2,
    title: "Takes action",
    detail: "Books, moves or cancels an appointment, answers from your documents, or takes a repeat-prescription request.",
  },
  {
    icon: Database,
    title: "Updates the clinic system",
    detail: "The change lands in your calendar and patient records straight away, with a transcript of the call.",
  },
  {
    icon: MessageCircle,
    title: "Confirms with the patient",
    detail: "Reads the outcome back on the call, and messages a confirmation where the patient has agreed to be contacted.",
  },
];

function Stage({ stage, index, active = false }) {
  const Icon = stage.icon;
  return (
    <li className="relative flex gap-3 sm:flex-1 sm:flex-col sm:gap-0">
      {/* The connector. Horizontal between columns above `sm`, vertical
          down the gutter below it — the same sequence either way. */}
      <span
        aria-hidden="true"
        className="absolute left-[15px] top-8 h-[calc(100%-8px)] w-px bg-outline-variant last:hidden sm:left-auto sm:right-0 sm:top-4 sm:h-px sm:w-[calc(100%-2rem)]"
      />
      <span
        className={`relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border ${
          active
            ? "border-accent bg-accent text-accent-contrast"
            : "border-outline-variant bg-surface-lowest text-on-surface-variant"
        }`}
      >
        <Icon size={15} aria-hidden="true" />
      </span>
      <div className="pb-6 sm:pb-0 sm:pr-4 sm:pt-3">
        <p className="text-sm font-semibold text-on-surface">
          <span className="mr-1.5 tabular-nums text-on-surface-variant/70">{index + 1}.</span>
          {stage.title}
        </p>
        <p className="mt-0.5 text-xs leading-relaxed text-on-surface-variant">{stage.detail}</p>
      </div>
    </li>
  );
}

export default function VoiceFlow() {
  const { liveCalls } = useRealtime();
  const count = liveCalls.length;

  return (
    <Card className="mb-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-bold text-on-surface">
            How your AI receptionist handles a call
          </h2>
          <p className="mt-0.5 text-sm text-on-surface-variant">
            Every call follows the same six steps. The settings below change how it sounds and
            when it hands over to a person.
          </p>
        </div>

        {/* Two observable states, and nothing else. "Ready" means no call
            is in progress right now — not that the phone line is healthy,
            which this page cannot see. */}
        {count > 0 ? (
          <Link to="/app/calls" className="focus-ring rounded-full">
            <Chip tone="live" className="gap-2">
              <LiveDot />
              {count === 1 ? "1 call in progress" : `${count} calls in progress`}
            </Chip>
          </Link>
        ) : (
          <Chip tone="neutral">Ready · no calls in progress</Chip>
        )}
      </div>

      <ol className="mt-6 sm:flex sm:gap-0">
        {STAGES.map((stage, i) => (
          <Stage key={stage.title} stage={stage} index={i} />
        ))}
      </ol>

      <p className="mt-2 border-t border-outline-variant pt-4 text-xs text-on-surface-variant">
        Calls are transcribed, never recorded. You can read any call word for word under{" "}
        <Link to="/app/calls" className="font-semibold text-accent hover:underline">
          Call Logs
        </Link>
        .
      </p>
    </Card>
  );
}
