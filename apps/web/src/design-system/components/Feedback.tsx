import clsx from "clsx";
import { AlertTriangle, Inbox, Lock } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

export type Tone = "neutral" | "accent" | "good" | "warning" | "critical";

export function Badge({ tone = "neutral", icon, children }: { tone?: Tone; icon?: ReactNode; children: ReactNode }) {
  return (
    <span className={clsx("iq-badge", tone !== "neutral" && `iq-badge--${tone}`)}>
      {icon}
      {children}
    </span>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="iq-spinner" role="status" aria-label={label} />;
}

export function Skeleton({ style, className }: { style?: CSSProperties; className?: string }) {
  return <div className={clsx("iq-skeleton", className)} style={style} aria-hidden />;
}

interface StateProps {
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  compact?: boolean;
}

export function EmptyState({ title, body, action, icon, compact }: StateProps) {
  return (
    <div className={clsx("iq-state", compact && "iq-state--compact")}>
      <div className="iq-state__icon">{icon ?? <Inbox />}</div>
      <div className="iq-state__title">{title}</div>
      {body && <div className="iq-state__body">{body}</div>}
      {action}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", body, action, compact }: Partial<StateProps>) {
  return (
    <div className={clsx("iq-state iq-state--critical", compact && "iq-state--compact")} role="alert">
      <div className="iq-state__icon">
        <AlertTriangle />
      </div>
      <div className="iq-state__title">{title}</div>
      {body && <div className="iq-state__body">{body}</div>}
      {action}
    </div>
  );
}

export function ForbiddenState({ body }: { body?: ReactNode }) {
  return (
    <EmptyState
      icon={<Lock />}
      title="You don't have access to this"
      body={body ?? "Ask a workspace administrator if you need permission."}
    />
  );
}

export function PageLoader() {
  return (
    <div className="iq-state">
      <Spinner />
    </div>
  );
}
