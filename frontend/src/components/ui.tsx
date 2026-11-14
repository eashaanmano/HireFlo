import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { ApiError } from "../api/client";

// ---------------------------------------------------------------- basics
type Variant = "primary" | "danger" | "ghost" | "default";

export function Button({
  variant = "default",
  size,
  busy,
  children,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "small" | "big"; busy?: boolean }) {
  const cls = ["btn", variant !== "default" ? variant : "", size ?? "", className].filter(Boolean).join(" ");
  return (
    <button className={cls} disabled={busy || rest.disabled} aria-busy={busy || undefined} {...rest}>
      {busy ? "Working…" : children}
    </button>
  );
}

export function Badge({ tone, children }: { tone?: "ok" | "warn" | "bad" | "info"; children: ReactNode }) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageHead({ title, lead, right }: { title: ReactNode; lead?: ReactNode; right?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {lead && <p>{lead}</p>}
      </div>
      {right}
    </header>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <div>{children}</div>}
    </div>
  );
}

export function Loading() {
  return <div className="skeleton" aria-label="Loading" />;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : "Something went wrong.";
  return <div className="callout bad">{msg}</div>;
}

export function Stat({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      {label}
      {children}
    </label>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

/**
 * A danger button that asks first. Use for actions that cannot be undone or that affect other people
 * (rotating links, rejecting candidates, withdrawing, deleting).
 */
export function ConfirmButton({
  title,
  message,
  confirmLabel,
  onConfirm,
  children,
  size,
  variant = "danger",
  busy,
  disabled,
  buttonTitle,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  children: ReactNode;
  size?: "small" | "big";
  variant?: Variant;
  busy?: boolean;
  disabled?: boolean;
  buttonTitle?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size={size} variant={variant} busy={busy} disabled={disabled} title={buttonTitle} onClick={() => setOpen(true)}>
        {children}
      </Button>
      {open && (
        <Modal title={title} onClose={() => setOpen(false)}>
          <div className="stack">
            <div>{message}</div>
            <div className="row">
              <Button onClick={() => setOpen(false)} autoFocus>Cancel</Button>
              <Button variant={variant} onClick={() => { setOpen(false); onConfirm(); }}>{confirmLabel ?? title.replace(/\?$/, "")}</Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={t.id === value}
          className={`tab ${t.id === value ? "active" : ""}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- toasts
interface Toast {
  id: number;
  text: string;
  bad?: boolean;
}

const ToastContext = createContext<(text: string, bad?: boolean) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, bad?: boolean) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, bad }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), bad ? 6000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-zone" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.bad ? "bad" : ""}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

/**
 * Mutation helper: runs `fn`, shows the success text, surfaces API errors as toasts, and refetches
 * the given queries (live updates refresh everything else).
 */
export function useAction<TArgs, TResult>(
  fn: (args: TArgs) => Promise<TResult>,
  opts: { success?: string | ((r: TResult) => string); invalidate?: QueryKey[]; onDone?: (r: TResult) => void } = {},
) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (result) => {
      if (opts.success) toast(typeof opts.success === "function" ? opts.success(result) : opts.success);
      await Promise.all((opts.invalidate ?? []).map((key) => qc.invalidateQueries({ queryKey: key })));
      opts.onDone?.(result);
    },
    onError: (e) => toast(e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong.", true),
  });
}
