import type { ReactNode } from "react";

export function Badge({
  kind,
  children,
  title,
}: {
  kind: "corrupt" | "fake" | "dup" | "unit" | "off" | "ok";
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`badge badge-${kind}`} title={title}>
      {children}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="spinner">
      <div className="dot" />
      <span>{label ?? "Loading"}</span>
    </div>
  );
}

export function Progress({ got, total }: { got: number; total: number }) {
  // `total` is known to under-report by about 8.6%, so it is used to draw a bar
  // and never to decide when to stop. has_more does that.
  const pct = total > 0 ? Math.min(100, (got / total) * 100) : 0;
  return (
    <div className="progress">
      <div className="bar" style={{ width: `${pct}%` }} />
      <span>
        {got.toLocaleString("en-IN")} records
        {total ? ` — server claims ${total.toLocaleString("en-IN")}` : ""}
      </span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="errorbox">{message}</div>;
}

export function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: ReactNode;
  note?: string;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {note ? <div className="stat-note">{note}</div> : null}
    </div>
  );
}
