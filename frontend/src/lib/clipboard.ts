import { useCallback } from "react";
import { useToast } from "../components/ui";

/**
 * Copy text to the clipboard. The async Clipboard API only exists on HTTPS and localhost, so on a plain
 * http:// address (a phone on the lab network) fall back to a hidden textarea and execCommand("copy").
 * Resolves to true when the text was copied.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // permission denied or not focused: try the fallback below
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

/** Copy a link with feedback; if the browser blocks copying, show the link so it can be copied by hand. */
export function useCopyLink() {
  const toast = useToast();
  return useCallback(
    (url: string, what = "Link") => {
      void copyText(url).then((ok) => {
        if (ok) toast(`${what} copied.`);
        else {
          toast("Couldn't copy automatically. Select the link and copy it.", true);
          window.prompt(`${what}: select and copy`, url);
        }
      });
    },
    [toast],
  );
}
