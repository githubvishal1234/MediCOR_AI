import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ChevronRight, Search, Users, Plus, Archive, RotateCcw, X, Languages } from "lucide-react";
import AppShell from "../../components/AppShell";
import {
  Card, Chip, Button, SkeletonTable, EmptyState, ErrorState, InlineAlert, Input,
  Table, THead, TH, SortableTH, TBody, TR, TD,
} from "../../components/ui";
import PatientFormModal from "../../components/PatientFormModal";
import RefillQueue from "../../components/RefillQueue";
import * as api from "../../lib/api";
import { initialsOf } from "../../lib/format";

// The three the voice pipeline speaks. `preferred_language` is detected
// on a call and stored by the backend (F5); until now nothing in the
// patient list surfaced it, so a clinic could not see which of its
// patients the AI answers in Hindi or Telugu.
const LANGUAGE_LABEL = {
  "en-IN": "English",
  "hi-IN": "हिन्दी",
  "te-IN": "తెలుగు",
};

/**
 * Roster filter.
 *
 * Replaces a "Show archived" checkbox, which could only ever answer two of
 * the three questions staff actually ask — it could show the active list
 * or active-plus-archived, but never the archived list on its own, which
 * is the one you want when hunting a record somebody filed by mistake.
 *
 * `includeArchived` is what the API takes, so "archived" asks for both and
 * narrows here. The endpoint has no archived-only mode and inventing a
 * query parameter it ignores would be a control that does nothing.
 */
const FILTERS = [
  { id: "active", label: "Active", includeArchived: false },
  { id: "all", label: "All", includeArchived: true },
  { id: "archived", label: "Archived", includeArchived: true },
];

// Neutral avatars, as on the Dashboard. The old `bg-navy` circle was the
// same colour as the dark-mode page, so in dark mode the circle vanished
// and the initials floated. Archived gets a dashed outline: a filed-away
// record reads differently without spending a colour on it.
function avatarClass(archived) {
  return archived
    ? "border border-dashed border-outline text-on-surface-variant"
    : "bg-surface-container text-on-surface-variant";
}

// Active is the normal state, so it is plain text; a green chip on every
// row said nothing. Only an archived record earns a chip.
function StatusMark({ archived, status }) {
  return archived ? <Chip tone="neutral">{status}</Chip> : <span className="text-on-surface-variant">{status}</span>;
}

const SORTS = {
  name: (p) => (p.name || "").toLowerCase(),
  doctor: (p) => (p.doctor || "").toLowerCase(),
  language: (p) => (p.preferred_language || "").toLowerCase(),
  status: (p) => (p.status || "").toLowerCase(),
};

export default function Patients() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState(() => searchParams.get("search") || "");
  const [patients, setPatients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [filter, setFilter] = useState("active");
  const [showAdd, setShowAdd] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [sort, setSort] = useState({ key: "name", dir: "asc" });

  const showArchived = FILTERS.find((f) => f.id === filter).includeArchived;

  // Stay in sync with the URL (the AppShell topbar search navigates here
  // with a `search` param) without fighting the user's own typing in the
  // input below.
  useEffect(() => {
    const fromUrl = searchParams.get("search") || "";
    setQuery(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get("search")]);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    const handle = setTimeout(() => {
      api
        .listPatients(query || undefined, { includeArchived: showArchived })
        .then((data) => {
          if (cancelled) return;
          setPatients(data);
          setLoading(false);
        })
        .catch(() => {
          if (cancelled) return;
          setError(true);
          setLoading(false);
        });
    }, 250); // debounce search
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query, showArchived]);

  useEffect(() => {
    return load();
  }, [load]);

  function handleQueryChange(value) {
    setQuery(value);
    setSearchParams(value ? { search: value } : {}, { replace: true });
  }

  // Archiving is a soft delete: the record, prescriptions and visit
  // history are all kept. Restoring simply flips the status back.
  async function archive(patient) {
    setActionError(null);
    const previous = patients;
    setPatients((prev) => prev.filter((p) => p.id !== patient.id));
    try {
      await api.archivePatient(patient.id);
      if (showArchived) load();
    } catch {
      setPatients(previous);
      setActionError(`Couldn't archive ${patient.name}.`);
    }
  }

  async function restore(patient) {
    setActionError(null);
    try {
      await api.restorePatient(patient.id);
      load();
    } catch {
      setActionError(`Couldn't restore ${patient.name}.`);
    }
  }

  /**
   * Narrow, then order — both client-side over the whole roster.
   *
   * `GET /patients` returns every match when `limit` is omitted, so this is
   * the complete list rather than a page of it, and sorting it here cannot
   * mislead by reordering only what happens to be loaded. No list endpoint
   * in this API takes a sort parameter; sending one would be a control the
   * server ignores.
   */
  const visible = useMemo(() => {
    const rows = filter === "archived" ? patients.filter((p) => p.status === "Archived") : patients;
    const pick = SORTS[sort.key] ?? SORTS.name;
    return [...rows].sort((a, b) => {
      const av = pick(a);
      const bv = pick(b);
      // Rows missing the sorted value sink to the bottom in both
      // directions — a blank is absent information, not a low value, and
      // reversing the order should not float them to the top.
      if (av === bv) return 0;
      if (!av) return 1;
      if (!bv) return -1;
      return sort.dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    });
  }, [patients, filter, sort]);

  const onSort = useCallback((key, dir) => setSort({ key, dir }), []);

  return (
    <AppShell
      title="Patients"
      subtitle="Every patient your AI receptionist has interacted with."
      actions={
        <Button type="button" onClick={() => setShowAdd(true)}>
          <Plus size={15} aria-hidden="true" /> Add patient
        </Button>
      }
    >
      {/* Repeat prescriptions the AI took by phone. It records them; a
          person decides them — see components/RefillQueue.jsx. Hidden
          entirely when there is nothing outstanding. */}
      <RefillQueue onSelectPatient={(id) => navigate(`/app/patients/${id}`)} />

      {/* Search and the archived toggle belong together — they are both
          "what am I looking at", where the Add button is "do something". */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-sm">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
            aria-hidden="true"
          />
          <Input
            value={query}
            onChange={(e) => handleQueryChange(e.target.value)}
            type="search"
            placeholder="Search by name or MRN…"
            aria-label="Search patients by name or MRN"
            className="pl-9 pr-9"
          />
          {query && (
            <button
              onClick={() => handleQueryChange("")}
              aria-label="Clear search"
              className="focus-ring absolute right-1.5 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container"
            >
              <X size={14} aria-hidden="true" />
            </button>
          )}
        </div>
        {/* A filter group, not a tablist: these narrow one list rather than
            switching between panels, so `aria-pressed` is the honest role
            and arrow-key navigation would be wrong. */}
        <div className="flex gap-0.5 rounded-lg border border-outline-variant bg-surface-lowest p-0.5" role="group" aria-label="Filter patients">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              aria-pressed={filter === f.id}
              className={`focus-ring min-h-9 rounded-md px-3 text-sm font-semibold transition-colors ${
                filter === f.id
                  ? "bg-surface-container text-on-surface"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* `GET /patients` returns every match when `limit` is omitted, so
            this is the whole roster, not a page of it — which is why it
            can be counted here and why there is nothing to paginate. */}
        {!loading && !error && visible.length > 0 && (
          <span className="ml-auto text-sm tabular-nums text-on-surface-variant">
            {visible.length} {visible.length === 1 ? "patient" : "patients"}
          </span>
        )}
      </div>

      {actionError && <InlineAlert className="mb-3">{actionError}</InlineAlert>}

      <Card className="overflow-hidden">
        {loading ? (
          <SkeletonTable rows={5} cols={4} label="Loading patients…" />
        ) : error ? (
          <ErrorState detail="We couldn't load your patients." onRetry={load} className="py-20" />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No patients found"
            detail={
              query
                ? `No results for "${query}" — try a different name or MRN.`
                : filter === "archived"
                ? "Nothing has been archived."
                : "No patients yet."
            }
          />
        ) : (
          <>
            {/* Two renderings of the same rows rather than one squeezed to
                fit. A five-column table on a phone is unreadable, and a
                stacked card list on a wide screen wastes the space that
                makes a patient list scannable. */}
            <div className="hidden sm:block">
              <Table>
                <THead>
                  <tr>
                    <SortableTH sortKey="name" active={sort.key} direction={sort.dir} onSort={onSort}>
                      Patient
                    </SortableTH>
                    <SortableTH sortKey="doctor" active={sort.key} direction={sort.dir} onSort={onSort}>
                      Clinician
                    </SortableTH>
                    <SortableTH sortKey="language" active={sort.key} direction={sort.dir} onSort={onSort}>
                      Language
                    </SortableTH>
                    <SortableTH sortKey="status" active={sort.key} direction={sort.dir} onSort={onSort}>
                      Status
                    </SortableTH>
                    <TH className="text-right">Actions</TH>
                  </tr>
                </THead>
                <TBody>
                  {visible.map((p) => {
                    const archived = p.status === "Archived";
                    return (
                      <TR key={p.id} onClick={() => navigate(`/app/patients/${p.id}`)}>
                        <TD>
                          <div className="flex items-center gap-3">
                            <span
                              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${avatarClass(archived)}`}
                            >
                              {initialsOf(p.name, p.initials)}
                            </span>
                            <div className="min-w-0">
                              <p className={`truncate font-semibold ${archived ? "text-on-surface-variant" : "text-on-surface"}`}>
                                {p.name}
                              </p>
                              <p className="truncate font-mono text-xs text-on-surface-variant">
                                {p.mrn}
                              </p>
                            </div>
                          </div>
                        </TD>
                        <TD className="text-on-surface-variant">{p.doctor || "—"}</TD>
                        <TD>
                          {/* Detected from what the caller actually spoke,
                              not staff-entered. Absent until they have
                              called and said something. */}
                          {p.preferred_language ? (
                            <span
                              className="inline-flex items-center gap-1.5 text-xs text-on-surface-variant"
                              title="Detected on this patient's last call"
                            >
                              <Languages size={13} aria-hidden="true" />
                              {LANGUAGE_LABEL[p.preferred_language] || p.preferred_language}
                            </span>
                          ) : (
                            <span className="text-on-surface-variant">—</span>
                          )}
                        </TD>
                        <TD>
                          <StatusMark archived={archived} status={p.status} />
                        </TD>
                        <TD className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <button
                              onClick={(e) => {
                                // The row itself navigates, so the row
                                // action must not also trigger it.
                                e.stopPropagation();
                                if (archived) restore(p);
                                else archive(p);
                              }}
                              className="focus-ring flex h-10 w-10 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                              aria-label={archived ? `Restore ${p.name}` : `Archive ${p.name}`}
                              title={archived ? "Restore" : "Archive"}
                            >
                              {archived ? <RotateCcw size={16} aria-hidden="true" /> : <Archive size={16} aria-hidden="true" />}
                            </button>
                            <ChevronRight
                              size={16}
                              className="shrink-0 text-on-surface-variant"
                              aria-hidden="true"
                            />
                          </div>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </div>

            <ul className="divide-y divide-outline-variant sm:hidden">
              {visible.map((p) => {
                const archived = p.status === "Archived";
                return (
                  <li key={p.id} className="flex items-center transition-colors hover:bg-surface-low">
                    <button
                      onClick={() => navigate(`/app/patients/${p.id}`)}
                      className="focus-ring flex min-w-0 flex-1 items-center gap-3 px-4 py-3.5 text-left"
                    >
                      <span
                        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${avatarClass(archived)}`}
                      >
                        {initialsOf(p.name, p.initials)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={`block truncate text-sm font-semibold ${archived ? "text-on-surface-variant" : "text-on-surface"}`}>
                          {p.name}
                        </span>
                        <span className="block truncate text-xs text-on-surface-variant">
                          {p.mrn}
                          {p.doctor && ` · ${p.doctor}`}
                          {p.preferred_language &&
                            ` · ${LANGUAGE_LABEL[p.preferred_language] || p.preferred_language}`}
                        </span>
                      </span>
                      {/* The phone row has no Status column, so "Active" in
                          plain text would be noise here; only archived shows. */}
                      {archived && <Chip tone="neutral">{p.status}</Chip>}
                    </button>
                    <button
                      onClick={() => (archived ? restore(p) : archive(p))}
                      className="focus-ring mr-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container"
                      aria-label={archived ? `Restore ${p.name}` : `Archive ${p.name}`}
                    >
                      {archived ? <RotateCcw size={16} aria-hidden="true" /> : <Archive size={16} aria-hidden="true" />}
                    </button>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </Card>

      <PatientFormModal
        open={showAdd}
        onClose={() => setShowAdd(false)}
        onSaved={() => load()}
      />
    </AppShell>
  );
}