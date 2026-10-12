import type { ResolvedTarget } from "../types";

interface Props {
  target: ResolvedTarget;
}

function fmt(n: number | null | undefined, digits = 2, suffix = ""): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}${suffix}`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
      <span className="shrink-0 text-slate-400">{label}</span>
      <span className="text-right font-mono text-slate-100">{children}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-slate-800 pt-3">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {children}
    </section>
  );
}

export default function TargetCard({ target }: Props) {
  const { coordinates: c, kinematics: k, morphology: m, environment: env } = target;
  const title = target.main_id?.replace(/\s+/g, " ") ?? target.ned_name ?? target.query;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <header>
        <h2 className="text-lg font-semibold text-white">{title}</h2>
        <p className="text-xs text-slate-400">
          {m.object_type_label ?? m.object_type ?? "Unclassified"}
          {m.object_type && m.object_type_label ? ` (${m.object_type})` : ""}
          {target.ned_name && target.ned_name !== title ? ` · NED: ${target.ned_name}` : ""}
        </p>
        <p className="mt-1 text-[11px] text-slate-500">
          Sources: {target.sources.join(", ") || "none"}
          {target.cached ? " · cached" : ""}
        </p>
      </header>

      <Section title="Position (ICRS / J2000)">
        <Row label="RA">{c.ra_hms} ({fmt(c.ra_deg, 5, "°")})</Row>
        <Row label="Dec">{c.dec_dms} ({fmt(c.dec_deg, 5, "°")})</Row>
        <Row label="Galactic l, b">
          {fmt(c.gal_l_deg, 3, "°")}, {fmt(c.gal_b_deg, 3, "°")}
        </Row>
        {c.source && <p className="text-[11px] text-slate-500">from {c.source}</p>}
      </Section>

      <Section title="Kinematics">
        <Row label="Redshift z">
          {k.redshift !== null ? k.redshift.toFixed(6) : "—"}
          {k.redshift_err !== null ? ` ± ${k.redshift_err.toFixed(6)}` : ""}
        </Row>
        <Row label="cz">
          {fmt(k.velocity_kms, 1)}
          {k.velocity_err_kms !== null ? ` ± ${k.velocity_err_kms.toFixed(1)}` : ""} km/s
        </Row>
        {k.source && (
          <p className="text-[11px] text-slate-500">
            from {k.source}
            {k.measurement_type ? ` (measured as ${k.measurement_type === "z" ? "redshift" : "velocity"})` : ""}
            {k.quality ? ` · SIMBAD quality ${k.quality}` : ""}
          </p>
        )}
        {k.ned_velocity_kms !== null && k.source === "SIMBAD" && (
          <p className="text-[11px] text-slate-500">
            NED: cz = {k.ned_velocity_kms.toFixed(1)} km/s (z = {k.ned_redshift?.toFixed(6)})
          </p>
        )}
      </Section>

      <Section title="Morphology">
        <Row label="Type">{m.type ?? "—"}{m.quality ? ` (qual. ${m.quality})` : ""}</Row>
        <Row label="Major × minor axis">
          {fmt(m.major_axis_arcmin, 2)} × {fmt(m.minor_axis_arcmin, 2)}′
        </Row>
        <Row label="Position angle">{fmt(m.position_angle_deg, 0, "°")}</Row>
      </Section>

      {target.distances.length > 0 && (
        <Section title="Distances">
          {target.distances.slice(0, 4).map((d, i) => (
            <Row key={i} label={d.method ?? "—"}>
              {d.value} {d.unit}
              {d.bibcode ? <span className="ml-1 text-[11px] text-slate-500">{d.bibcode}</span> : null}
            </Row>
          ))}
          {target.distances.length > 4 && (
            <p className="text-[11px] text-slate-500">+{target.distances.length - 4} more in SIMBAD</p>
          )}
        </Section>
      )}

      <Section title="Environment">
        {env.note && <p className="mb-1 text-xs text-slate-300">{env.note}</p>}
        {env.memberships.length > 0 ? (
          <ul className="space-y-1">
            {env.memberships.map((g) => (
              <li key={g.name} className="text-sm">
                <span className="font-mono text-emerald-300">{g.name}</span>
                {g.catalog && <span className="ml-1 text-xs text-slate-400">{g.catalog}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-slate-500">No cataloged group/cluster membership found.</p>
        )}
        {env.nearby_groups.length > 0 && (
          <div className="mt-2">
            <p className="text-[11px] uppercase tracking-wide text-slate-500">
              Groups / clusters within {env.search_radius_arcmin ?? 60}′
            </p>
            <ul className="space-y-0.5">
              {env.nearby_groups.map((g) => (
                <li key={g.name} className="flex justify-between gap-2 text-xs">
                  <span className="font-mono text-slate-200">
                    {g.name} <span className="text-slate-500">{g.object_type}</span>
                  </span>
                  <span className="shrink-0 text-slate-400">
                    {g.separation_arcmin !== null ? `${g.separation_arcmin.toFixed(1)}′` : ""}
                    {g.redshift !== null ? ` · z=${g.redshift.toFixed(4)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      {target.aliases.length > 1 && (
        <Section title={`Identifiers (${target.aliases.length})`}>
          <p className="max-h-24 overflow-y-auto font-mono text-[11px] leading-5 text-slate-300">
            {target.aliases.map((a) => a.replace(/\s+/g, " ")).join(" · ")}
          </p>
        </Section>
      )}

      {target.warnings.length > 0 && (
        <Section title="Warnings">
          <ul className="space-y-1 text-xs text-amber-300">
            {target.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
