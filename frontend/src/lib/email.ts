/**
 * Why an address can't sign in, or null. While typing (`submitted` false) it only speaks once the text after "@"
 * can no longer become the institute domain, so "x@ni" is fine but "x@g" is flagged at once.
 */
export function domainProblem(email: string, domain: string, submitted: boolean): string | null {
  if (!domain) return null;
  const value = email.trim().toLowerCase();
  const at = value.lastIndexOf("@");
  const typed = at < 0 ? null : value.slice(at + 1);
  if (typed === domain) return null;
  if (!submitted && (typed === null || domain.startsWith(typed))) return null;
  return `Only @${domain} addresses can sign in.`;
}
