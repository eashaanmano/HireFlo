import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { useCopyLink } from "../lib/clipboard";
import { Button, Modal } from "./ui";

export interface PanelLink {
  panel_id: number;
  name: string;
  round: string;
  room?: string | null;
  url: string;
}

/** One line per panel for a single message (WhatsApp, e-mail), e.g. "Panel A (Technical interview, Seminar Hall 1): https://…". */
export function allLinksText(heading: string, links: PanelLink[]): string {
  const lines = links.map((l) => `${l.name} (${[l.round, l.room].filter(Boolean).join(", ")}): ${l.url}`);
  return [heading, ...lines].join("\n");
}

/** Panel links with Copy, QR and (optionally) Open per panel, plus Copy all. */
export function PanelLinkList({ links, heading, disabled, disabledNote, showOpen }: {
  links: PanelLink[];
  heading: string;
  disabled?: boolean;
  disabledNote?: string;
  showOpen?: boolean;
}) {
  const copy = useCopyLink();
  const [qr, setQr] = useState<PanelLink | null>(null);
  return (
    <>
      {links.length > 1 && (
        <div className="row" style={{ justifyContent: "flex-end", marginBottom: 4 }}>
          <Button size="small" disabled={disabled} title={disabled ? disabledNote : undefined}
            onClick={() => copy(allLinksText(heading, links), "All panel links")}>Copy all</Button>
        </div>
      )}
      {links.map((l) => (
        <div className="row between" key={l.panel_id} style={{ padding: "5px 0" }}>
          <span><strong>{l.name}</strong> <span className="small faint">{[l.round, l.room].filter(Boolean).join(", ")}</span></span>
          <span className="row">
            <Button size="small" disabled={disabled} title={disabled ? disabledNote : undefined} onClick={() => copy(l.url, `${l.name} link`)}>Copy</Button>
            <Button size="small" disabled={disabled} title={disabled ? disabledNote : "Show a QR code to scan"} onClick={() => setQr(l)}>QR</Button>
            {showOpen && (disabled
              ? <Button size="small" disabled title={disabledNote}>Open</Button>
              : <a className="btn small" href={l.url} target="_blank" rel="noreferrer">Open</a>)}
          </span>
        </div>
      ))}
      {qr && (
        <Modal title={`${qr.name}: scan to open`} onClose={() => setQr(null)}>
          <div className="stack" style={{ alignItems: "center" }}>
            <div className="qr-box"><QRCodeSVG value={qr.url} size={220} /></div>
            <p className="small muted" style={{ textAlign: "center" }}>
              {[qr.round, qr.room].filter(Boolean).join(", ")}. Anyone who scans this opens the panel screen for this drive.
            </p>
            <Button onClick={() => setQr(null)}>Close</Button>
          </div>
        </Modal>
      )}
    </>
  );
}
